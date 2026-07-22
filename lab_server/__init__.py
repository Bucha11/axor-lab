"""lab_server — the hosted surface (plan Phase 4 + minimal Phase 5).

The publish handshake (schema + hash + safe replay verification), an
append-only attestation log, and HTML catalog / publication / EvidenceCase
pages. Stdlib-only (`http.server`); the server executes no live agents —
only deterministic replay to confirm published verdicts.
"""

from .app import Unauthorized, make_server
from .errors import NotFound, PublishRejected, ServerError
from .providers import (
    ArtifactStore,
    ControlPlaneIdentityProvider,
    ControlPlanePromotionBackend,
    ControlPlaneRuntimeProvider,
    LabTraceStore,
    RuntimeRegistry,
    TraceIngest,
    TraceStore,
)
from .runtime_jobs import (
    InMemoryRuntimeRegistry,
    RuntimeJobsError,
    RuntimeJobStore,
    make_runtime_server,
    plan_experiment,
)
from .store import PublicationStore, StoredPublication

__all__ = [
    "NotFound",
    "PublicationStore",
    "PublishRejected",
    "ArtifactStore",
    "ControlPlaneIdentityProvider",
    "ControlPlanePromotionBackend",
    "ControlPlaneRuntimeProvider",
    "InMemoryRuntimeRegistry",
    "LabTraceStore",
    "RuntimeJobStore",
    "RuntimeJobsError",
    "RuntimeRegistry",
    "TraceIngest",
    "TraceStore",
    "ServerError",
    "StoredPublication",
    "Unauthorized",
    "make_runtime_server",
    "make_server",
    "plan_experiment",
]
