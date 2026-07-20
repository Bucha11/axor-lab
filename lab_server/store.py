"""The publish handshake and persistence (runner-protocol.md, threat-model §4–5).

What the server trusts: nothing executable. On upload it (1) schema-validates
the bundle and publication, (2) verifies content hashes, (3) re-runs *replay*
— deterministic, no model calls, no tools — to confirm the published verdicts
match the traces, then mints an immutable publication with `origin=local`
(a runner upload never claims `lab_infra`). Reproductions accrue in a
separate append-only attestation log, so the publication body stays immutable.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from lab_contracts import (
    BundleIntegrityError,
    build_publication,
    content_hash,
    evidence_lineage_ref,
    make_claim,
    provenance_axes,
    validate_artifact,
    verify_bundle,
)
from lab_contracts.publication import add_reproduction, rebuild_reproduction_log
from lab_runner import default_registry, replay_bundle

from .errors import NotFound, PublishRejected
from .recompute import check_aggregates

_ATTESTATION_ID_MAX = 128


# the DENY claim text is rendered by ONE shared function so the CLI local
# publish and this server path produce identical assertions (review r6)
from lab_runner.claims import deny_claim_text as _deny_claim_text


def _semantic_errors(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> list[str]:
    """The semantic checks a publication must clear: exact replay of every
    recorded verdict, and statistical aggregates that follow from the traces.

    Hash verification proves a bundle is internally consistent; it does NOT
    prove the verdicts replay or the numbers were measured. Both the publish
    handshake AND the load path run this, so a hand-assembled but hash-coherent
    publication placed in the store directory (fabricated aggregates, arbitrary
    claims, non-replaying decisions) is refused on restart, not trusted into the
    catalog (review r8)."""
    errors: list[str] = []
    versions = tuple(str(c["kernel"]) for c in bundle["conditions"])  # type: ignore[union-attr]
    kernels = {k.version: k for k in default_registry(versions).kernels}
    report = replay_bundle(bundle, traces, kernels)
    if not report.bit_identical:
        errors.append("server replay does not match recorded verdicts")
    mismatches = check_aggregates(bundle, traces)
    if mismatches:
        errors.append(
            "uploaded aggregates do not match server recomputation: "
            + "; ".join(mismatches[:5])
        )
    return errors


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash never leaves partial JSON
    (review §7.2). The temp file lives in the same directory for an atomic
    same-filesystem rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


@dataclass
class StoredPublication:
    publication: dict[str, object]
    bundle: dict[str, object]
    traces: dict[str, dict[str, object]]
    reproductions: list[dict[str, object]] = field(default_factory=list)
    # the detached author signature that EARNED integrity=signed, persisted as a
    # receipt so the badge can be RE-VERIFIED on load — never trusted from the
    # publication.json integrity field alone (review r9)
    author: str | None = None
    signature: str | None = None
    # the STABLE evidence lineage this publication rests on (review r15) — the
    # takedown/read guards key on this, not on the packaging-sensitive bundle_ref
    lineage_ref: str = ""

    def __post_init__(self) -> None:
        if not self.lineage_ref:
            self.lineage_ref = evidence_lineage_ref(self.bundle)

    def axes(self) -> dict[str, object]:
        return provenance_axes(self.publication, tuple(self.reproductions))

    def receipt(self) -> dict[str, object]:
        """A PORTABLE verification receipt served alongside the bundle download,
        so a reader can verify integrity offline without trusting the server
        (review r14). It carries the author/key_id/signature ONLY when the
        publication earned integrity=signed; otherwise just the content-addressed
        signed_ref for a hash-only check."""
        from lab_contracts.signing import build_receipt

        integrity = str(self.publication.get("integrity", "hash_verified"))
        return build_receipt(
            self.bundle, integrity=integrity, author=self.author,
            key_id=self.author, signature=self.signature,
        )


@dataclass
class PublicationStore:
    """File-backed store of published bundles + append-only attestations.

    Optional `known_keys` maps author id → Ed25519 public key (hex); a bundle
    that arrives with a `signature` verifying against one upgrades the
    publication's integrity axis to `signed` (never changes `origin`).
    """

    root: Path
    known_keys: dict[str, str] = field(default_factory=dict)
    _cache: dict[str, StoredPublication] = field(default_factory=dict)
    _tombstones: set[str] = field(default_factory=set)
    # STABLE evidence lineage refs that were taken down — a takedown removes the
    # EVIDENCE, so the same experiment cannot be re-published under altered
    # metadata OR repackaged bytes (review r14/r15). Keyed by evidence_lineage_ref,
    # which is invariant to bundle_id/created/packaging — the packaging-sensitive
    # bundle_ref was escapable by re-serialising the same evidence.
    _lineage_tombstones: set[str] = field(default_factory=set)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # TWO-PASS cold load (review r15): collect EVERY tombstone (id + evidence
        # lineage) BEFORE loading any publication, so a sibling publication of a
        # taken-down evidence can never be admitted just because its directory
        # sorts ahead of the tombstone's. A one-pass sorted walk could load the
        # sibling first and then never remove it.
        directories = sorted(self.root.glob("*/"))
        for directory in directories:
            try:
                if (directory / "tombstone.json").is_file():
                    self._tombstones.add(directory.name)
                    tomb = json.loads((directory / "tombstone.json").read_text())
                    if isinstance(tomb, dict):
                        # prefer the stable lineage ref; fall back to a legacy
                        # bundle_ref-only tombstone (round-14 records)
                        lineage = tomb.get("evidence_lineage_ref") or tomb.get("bundle_ref")
                        if lineage:
                            self._lineage_tombstones.add(str(lineage))
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
        for directory in directories:
            # ISOLATE each directory: one corrupt file (truncated JSON, a
            # non-object array element, a missing key) must skip that ONE
            # publication, never crash the whole catalog on startup (review r10).
            try:
                if (directory / "tombstone.json").is_file():
                    self._load_reproductions_only(directory.name)
                elif (directory / "publication.json").is_file():
                    self._load(directory.name)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                # ValueError covers json.JSONDecodeError; the record is quarantined
                # (left on disk, not loaded) rather than trusted or fatal
                continue

    # -- publish handshake ------------------------------------------------

    def publish(
        self,
        bundle: dict[str, object],
        traces: dict[str, dict[str, object]],
        question: str,
        license_id: str = "CC-BY-4.0",
        visibility: str = "unlisted",  # safe default: NOT public unless asked (review r4)
        signature: str | None = None,
        author: str | None = None,
    ) -> StoredPublication:
        errors = validate_artifact(bundle, "bundle")
        if errors:
            raise PublishRejected(f"bundle failed schema validation: {errors}")
        # raw traces live OUTSIDE the bundle schema — validate each one here too,
        # matching the local read_bundle_dir pipeline, so a malformed/schema-
        # invalid trace is a clean 4xx, not a deeper TypeError (review r7)
        for trace in traces.values():
            terrors = validate_artifact(trace, "trace")
            if terrors:
                raise PublishRejected(
                    f"trace {trace.get('trace_id')} failed schema validation: {terrors[:5]}"
                )
        # normalize to trace_id keys (runners key by content hash; pages address by id)
        traces = {str(t["trace_id"]): t for t in traces.values()}
        try:
            verify_bundle(bundle, traces)
        except BundleIntegrityError as exc:
            raise PublishRejected(f"content hashes do not verify: {exc}") from exc

        # replay + statistical recomputation — the semantic checks a publication
        # must pass; shared with the load path so a restart can't trust a record
        # that never cleared them (review r8)
        semantic = _semantic_errors(bundle, traces)
        if semantic:
            raise PublishRejected("; ".join(semantic[:5]))

        integrity = self._integrity(bundle, signature, author)
        publication = self._mint(bundle, traces, question, license_id, visibility, integrity)
        errors = validate_artifact(publication, "publication")
        if errors:
            raise PublishRejected(f"publication failed schema validation: {errors}")

        # keep the signature+author ONLY when they earned integrity=signed, so a
        # reload can re-verify the badge rather than trust the persisted field
        signed = integrity == "signed"
        stored = StoredPublication(
            publication=publication, bundle=bundle, traces=traces,
            author=author if signed else None, signature=signature if signed else None,
        )
        pid = str(publication["publication_id"])
        with self._lock:
            # a tombstoned id must NOT be resurrected by re-publishing the same
            # body: takedown is an admin action, and because the id
            # content-addresses the body, ANY writer who has the bytes could
            # re-derive the same id and put the taken-down record back in the
            # catalog until the next restart (when the tombstone wins again).
            # Refuse it; restoring a taken-down publication is a separate
            # admin-only operation, not a side effect of a write-token publish
            # (review r13).
            if pid in self._tombstones:
                raise PublishRejected(
                    f"publication {pid} was taken down and cannot be re-published; "
                    "restoring a taken-down record is an admin-only operation",
                    status=409,
                )
            # evidence-level takedown: the SAME evidence cannot be re-published
            # under altered metadata OR repackaged bytes. takedown follows the
            # STABLE evidence lineage (invariant to bundle_id/created/packaging),
            # so "takedown is final" means the evidence is gone — not just one
            # wording of it, and not just one packaging of it (review r14/r15).
            if stored.lineage_ref in self._lineage_tombstones:
                raise PublishRejected(
                    f"the evidence (lineage {stored.lineage_ref}) was taken down and cannot "
                    "be re-published under any metadata or packaging; restoring taken-down "
                    "evidence is an admin-only operation",
                    status=409,
                )
            existing = self._cache.get(pid)
            if existing is not None:
                # a publication is immutable: re-publishing the SAME bundle with
                # IDENTICAL metadata is idempotent (return the existing record);
                # re-publishing with DIFFERENT question/visibility/license/claims
                # must not silently overwrite the public record (review r7)
                if existing.publication == publication:
                    return existing
                raise PublishRejected(
                    f"publication {pid} already exists and is immutable; a re-publish with "
                    "different metadata (question/visibility/license) is refused"
                )
            self._persist(stored)
            self._cache[pid] = stored
        return stored

    def _integrity(
        self, bundle: dict[str, object], signature: str | None, author: str | None
    ) -> str:
        """hash_verified by default; signed if a KNOWN author key verifies the
        detached signature over content_hashes. Never claims signed on an
        unknown key — the catalog must not equate that with a verified one."""
        if not signature or not author:
            return "hash_verified"
        pubkey = self.known_keys.get(author)
        if pubkey is None:
            raise PublishRejected(f"unknown author key {author!r}; cannot claim 'signed'")
        from lab_contracts.signing import (
            SignatureInvalid,
            SignatureUnavailable,
            verify_bundle_signature,
        )

        try:
            verify_bundle_signature(bundle, signature, pubkey)
        except SignatureInvalid as exc:
            raise PublishRejected(str(exc)) from exc
        except SignatureUnavailable as exc:
            # crypto not installed on the server: a clean rejection, not a 500
            raise PublishRejected(f"cannot verify signature: {exc}") from exc
        return "signed"

    def _integrity_on_load(
        self, bundle: dict[str, object], author: str | None, signature: str | None
    ) -> str:
        """Recompute the integrity badge on load. Unlike publish (which REJECTS
        an unverifiable signature), load degrades gracefully to hash_verified —
        a signed badge is granted only if the receipt's author signature still
        verifies against a known key on this server (review r9)."""
        if not author or not signature:
            return "hash_verified"
        try:
            return self._integrity(bundle, signature, author)
        except PublishRejected:
            # unknown key on this server / bad signature / crypto absent → the
            # signed badge cannot be re-earned here; fall back to hash_verified
            return "hash_verified"

    def takedown(self, publication_id: str) -> None:
        """Remove a publication AND every sibling that rests on the SAME evidence
        lineage, while PRESERVING each one's append-only attestation record (plan
        B4 DoD, threat-model §4). The bundle/traces/publication body are removed;
        reproductions.json stays.

        takedown follows the STABLE evidence lineage (review r14/r15): the same
        experiment published twice under different questions/visibility yields two
        publication ids but ONE lineage — taking one down retires BOTH, and the
        lineage tombstone blocks any future re-publish under altered metadata or
        repackaged bytes. Removing only the exact id left the sibling public."""
        with self._lock:
            stored = self._cache.get(publication_id)
            if stored is None and publication_id not in self._tombstones:
                raise NotFound(f"publication {publication_id} not found")
            lineage = stored.lineage_ref if stored is not None else ""
            # every currently-cached publication on this lineage (the target plus
            # any sibling) is retired together — otherwise a sibling published
            # BEFORE the takedown stays served (review r15)
            targets = [publication_id]
            if lineage:
                targets = sorted(
                    {publication_id}
                    | {pid for pid, s in self._cache.items() if s.lineage_ref == lineage}
                )
            for pid in targets:
                self._cache.pop(pid, None)
                directory = self._dir(pid)
                for name in ("publication.json", "bundle.json"):
                    (directory / name).unlink(missing_ok=True)
                traces_dir = directory / "traces"
                if traces_dir.is_dir():
                    for path in traces_dir.glob("*.json"):
                        path.unlink()
                _write_atomic(
                    directory / "tombstone.json",
                    json.dumps({
                        "publication_id": pid, "status": "taken_down",
                        "evidence_lineage_ref": lineage,
                    }),
                )
                self._tombstones.add(pid)
            if lineage:
                self._lineage_tombstones.add(lineage)

    def add_attestation(self, publication_id: str, attestation: dict[str, object]) -> StoredPublication:
        errors = validate_artifact(attestation, "attestation")
        if errors:
            raise PublishRejected(f"attestation failed schema validation: {errors}")
        if attestation.get("publication_id") != publication_id:
            raise PublishRejected("attestation.publication_id does not match the target")
        with self._lock:  # serialize the read-modify-write so concurrent attestations don't race
            stored = self.get(publication_id)
            stored.reproductions = list(
                add_reproduction(tuple(stored.reproductions), attestation, self.known_keys)
            )
            self._persist_reproductions(stored)
            return stored

    # -- reads ------------------------------------------------------------

    def get(self, publication_id: str) -> StoredPublication:
        stored = self._cache.get(publication_id)
        # defense in depth (review r15): never serve a publication whose id OR
        # whose evidence lineage was taken down, even if one somehow re-entered
        # the cache. Reads guard on the lineage tombstone, not only the id.
        if (
            stored is None
            or publication_id in self._tombstones
            or stored.lineage_ref in self._lineage_tombstones
        ):
            raise NotFound(f"publication {publication_id} not found")
        return stored

    def catalog(self) -> list[StoredPublication]:
        # ONLY public: unlisted is capability-URL-reachable but never listed,
        # private is never served (review §7 / P0.5); a tombstoned id OR a
        # tombstoned evidence lineage is never listed even if one ever re-entered
        # the cache (defense in depth, r13/r15)
        return [
            s for s in self._cache.values()
            if s.publication.get("visibility") == "public"
            and str(s.publication.get("publication_id")) not in self._tombstones
            and s.lineage_ref not in self._lineage_tombstones
        ]

    @staticmethod
    def _derive_id(publication: dict[str, object]) -> str:
        """Content-address the WHOLE publication body — the ONE definition shared
        with the local CLI publish (lab_contracts.derive_publication_id) so both
        mint identical ids (review §6.3, r7, r12)."""
        from lab_contracts import derive_publication_id

        return derive_publication_id(publication)

    def is_taken_down(self, publication_id: str) -> bool:
        return publication_id in self._tombstones

    def reproductions_of(self, publication_id: str) -> list[dict[str, object]]:
        """The append-only attestation record — survives a takedown."""
        stored = self._cache.get(publication_id)
        if stored is not None:
            return stored.reproductions
        directory = self._dir(publication_id)
        reproductions_file = directory / "reproductions.json"
        if reproductions_file.is_file():
            # re-derive a TRUSTED log from the on-disk bytes: re-check kind,
            # re-dedup, re-verify signatures, and bind to THIS publication_id
            # (review r8/r9) rather than trust the file. A corrupt/non-list file
            # yields an empty log, never an exception (review r10).
            try:
                parsed = json.loads(reproductions_file.read_text())
                raw = tuple(parsed) if isinstance(parsed, list) else ()
            except (OSError, ValueError):
                raw = ()
            return list(rebuild_reproduction_log(raw, self.known_keys, publication_id))
        raise NotFound(f"publication {publication_id} not found")

    # -- internals --------------------------------------------------------

    def _mint(
        self,
        bundle: dict[str, object],
        traces: dict[str, dict[str, object]],
        question: str,
        license_id: str,
        visibility: str,
        integrity: str = "hash_verified",
    ) -> dict[str, object]:
        bundle_ref = content_hash(bundle)
        trace_refs = frozenset(content_hash(t) for t in traces.values())
        aggregates: list[dict[str, object]] = bundle["aggregates"]  # type: ignore[assignment]
        aggregate_refs = frozenset(
            f"agg:{a['metric']}:{a['condition_id']}" for a in aggregates
        )
        claims: list[dict[str, object]] = []
        denied = self._first_denied(traces)
        if denied is not None:
            claims.append(
                make_claim(
                    "exactly_replayable",
                    _deny_claim_text(denied),
                    content_hash(denied),
                    trace_refs=trace_refs,
                    aggregate_refs=aggregate_refs,
                )
            )
        # honest agent wording: the default runner drives a deterministic
        # scripted agent, so the claim says "trials", not "live trials"
        provider = str(
            bundle.get("environment", {}).get("model", {}).get("provider", "")  # type: ignore[union-attr]
        )
        agent_note = " (scripted agent)" if provider in ("", "scripted") else ""
        for aggregate in aggregates:
            interval: dict[str, object] = aggregate["interval"]  # type: ignore[assignment]
            # an independent-samples comparison is exploratory — never present it
            # as a paired significance result (review r4)
            design = str(aggregate.get("comparison_design", "matched_pairs"))
            if design == "independent_samples":
                design_note = (
                    " independent-samples comparison (exploratory; not a paired significance test)"
                )
            elif design == "matched_pairs":
                # the server recomputes the McNemar ARITHMETIC over the stored
                # pairing, but it cannot PROVE the observations are truly paired —
                # that rests on environment.model.provider, an uploader-controlled
                # string. Say so, so a paired p-value is never read as attested
                # (review r14).
                design_note = (
                    " matched-pairs design is UPLOADER-DECLARED, not attested: the recompute "
                    "verifies the arithmetic over the stored pairing, not that the observations "
                    "are genuinely paired (no signed execution receipt)"
                )
            else:
                design_note = ""
            claims.append(
                make_claim(
                    "statistically_reproducible",
                    f"{aggregate['metric']} under {aggregate['condition_id']}: "
                    f"{float(aggregate['estimate']):.2f} "
                    f"[{float(interval['low']):.2f}, {float(interval['high']):.2f}] "
                    f"over {aggregate['n']} trials{agent_note}, "
                    f"server-recomputed from the traces.{design_note}",
                    f"agg:{aggregate['metric']}:{aggregate['condition_id']}",
                    trace_refs=trace_refs,
                    aggregate_refs=aggregate_refs,
                )
            )
        # the server only reaches here after check_aggregates matched, so every
        # statistical claim is backed by a server recomputation, not the upload
        statistics_integrity = "recomputed_from_traces" if aggregates else None
        # build the body with a placeholder id, then content-address it: the id
        # commits to every field, so the publication is immutable by construction
        publication = build_publication(
            publication_id="e_pending",
            bundle_ref=bundle_ref,
            question=question,
            origin="local",
            integrity=integrity,
            claims=claims,
            license_id=license_id,
            visibility=visibility,
            statistics_integrity=statistics_integrity,
        )
        pid = self._derive_id(publication)
        publication["publication_id"] = pid
        publication["reproductions_ref"] = f"attlog:{pid}"
        return publication

    # (module-level helper below is used here)

    @staticmethod
    def _first_denied(traces: dict[str, dict[str, object]]) -> dict[str, object] | None:
        for trace in sorted(traces.values(), key=lambda t: str(t["trace_id"])):
            for event in trace["events"]:  # type: ignore[union-attr]
                if (
                    event.get("type") == "gate_decision"
                    and event["decision"]["verdict"] == "DENY"  # type: ignore[index]
                ):
                    return trace
        return None

    def _dir(self, publication_id: str) -> Path:
        return self.root / publication_id

    def _persist(self, stored: StoredPublication) -> None:
        directory = self._dir(str(stored.publication["publication_id"]))
        (directory / "traces").mkdir(parents=True, exist_ok=True)
        _write_atomic(directory / "publication.json", json.dumps(stored.publication, indent=2))
        _write_atomic(directory / "bundle.json", json.dumps(stored.bundle, indent=2))
        for trace in stored.traces.values():
            # filename is a server-computed content hash, NEVER the caller-supplied
            # trace_id — a hostile trace_id like '../../x' cannot escape traces/
            name = content_hash(trace).removeprefix("sha256:")
            _write_atomic(directory / "traces" / f"{name}.json", json.dumps(trace))
        # the signing receipt: present ONLY for a signed publication, so load can
        # re-verify the author signature instead of trusting integrity from disk
        if stored.author and stored.signature:
            _write_atomic(
                directory / "receipt.json",
                json.dumps({"author": stored.author, "signature": stored.signature}),
            )
        self._persist_reproductions(stored)

    def _persist_reproductions(self, stored: StoredPublication) -> None:
        directory = self._dir(str(stored.publication["publication_id"]))
        _write_atomic(directory / "reproductions.json", json.dumps(stored.reproductions, indent=2))

    def _load(self, publication_id: str) -> None:
        directory = self._dir(publication_id)
        publication = json.loads((directory / "publication.json").read_text())
        bundle = json.loads((directory / "bundle.json").read_text())
        # never resurrect a publication whose evidence lineage was taken down —
        # e.g. a sibling whose files survived a takedown that crashed mid-sweep
        # (the two-pass load has already collected every lineage tombstone, r15)
        if evidence_lineage_ref(bundle) in self._lineage_tombstones:
            return
        traces: dict[str, dict[str, object]] = {}
        traces_dir = directory / "traces"
        if traces_dir.is_dir():
            for path in sorted(traces_dir.glob("*.json")):
                trace = json.loads(path.read_text())
                traces[str(trace["trace_id"])] = trace
        # re-verify integrity on load: a locally tampered file must not become
        # trusted catalog state (review §7.3)
        try:
            verify_bundle(bundle, traces)
        except BundleIntegrityError:
            return  # skip a tampered/corrupt publication rather than trust it
        # ALSO re-verify the PUBLICATION body, not just the evidence (review r7):
        # schema-valid, its bundle_ref binds the bundle actually present, and its
        # id content-addresses the whole body. The id commits to EVERY field, so
        # a hand-edited publication.json — visibility flipped to public, integrity
        # to signed, question or claims rewritten — WITHOUT renaming the directory
        # no longer matches its own id and is dropped rather than trusted.
        if validate_artifact(publication, "publication"):
            return
        if str(publication.get("publication_id")) != publication_id:
            return
        if str(publication.get("bundle_ref")) != content_hash(bundle):
            return
        if publication_id != self._derive_id(publication):
            return
        # run the SAME semantic handshake publish ran — replay + aggregate
        # recomputation — so a from-scratch, hash-coherent publication dropped
        # into the store dir cannot be trusted just because its id is derived
        # correctly (review r8). The content-address alone only catches EDITS.
        if _semantic_errors(bundle, traces):
            return
        # RE-VERIFY the integrity badge from the persisted signing receipt rather
        # than trust the disk field: integrity=signed is re-earned only if the
        # detached author signature verifies against a known key on THIS load;
        # absent/unverifiable → hash_verified. A from-scratch forgery that writes
        # integrity:signed with no valid receipt recomputes to hash_verified, so
        # the re-mint below no longer matches the stored body and is dropped (r9).
        receipt_file = directory / "receipt.json"
        author = signature = None
        if receipt_file.is_file():
            # a corrupt receipt → treat as no receipt (integrity degrades to
            # hash_verified), never crash the load (review r10)
            try:
                receipt = json.loads(receipt_file.read_text())
                if isinstance(receipt, dict):
                    author, signature = receipt.get("author"), receipt.get("signature")
            except ValueError:
                author = signature = None
        integrity = self._integrity_on_load(bundle, author, signature)
        # and re-MINT the publication from the evidence: the claims, aggregate
        # refs, statistics_integrity AND integrity must be exactly what the server
        # would generate, so a fabricated claim text or a forged signed badge is
        # refused even when it is internally hash-consistent.
        expected = self._mint(
            bundle, traces, str(publication.get("question", "")),
            str(publication.get("license", "CC-BY-4.0")),
            str(publication.get("visibility", "unlisted")),
            integrity=integrity,
        )
        if expected != publication:
            return
        reproductions_file = directory / "reproductions.json"
        # re-derive a trusted log from disk: re-check kind, re-dedup, re-verify
        # signatures, bind to THIS publication — never trust a persisted `verified`
        # flag or an attestation transplanted from another publication (review
        # r8/r9). A corrupt/non-list reproductions file degrades to an empty log
        # so it doesn't drop the whole (otherwise valid) publication (review r10).
        raw: tuple[object, ...] = ()
        if reproductions_file.is_file():
            try:
                parsed = json.loads(reproductions_file.read_text())
                raw = tuple(parsed) if isinstance(parsed, list) else ()
            except ValueError:
                raw = ()
        reproductions = list(rebuild_reproduction_log(raw, self.known_keys, publication_id))
        self._cache[publication_id] = StoredPublication(
            publication=publication, bundle=bundle, traces=traces, reproductions=reproductions,
            author=author if integrity == "signed" else None,
            signature=signature if integrity == "signed" else None,
        )

    def _load_reproductions_only(self, publication_id: str) -> None:
        """A taken-down publication keeps only its attestation record."""
        directory = self._dir(publication_id)
        # nothing to load into the catalog cache; reproductions_of reads lazily
        _ = directory
