"""lab_endpoint — endpoint ingest (plan B5, endpoint-protocol.md).

Two modes the surface MUST keep separate, because governance is only possible
in one:

- **instrumented** — the agent emits value-carrying tool-call events and routes
  tools through the gateway, so Lab sees lineage and can gate. Produces a
  conformant `trace/v1` with `producer.mode = instrumented_endpoint`.
- **black-box** — plain task-in / answer-out. No lineage, no mid-run gating.
  Produces NO conformant trace; labeled evaluation-only everywhere.

Plus the shared endpoint safety checks (SSRF / private-network / DNS-rebinding).
"""

from .black_box import BLACK_BOX_LABEL, BlackBoxResult, score_black_box
from .errors import EndpointError, GovernanceUnavailable, UnsafeEndpoint
from .instrumented import EmittedEvent, assemble_and_gate
from .safety import ssrf_check

__all__ = [
    "BLACK_BOX_LABEL",
    "BlackBoxResult",
    "EmittedEvent",
    "EndpointError",
    "GovernanceUnavailable",
    "UnsafeEndpoint",
    "assemble_and_gate",
    "score_black_box",
    "ssrf_check",
]
