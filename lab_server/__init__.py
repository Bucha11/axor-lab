"""lab_server — the hosted surface (plan Phase 4 + minimal Phase 5).

The publish handshake (schema + hash + safe replay verification), an
append-only attestation log, and HTML catalog / publication / EvidenceCase
pages. Stdlib-only (`http.server`); the server executes no live agents —
only deterministic replay to confirm published verdicts.
"""

from .app import make_server
from .errors import NotFound, PublishRejected, ServerError
from .store import PublicationStore, StoredPublication

__all__ = [
    "NotFound",
    "PublicationStore",
    "PublishRejected",
    "ServerError",
    "StoredPublication",
    "make_server",
]
