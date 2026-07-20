"""lab_sandbox — resource-limited execution + the sandbox POLICY layer.

⚠️ MATURITY: experimental. This is NOT a security boundary for hostile code
from untrusted users. It provides (1) real OS resource limits via subprocess +
RLIMIT (`run_python`: CPU/mem/disk/wall/output/process caps that the kernel
actually enforces) and (2) the *policy decision layer* a real isolation
runtime would consult (egress allowlist, no host mounts, non-persistent
secrets, audit). It does NOT provide namespace/seccomp/gVisor isolation:
network egress and the host filesystem are not sandboxed at the process level.
Do not run untrusted arbitrary code with this alone — the MVP posture stays
local-only trusted execution until the isolation runtime (plan B6) lands, at
which point that runtime enforces exactly these decisions.
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
