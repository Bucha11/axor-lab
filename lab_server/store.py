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

    def axes(self) -> dict[str, object]:
        return provenance_axes(self.publication, tuple(self.reproductions))


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
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in sorted(self.root.glob("*/")):
            if (directory / "tombstone.json").is_file():
                self._tombstones.add(directory.name)
                self._load_reproductions_only(directory.name)
            elif (directory / "publication.json").is_file():
                self._load(directory.name)

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

        versions = tuple(str(c["kernel"]) for c in bundle["conditions"])  # type: ignore[union-attr]
        kernels = {k.version: k for k in default_registry(versions).kernels}
        report = replay_bundle(bundle, traces, kernels)
        if not report.bit_identical:
            raise PublishRejected(
                "server replay does not match recorded verdicts — refusing to publish"
            )

        # recompute every statistical aggregate from the evidence and reject a
        # bundle whose uploaded numbers do not follow from its own traces — hash
        # verification proves internal consistency, NOT that the estimates and n
        # were actually measured (review r2 Patch 4)
        mismatches = check_aggregates(bundle, traces)
        if mismatches:
            raise PublishRejected(
                "uploaded aggregates do not match server recomputation: "
                + "; ".join(mismatches[:5])
            )

        integrity = self._integrity(bundle, signature, author)
        publication = self._mint(bundle, traces, question, license_id, visibility, integrity)
        errors = validate_artifact(publication, "publication")
        if errors:
            raise PublishRejected(f"publication failed schema validation: {errors}")

        stored = StoredPublication(publication=publication, bundle=bundle, traces=traces)
        pid = str(publication["publication_id"])
        with self._lock:
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

    def takedown(self, publication_id: str) -> None:
        """Remove a publication from the catalog while PRESERVING its
        append-only attestation record (plan B4 DoD, threat-model §4). The
        bundle/traces/publication body are removed; reproductions.json stays."""
        with self._lock:
            stored = self._cache.pop(publication_id, None)
            if stored is None and publication_id not in self._tombstones:
                raise NotFound(f"publication {publication_id} not found")
            directory = self._dir(publication_id)
            for name in ("publication.json", "bundle.json"):
                (directory / name).unlink(missing_ok=True)
            traces_dir = directory / "traces"
            if traces_dir.is_dir():
                for path in traces_dir.glob("*.json"):
                    path.unlink()
            _write_atomic(
                directory / "tombstone.json",
                json.dumps({"publication_id": publication_id, "status": "taken_down"}),
            )
            self._tombstones.add(publication_id)

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
        if stored is None:
            raise NotFound(f"publication {publication_id} not found")
        return stored

    def catalog(self) -> list[StoredPublication]:
        # ONLY public: unlisted is capability-URL-reachable but never listed,
        # private is never served (review §7 / P0.5)
        return [s for s in self._cache.values() if s.publication.get("visibility") == "public"]

    @staticmethod
    def _derive_id(publication: dict[str, object]) -> str:
        """A 128-bit id (32 hex chars) that content-addresses the WHOLE
        publication body — every field except the two that are themselves
        derived from the id (review §6.3 — the old 32-bit id was collision-
        searchable; and r7 — the id must COMMIT to the body, so no hand-edit
        of visibility/question/claims/integrity/license survives a reload).
        Byte-identical publications get the same id (idempotent re-publish);
        any changed field yields a different id, i.e. a different publication."""
        body = {
            k: v for k, v in publication.items()
            if k not in ("publication_id", "reproductions_ref")
        }
        return f"e_{content_hash(body).removeprefix('sha256:')[:32]}"

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
            # re-dedup, re-verify signatures (review r8) rather than trust the file
            raw = tuple(json.loads(reproductions_file.read_text()))
            return list(rebuild_reproduction_log(raw, self.known_keys))
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
            design_note = (
                " independent-samples comparison (exploratory; not a paired significance test)"
                if design == "independent_samples" else ""
            )
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
        self._persist_reproductions(stored)

    def _persist_reproductions(self, stored: StoredPublication) -> None:
        directory = self._dir(str(stored.publication["publication_id"]))
        _write_atomic(directory / "reproductions.json", json.dumps(stored.reproductions, indent=2))

    def _load(self, publication_id: str) -> None:
        directory = self._dir(publication_id)
        publication = json.loads((directory / "publication.json").read_text())
        bundle = json.loads((directory / "bundle.json").read_text())
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
        reproductions_file = directory / "reproductions.json"
        # re-derive a trusted log from disk: re-check kind, re-dedup, re-verify
        # signatures — never trust a persisted `verified` flag (review r8)
        raw = tuple(json.loads(reproductions_file.read_text())) if reproductions_file.is_file() else ()
        reproductions = list(rebuild_reproduction_log(raw, self.known_keys))
        self._cache[publication_id] = StoredPublication(
            publication=publication, bundle=bundle, traces=traces, reproductions=reproductions
        )

    def _load_reproductions_only(self, publication_id: str) -> None:
        """A taken-down publication keeps only its attestation record."""
        directory = self._dir(publication_id)
        # nothing to load into the catalog cache; reproductions_of reads lazily
        _ = directory
