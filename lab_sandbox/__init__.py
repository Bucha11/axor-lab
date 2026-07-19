"""lab_sandbox — the sandbox POLICY layer (plan B6, spec-lab.md §9).

"Runs in the lab sandbox" is a real subsystem, not a phrase. The actual
isolation is gVisor/Firecracker-class at the runtime; this module is the
*policy decision layer* it enforces: egress deny-by-default + API allowlist,
CPU/RAM/disk/wall-time caps, no host mounts, secret injection without
persistence, output-size caps, an audit trail, and kill/cancel. Until the
isolation runtime lands, code execution stays local-only (the MVP posture) —
this layer is what that runtime consults, and the red-team suite drives it.
"""

from .errors import SandboxDenied, SandboxError
from .executor import (
    HAS_RESOURCE,
    OUTCOME_COMPLETED,
    OUTCOME_KILLED_CPU,
    OUTCOME_KILLED_FSIZE,
    OUTCOME_KILLED_WALL,
    OUTCOME_OUTPUT_CAPPED,
    ExecutionResult,
    run_python,
)
from .policy import ResourceLimits, SandboxPolicy

__all__ = [
    "ExecutionResult",
    "HAS_RESOURCE",
    "OUTCOME_COMPLETED",
    "OUTCOME_KILLED_CPU",
    "OUTCOME_KILLED_FSIZE",
    "OUTCOME_KILLED_WALL",
    "OUTCOME_OUTPUT_CAPPED",
    "ResourceLimits",
    "SandboxDenied",
    "SandboxError",
    "SandboxPolicy",
    "run_python",
]
