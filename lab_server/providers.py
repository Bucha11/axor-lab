"""Provider ports — the infrastructure contracts **Axor Lab owns** (review v0.3-3).

Axor Lab is a self-contained experiment / evidence product: it must run fully
WITHOUT Control Plane. So Lab does not depend on CP infrastructure — it defines the
ports and supplies standalone implementations; an integrated deployment may inject
CP-backed implementations of the SAME ports.

    Axor Lab (experiment / evidence domain)
              │  depends only on these ports
      ┌───────┴────────────────────────────┐
      │                                     │
 Standalone providers (this repo)     Platform providers (optional, e.g. CP)
   InMemoryRuntimeRegistry               ControlPlaneRuntimeRegistry
   LabTraceStore                         ControlPlaneTraceStore
   PackagePromotion (cp_export)          SharedRefPromotion

The domain — experiment assignment, run lifecycle, trace validation, immutable
trial attempts, bundle assembly, statistical recompute, EvidenceCase, publication —
never knows which implementation is wired. That is the quality bar for the seam:
swapping a provider changes deployment, not product behaviour.

These are structural (`typing.Protocol`) contracts: an implementation conforms by
shape, so a CP-backed provider in another package satisfies them without importing
Lab internals.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RuntimeRegistry(Protocol):
    """Owns `RuntimeRef` + credentials + status for connected runtimes. Standalone
    Lab supplies `InMemoryRuntimeRegistry`; an integrated deployment injects a
    CP-backed one so a CP user's already-connected runtime is the one Lab assigns to
    (connect once per deployment, not necessarily through CP)."""

    def connect(self, model: str = ..., agent_ref: str | None = ...) -> dict[str, object]:
        """Register a runtime → `{runtime_ref, ingest_key}`."""
        ...

    def list(self) -> list[dict[str, object]]:
        """Connected runtimes (never leaking the ingest_key)."""
        ...

    def exists(self, runtime_ref: str) -> bool:
        """Whether a `runtime_ref` is registered (Lab *selects*, never mints)."""
        ...

    def runtime_for_key(self, ingest_key: str) -> str | None:
        """Resolve an ingest_key to its runtime_ref, or None."""
        ...


@runtime_checkable
class TraceStore(Protocol):
    """Persists and serves accepted `trace/v1` bodies by content-addressed
    `trace_ref`. Standalone Lab supplies `LabTraceStore`; an integrated deployment
    can inject a CP/shared trace-fabric-backed store so Lab and CP read the SAME
    traces without copying evidence."""

    def put(self, trace_ref: str, trace: dict[str, object]) -> None:
        """Store an accepted trace under its content-addressed ref (idempotent)."""
        ...

    def get(self, trace_ref: str) -> dict[str, object] | None:
        """Fetch a stored trace, or None."""
        ...


@runtime_checkable
class TraceIngest(Protocol):
    """Accepts a runtime's pushed events / finished traces and validates them before
    they become evidence. The standalone implementation is `RuntimeJobStore` itself
    (strict schema+semantics validation, immutable attempts); a shared deployment can
    front it with the CP telemetry ingest (idempotent batches, disk-backed queue)."""

    def append_events(self, job_id: str, trial_id: str, runtime_ref: str,
                      events: list[dict[str, object]],
                      batch_id: str | None = ...) -> dict[str, object]:
        ...

    def complete_trial(self, job_id: str, trial_id: str, runtime_ref: str,
                       trace: dict[str, object] | None, status: str = ...,
                       failure: dict[str, object] | None = ...) -> dict[str, object]:
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Stores content-addressed evidence artifacts (bundles, publications) and
    resolves them by ref. Standalone Lab keeps them on its own store
    (`PublicationStore`); a shared deployment can point at a common artifact store so
    promotion is a reference, not a copy."""

    def put(self, artifact_ref: str, artifact: dict[str, object]) -> None:
        ...

    def get(self, artifact_ref: str) -> dict[str, object] | None:
        ...


@runtime_checkable
class PromotionBackend(Protocol):
    """Carries a verified run into a production configuration. Both paths are
    first-class deployment capabilities, NOT competing architectures (review
    v0.3-3): standalone Lab emits a portable verified package (`cp_export`) that CP
    imports; an integrated deployment promotes by shared artifact refs
    (`policy_ref` / `regression_refs`) with no copy."""

    def promote(self, request: dict[str, object]) -> dict[str, object]:
        """Promote a verified run; returns the promotion record / package ref."""
        ...


class LabTraceStore:
    """The standalone `TraceStore` — an in-process content-addressed map of accepted
    traces. This is Lab's own trace fabric: with it, Lab runs experiments, stores
    traces and builds EvidenceCases with NO Control Plane. An integrated deployment
    swaps it for a shared/CP-backed `TraceStore` implementing the same port."""

    def __init__(self) -> None:
        self._by_ref: dict[str, dict[str, object]] = {}

    def put(self, trace_ref: str, trace: dict[str, object]) -> None:
        self._by_ref.setdefault(trace_ref, trace)  # content-addressed → idempotent

    def get(self, trace_ref: str) -> dict[str, object] | None:
        return self._by_ref.get(trace_ref)
