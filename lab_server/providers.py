"""Provider ports — the infrastructure contracts **Axor Lab owns** (agent-connection.md).

**Control Plane and Axor Lab are two separate products** — separate backends, URLs,
APIs, credentials, and STORES. What is shared is only the local axor-core + adapter
and the trace *schema*, never a backend. So Lab owns its runtime registry, trace
store, job queue, and artifact store outright; there is **no shared "trace fabric"
backend** and no CP-backed `TraceStore`/`RuntimeRegistry` — each product ingests and
stores its own traces (architecture-boundary.md).

    Axor Lab (experiment / evidence domain)
              │  depends only on these ports — Lab's OWN
      ┌───────┴──────────────────────────────────┐
   Lab-owned data ports                    CP-integration ports (server-side, OPTIONAL)
   RuntimeRegistry  (issues axlab_ tokens)  ControlPlaneRuntimeProvider  (import CP runtime refs)
   TraceStore       (Lab's own store)       ControlPlanePromotionBackend (promote Lab→CP)
   TraceIngest / ArtifactStore              ControlPlaneIdentityProvider (optional org SSO)

The data ports below are ALWAYS backed by Lab's own implementations — swappable only
for *another Lab-owned* backend (in-memory ↔ durable), never for a CP store. The
CP-integration ports are a different thing: optional, server-side links an
*integrated* deployment may wire so Lab can import a runtime reference from CP or
promote an artifact into CP — but URLs, jobs, Results and stores stay separate, and
standalone Lab runs with none of them (agent-connection.md "Integrated deployment").

These are structural (`typing.Protocol`) contracts.

Status (honest): `RuntimeRegistry` and `TraceStore` are WIRED into `RuntimeJobStore`
and proved swappable by tests. `TraceIngest` / `ArtifactStore` and the three
`ControlPlane*` integration ports are DECLARED contracts naming the target shape; a
thin conforming adapter (and the integrated CP link) is still pending.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable


@runtime_checkable
class RuntimeRegistry(Protocol):
    """**Lab's own** runtime registry: owns `RuntimeRef` + credentials + status and
    issues the `axlab_` runtime token at connect. It is NOT shared with Control
    Plane and is never CP-backed — a runtime registers with Lab separately, using
    Lab's protocol and token (agent-connection.md). An integrated deployment may
    *import* a CP runtime reference via `ControlPlaneRuntimeProvider`, but Lab still
    mints its own `axlab_` credential and owns its jobs. Swappable only for another
    Lab-owned backend (e.g. in-memory ↔ durable)."""

    def connect(self, model: str = ..., agent_ref: str | None = ...) -> dict[str, object]:
        """Register a runtime → `{runtime_ref, ingest_key}` (the `axlab_` token)."""
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
    """**Lab's own** content-addressed store of accepted `trace/v1` bodies. The store
    OWNS the addressing: `put` canonicalizes + hashes the trace itself and returns
    the ref. There is no shared/CP trace store — only the trace *schema* is common
    (architecture-boundary.md); Lab ingests into its OWN store. Swappable only for
    another Lab-owned backend (in-memory ↔ durable)."""

    def put(self, trace: dict[str, object]) -> str:
        """Store a trace and return its content-addressed ref. Storing the same
        bytes again is idempotent; a different body under an existing ref is an
        integrity error."""
        ...

    def get(self, trace_ref: str) -> dict[str, object] | None:
        """Fetch a stored trace (a fresh copy), or None."""
        ...


@runtime_checkable
class TraceIngest(Protocol):
    """Accepts a runtime's pushed events / finished traces and validates them before
    they become evidence — Lab's own ingest (strict schema+semantics validation,
    immutable attempts). The standalone implementation is `RuntimeJobStore` itself."""

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
    """**Lab's own** store of content-addressed evidence artifacts (bundles,
    publications). Standalone Lab keeps them on `PublicationStore`. Promotion into CP
    is a separate, explicit act (`ControlPlanePromotionBackend`) — not a shared store."""

    def put(self, artifact_ref: str, artifact: dict[str, object]) -> None:
        ...

    def get(self, artifact_ref: str) -> dict[str, object] | None:
        ...


# -- Control Plane integration ports (server-side, OPTIONAL) --------------------
# These are the ONLY CP-facing seams (agent-connection.md "Integrated deployment").
# They never merge stores: Lab jobs/Results stay in Lab, CP desired state in CP.
# Standalone Lab wires NONE of them and runs fully without Control Plane.

@runtime_checkable
class ControlPlaneRuntimeProvider(Protocol):
    """*Optional, integrated only.* Lets Lab SHOW runtimes already known to Control
    Plane and map ids (`{lab_runtime_id, external_refs:{control_plane_runtime_id}}`).
    Lab still issues its own `axlab_` credential and owns its jobs — this is an import
    of a reference, not a shared registry."""

    def list_runtimes(self) -> list[dict[str, object]]:
        """CP-known runtime references Lab may offer to import (read-only)."""
        ...


@runtime_checkable
class ControlPlanePromotionBackend(Protocol):
    """*Optional, integrated only.* Promote a verified Lab artifact/config INTO
    Control Plane. The standalone path is instead the portable `cp_export` package CP
    imports; this port is the server-to-server variant. Not a shared store."""

    def promote(self, request: dict[str, object]) -> dict[str, object]:
        """Promote a verified run into CP; returns the CP-side promotion record."""
        ...


@runtime_checkable
class ControlPlaneIdentityProvider(Protocol):
    """*Optional, integrated only.* Use CP's org SSO as the shared identity layer so
    one org login spans both products (token-exchange still mints each product's own
    scoped token, gated by `entitled_products`). Standalone Lab uses its own identity
    instead and never calls CP for auth (agent-connection.md)."""

    def verify_session(self, session_token: str) -> dict[str, object]:
        """Verify an account session → `{account_id, entitled_products}`."""
        ...


class TraceStoreIntegrityError(Exception):
    """Two different trace bodies collided on one content-addressed ref."""


class LabTraceStore:
    """The standalone `TraceStore` — an in-process content-addressed map of accepted
    traces. This is Lab's own trace store: with it, Lab runs experiments, stores
    traces and builds EvidenceCases with NO Control Plane. A durable deployment swaps
    it for another **Lab-owned** backend (on-disk / DB) — never a CP store; there is
    no shared trace fabric (architecture-boundary.md).

    The store owns addressing and immutability: `put`
    canonicalizes the trace, computes the ref itself, keeps an immutable byte copy,
    and `get` returns a fresh decoded copy — so a caller cannot mutate a stored
    trace out from under its ref, and the ref always matches the stored bytes."""

    def __init__(self) -> None:
        self._by_ref: dict[str, str] = {}  # ref -> canonical JSON bytes (as str)

    def put(self, trace: dict[str, object]) -> str:
        from lab_contracts import content_hash
        ref = content_hash(trace)
        canonical = json.dumps(trace, sort_keys=True, separators=(",", ":"))
        existing = self._by_ref.get(ref)
        if existing is not None and existing != canonical:
            # a hash collision under a different body — never silently keep one
            raise TraceStoreIntegrityError(f"ref {ref} already holds different bytes")
        self._by_ref[ref] = canonical  # same bytes → idempotent
        return ref

    def get(self, trace_ref: str) -> dict[str, object] | None:
        raw = self._by_ref.get(trace_ref)
        return json.loads(raw) if raw is not None else None  # a fresh copy each call
