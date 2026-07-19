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
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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


def _killpg(proc: subprocess.Popen) -> None:  # pragma: no cover - timing dependent
    """SIGKILL the child's whole process group (it is a session leader via
    start_new_session), so descendants a wall timeout would otherwise orphan
    die too."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_python(
    code: str,
    limits: ResourceLimits | None = None,
) -> ExecutionResult:
    """Execute a Python snippet under real OS limits; capture bounded output.

    Output is read incrementally and the child's process group is killed once
    the cap is reached, so a child that prints gigabytes cannot make the PARENT
    accumulate them in memory (the old capture_output buffered everything before
    truncating). stdout+stderr share one capped stream; the child runs in an
    isolated temp cwd that is removed afterward."""
    if not HAS_RESOURCE:
        raise RuntimeError("resource limits require a Unix host")
    limits = limits or ResourceLimits()
    audit: list[dict[str, object]] = [{"control": "spawn", "limits": _limit_dict(limits)}]
    output_cap = limits.output_kb * 1024
    workdir = tempfile.mkdtemp(prefix="axor-sbx-")
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(  # noqa: PLW1509 - preexec_fn is intentional (child rlimits)
            [sys.executable, "-I", "-c", code],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            preexec_fn=lambda: _apply_limits(limits),
            env={"PATH": "/usr/bin:/bin"},
            cwd=workdir,
            start_new_session=True,  # own process group → we can kill descendants
        )
        buf = bytearray()
        state = {"capped": False}

        def _drain() -> None:
            assert proc is not None and proc.stdout is not None
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                if len(buf) < output_cap:
                    buf.extend(chunk[: output_cap - len(buf)])
                else:
                    # cap reached: stop buffering and kill the group so the
                    # child cannot keep producing output (bounded parent memory)
                    state["capped"] = True
                    _killpg(proc)
                    break

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()
        try:
            proc.wait(timeout=limits.wall_seconds)
            wall = False
        except subprocess.TimeoutExpired:
            wall = True
            audit.append({"control": "wall_seconds", "allowed": False})
            _killpg(proc)
            proc.wait()
        reader.join(timeout=1.0)

        stdout = bytes(buf).decode("utf-8", "replace")
        truncated = state["capped"]
        if wall:
            audit.append({"control": "exit", "returncode": proc.returncode, "outcome": OUTCOME_KILLED_WALL})
            return ExecutionResult(OUTCOME_KILLED_WALL, proc.returncode, stdout, True, audit)
        outcome = _classify(proc.returncode, truncated)
        audit.append({"control": "exit", "returncode": proc.returncode, "outcome": outcome})
        return ExecutionResult(outcome, proc.returncode, stdout, truncated, audit)
    finally:
        if proc is not None and proc.poll() is None:
            _killpg(proc)
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
        shutil.rmtree(workdir, ignore_errors=True)  # ephemeral working dir, cleaned up


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
