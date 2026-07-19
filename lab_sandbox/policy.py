"""The sandbox policy decision layer (spec-lab.md §9 enumerated controls).

Every method is a pure decision + an audit record. The isolation runtime
(gVisor/Firecracker) calls these to decide whether to permit a syscall-level
action; here they are testable without the runtime, and the red-team suite
asserts each control blocks its attack class.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import SandboxDenied


@dataclass(frozen=True)
class ResourceLimits:
    """Hard caps; a request over any cap is denied (spec-lab.md §9)."""

    cpu_seconds: float = 30.0
    mem_mb: int = 512
    disk_mb: int = 256
    wall_seconds: float = 120.0
    output_kb: int = 1024
    max_processes: int = 64  # fork-bomb backstop


@dataclass
class SandboxPolicy:
    """Egress deny-by-default + caps + no host mounts + non-persistent secrets."""

    egress_allowlist: frozenset[str] = frozenset()
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    audit: list[dict[str, object]] = field(default_factory=list)
    _secret_reads: int = 0

    # -- egress: deny by default ------------------------------------------

    def check_egress(self, host: str) -> None:
        allowed = host in self.egress_allowlist
        self._record("egress", host, allowed)
        if not allowed:
            raise SandboxDenied("egress", f"host {host!r} not in the API allowlist (deny-by-default)")

    # -- resource caps ----------------------------------------------------

    def check_resource(self, kind: str, requested: float) -> None:
        cap = {
            "cpu_seconds": self.limits.cpu_seconds,
            "mem_mb": self.limits.mem_mb,
            "disk_mb": self.limits.disk_mb,
            "wall_seconds": self.limits.wall_seconds,
            "output_kb": self.limits.output_kb,
            "processes": self.limits.max_processes,
        }.get(kind)
        if cap is None:
            raise SandboxDenied("resource", f"unknown resource {kind!r}")
        within = requested <= cap
        self._record(f"resource:{kind}", requested, within)
        if not within:
            raise SandboxDenied("resource", f"{kind} request {requested} exceeds cap {cap}")

    # -- filesystem: no host mounts ---------------------------------------

    def check_mount(self, path: str) -> None:
        # ephemeral FS only; any host mount is denied
        self._record("mount", path, False)
        raise SandboxDenied("mount", f"host mount {path!r} denied (ephemeral FS, no host mounts)")

    # -- secrets: injected without persistence ----------------------------

    def read_secret(self, name: str, resolver: "object") -> str:
        """Return a secret value WITHOUT persisting it; audit records the
        access, never the value."""
        self._secret_reads += 1
        self._record("secret", name, True)  # name only — never the value
        return str(resolver(name))  # type: ignore[operator]

    def secret_reads(self) -> int:
        return self._secret_reads

    def _record(self, control: str, target: object, allowed: bool) -> None:
        self.audit.append({"control": control, "target": target, "allowed": allowed})
