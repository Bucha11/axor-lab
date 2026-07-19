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
from lab_contracts.publication import add_reproduction
from lab_runner import default_registry, replay_bundle

from .errors import NotFound, PublishRejected


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
    """File-backed store of published bundles + append-only attestations."""

    root: Path
    _cache: dict[str, StoredPublication] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in sorted(self.root.glob("*/")):
            if (directory / "publication.json").is_file():
                self._load(directory.name)

    # -- publish handshake ------------------------------------------------

    def publish(
        self,
        bundle: dict[str, object],
        traces: dict[str, dict[str, object]],
        question: str,
        license_id: str = "CC-BY-4.0",
        visibility: str = "public",
    ) -> StoredPublication:
        errors = validate_artifact(bundle, "bundle")
        if errors:
            raise PublishRejected(f"bundle failed schema validation: {errors}")
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

        publication = self._mint(bundle, traces, question, license_id, visibility)
        errors = validate_artifact(publication, "publication")
        if errors:
            raise PublishRejected(f"publication failed schema validation: {errors}")

        stored = StoredPublication(publication=publication, bundle=bundle, traces=traces)
        self._persist(stored)
        self._cache[str(publication["publication_id"])] = stored
        return stored

    def add_attestation(self, publication_id: str, attestation: dict[str, object]) -> StoredPublication:
        stored = self.get(publication_id)
        errors = validate_artifact(attestation, "attestation")
        if errors:
            raise PublishRejected(f"attestation failed schema validation: {errors}")
        if attestation.get("publication_id") != publication_id:
            raise PublishRejected("attestation.publication_id does not match the target")
        stored.reproductions = list(
            add_reproduction(tuple(stored.reproductions), attestation)
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
        return [
            s for s in self._cache.values() if s.publication.get("visibility") != "private"
        ]

    # -- internals --------------------------------------------------------

    def _mint(
        self,
        bundle: dict[str, object],
        traces: dict[str, dict[str, object]],
        question: str,
        license_id: str,
        visibility: str,
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
            kernel_version = str(denied["producer"]["kernel_version"])  # type: ignore[index]
            claims.append(
                make_claim(
                    "exactly_replayable",
                    f"On trace {denied['trace_id']}, {kernel_version} returns DENY; "
                    "the driving argument is untrusted_derived.",
                    content_hash(denied),
                    trace_refs=trace_refs,
                    aggregate_refs=aggregate_refs,
                )
            )
        for aggregate in aggregates:
            interval: dict[str, object] = aggregate["interval"]  # type: ignore[assignment]
            claims.append(
                make_claim(
                    "statistically_reproducible",
                    f"{aggregate['metric']} under {aggregate['condition_id']}: "
                    f"{float(aggregate['estimate']):.2f} "
                    f"[{float(interval['low']):.2f}, {float(interval['high']):.2f}] "
                    f"over {aggregate['n']} live trials.",
                    f"agg:{aggregate['metric']}:{aggregate['condition_id']}",
                    trace_refs=trace_refs,
                    aggregate_refs=aggregate_refs,
                )
            )
        return build_publication(
            publication_id=f"e_{bundle_ref[7:15]}",
            bundle_ref=bundle_ref,
            question=question,
            origin="local",
            integrity="hash_verified",
            claims=claims,
            license_id=license_id,
            visibility=visibility,
        )

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
        (directory / "publication.json").write_text(json.dumps(stored.publication, indent=2))
        (directory / "bundle.json").write_text(json.dumps(stored.bundle, indent=2))
        for trace in stored.traces.values():
            (directory / "traces" / f"{trace['trace_id']}.json").write_text(json.dumps(trace))
        self._persist_reproductions(stored)

    def _persist_reproductions(self, stored: StoredPublication) -> None:
        directory = self._dir(str(stored.publication["publication_id"]))
        (directory / "reproductions.json").write_text(json.dumps(stored.reproductions, indent=2))

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
        reproductions_file = directory / "reproductions.json"
        reproductions = (
            json.loads(reproductions_file.read_text()) if reproductions_file.is_file() else []
        )
        self._cache[publication_id] = StoredPublication(
            publication=publication, bundle=bundle, traces=traces, reproductions=reproductions
        )
