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
from .policy import ResourceLimits, SandboxPolicy

__all__ = [
    "ResourceLimits",
    "SandboxDenied",
    "SandboxError",
    "SandboxPolicy",
]
