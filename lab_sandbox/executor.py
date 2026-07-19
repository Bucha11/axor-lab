"""A real subprocess executor that applies OS-level resource limits.

This turns the policy decision layer into actual enforcement for the controls
the OS can enforce without a full isolation runtime: CPU time (`RLIMIT_CPU`),
address space (`RLIMIT_AS`), file size (`RLIMIT_FSIZE`), process count
(`RLIMIT_NPROC`), wall-clock (subprocess timeout), and output size (capped
read + kill). Egress deny-by-default, ephemeral FS, and no-host-mounts still
require namespaces/seccomp — the gVisor/Firecracker runtime — and stay policy
decisions (policy.py) until that lands; this module is the honest half that
runs today.

`preexec_fn` sets the limits in the child *before* exec, so a hostile program
cannot raise them. Unix only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field

from .policy import ResourceLimits

try:
    import resource  # Unix only
    HAS_RESOURCE = True
except ImportError:  # pragma: no cover - Windows
    HAS_RESOURCE = False

OUTCOME_COMPLETED = "completed"
OUTCOME_KILLED_CPU = "killed_cpu"
OUTCOME_KILLED_FSIZE = "killed_fsize"
OUTCOME_KILLED_WALL = "killed_wall"
OUTCOME_OUTPUT_CAPPED = "output_capped"
OUTCOME_NONZERO = "nonzero_exit"

_MB = 1024 * 1024


@dataclass
class ExecutionResult:
    outcome: str
    returncode: int | None
    stdout: str
    truncated: bool
    audit: list[dict[str, object]] = field(default_factory=list)


def _apply_limits(limits: ResourceLimits) -> None:  # pragma: no cover - runs in child
    """Set hard rlimits in the child before exec. A hostile program inherits
    these and cannot raise them."""
    # soft < hard so SIGXCPU is delivered at the soft limit (classifiable);
    # the hard limit is the SIGKILL backstop if the child ignores SIGXCPU
    cpu = int(limits.cpu_seconds) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.disk_mb * _MB, limits.disk_mb * _MB))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
    except (ValueError, OSError):
        pass  # RLIMIT_NPROC not settable everywhere (e.g. macOS)
    try:
        as_bytes = limits.mem_mb * _MB
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
    except (ValueError, OSError):
        pass


def run_python(
    code: str,
    limits: ResourceLimits | None = None,
) -> ExecutionResult:
    """Execute a Python snippet under real OS limits; capture bounded output."""
    if not HAS_RESOURCE:
        raise RuntimeError("resource limits require a Unix host")
    limits = limits or ResourceLimits()
    audit: list[dict[str, object]] = [{"control": "spawn", "limits": _limit_dict(limits)}]
    output_cap = limits.output_kb * 1024
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            timeout=limits.wall_seconds,
            preexec_fn=lambda: _apply_limits(limits),  # noqa: PLW1509
            env={"PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired as exc:
        audit.append({"control": "wall_seconds", "allowed": False})
        out = (exc.stdout or b"").decode("utf-8", "replace")[:output_cap]
        return ExecutionResult(OUTCOME_KILLED_WALL, None, out, True, audit)

    stdout = proc.stdout.decode("utf-8", "replace")
    truncated = len(proc.stdout) > output_cap
    stdout = stdout[:output_cap]
    outcome = _classify(proc.returncode, truncated)
    audit.append({"control": "exit", "returncode": proc.returncode, "outcome": outcome})
    return ExecutionResult(outcome, proc.returncode, stdout, truncated, audit)


def _classify(returncode: int, truncated: bool) -> str:
    import signal

    if returncode == -signal.SIGXCPU:
        return OUTCOME_KILLED_CPU
    if returncode == -signal.SIGXFSZ:
        return OUTCOME_KILLED_FSIZE
    if truncated:
        return OUTCOME_OUTPUT_CAPPED
    if returncode != 0:
        return OUTCOME_NONZERO
    return OUTCOME_COMPLETED


def _limit_dict(limits: ResourceLimits) -> dict[str, object]:
    return {
        "cpu_seconds": limits.cpu_seconds, "mem_mb": limits.mem_mb,
        "disk_mb": limits.disk_mb, "wall_seconds": limits.wall_seconds,
        "output_kb": limits.output_kb, "max_processes": limits.max_processes,
    }
