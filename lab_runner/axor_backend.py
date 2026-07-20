"""The REAL axor-core kernel as a selectable backend (review P0.2).

Instead of the reference taint_floor reimplementation, this drives the actual
production `axor_core.governor.ToolCallGovernor` — the same per-value taint
engine and 9-gate sequence the Control Plane enforces. A condition selects it
by pinning a real kernel version (e.g. `axor-core@0.9.2`); the reference kernel
stays the fallback for environments without axor-core installed.

The governor is stateful (a session ledger built from tool outputs), so ONE
function — `gate_with_governor` — drives `evaluate` + `register_output` over a
trial's tool sequence, and BOTH the live runner and replay call it with the
same reconstructed inputs (architecture rule 0: one decision path, so replay
cannot diverge). Taint here is content-derivation (the real engine), not Lab's
explicit-flow ledger; the ledger remains Lab's EvidenceCase explanation, the
verdict is axor-core's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_contracts.semantics import EGRESS_CLASSES

from .errors import UnknownKernelError

try:
    import axor_core  # noqa: F401
    from axor_core.governor import ToolCallGovernor

    HAS_AXOR_CORE = True
    AXOR_CORE_VERSION = getattr(axor_core, "__version__", "unknown")
except ImportError:  # pragma: no cover - environment without axor-core
    HAS_AXOR_CORE = False
    AXOR_CORE_VERSION = None

GATE_CATEGORY_MAP = {
    "taint_enforcement": "taint_floor",
    "consequence": "consequence",
    "value_policy": "value_policies",
    "ssrf": "ssrf",
    "positional": "positional",
    "carrier": "carrier",
    "unclassified_tool": "capability",
}


def axor_available() -> bool:
    return HAS_AXOR_CORE


def real_kernel_version() -> str | None:
    """The pinned string for the installed axor-core, e.g. 'axor-core@0.9.2'."""
    return f"axor-core@{AXOR_CORE_VERSION}" if HAS_AXOR_CORE else None


def is_real_kernel_version(version: str) -> bool:
    return version.startswith("axor-core@")


@dataclass(frozen=True)
class AxorKernel:
    """A real-kernel backend: drives axor_core.governor.ToolCallGovernor.

    Carries the governor CONFIG derived from the scenario's manifests + the
    condition policy; a fresh governor is built per trial (session isolation).
    Shares the reference kernel's surface (`version`) so the registry is
    uniform, but run_trial/replay dispatch to the governor path for it.
    """

    version: str
    config: dict[str, object] = field(default_factory=dict)
    taint_floor_enabled: bool = True  # only for regression-style variants

    def is_real(self) -> bool:
        return True


def resolve_kernel(
    version: str,
    manifests: dict[str, dict[str, object]],
    policy: dict[str, object] | None,
    registry: object,
) -> object:
    """Pick the kernel for a condition: the REAL axor-core governor ONLY when
    the pinned version EXACTLY matches the installed axor-core, else the
    reference kernel. We never silently run a different build than pinned — a
    condition pinning axor-core@0.4.2 does not get the installed 0.9.2 (that
    would be the version-is-metadata dishonesty the review flagged)."""
    if HAS_AXOR_CORE and version == real_kernel_version():
        return AxorKernel(version=version, config=governor_config(manifests, policy))
    return registry.get(version)  # type: ignore[attr-defined]


def governor_config(
    manifests: dict[str, dict[str, object]], policy: dict[str, object] | None
) -> dict[str, object]:
    """Map Lab tool manifests + condition policy → ToolCallGovernor kwargs.

    - egress_sinks: tools whose effect can resolve to EXPORT/EXEC;
    - untrusted_sources: tools declaring untrusted result fields;
    - driving_args: each sink's effect.driving_args;
    - value_policies: an allowlist becomes an enum destination policy
      (the sound, paraphrase-proof control the kernel supersedes taint with).
    """
    egress: set[str] = set()
    untrusted: set[str] = set()
    driving: dict[str, list[str]] = {}
    for tool_id, manifest in manifests.items():
        effect: dict[str, object] = manifest.get("effect", {})  # type: ignore[assignment]
        classes = {str(effect.get("default_class"))}
        classes.update(str(rule["class"]) for rule in effect.get("resolve", []))  # type: ignore[union-attr]
        if classes & EGRESS_CLASSES:
            egress.add(tool_id)
        if manifest.get("untrusted_fields"):
            untrusted.add(tool_id)
        args = list(effect.get("driving_args", []))  # type: ignore[arg-type]
        if args:
            driving[tool_id] = args
    config: dict[str, object] = {
        "egress_sinks": egress,
        "untrusted_sources": untrusted,
        "driving_args": driving,
    }
    allowlist = (policy or {}).get("allowlist")
    if allowlist:
        # an operator allowlist → an enum value_policy on each egress sink's
        # driving arg (the kernel's decidable destination control)
        value_policies: dict[str, object] = {}
        for sink in egress:
            arg = (driving.get(sink) or ["recipient"])[0]
            value_policies[sink] = {arg: {"enum": list(allowlist)}}
        config["value_policies"] = value_policies
    return config


def gate_with_governor(
    config: dict[str, object],
    enforcement: str,
    registrations: list[tuple[str, object]],
    sink_tool: str,
    sink_args: dict[str, object],
    driving_value_id: str,
) -> dict[str, object]:
    """The single decision path (live AND replay).

    ``registrations`` is the ordered list of (read_tool, untrusted_value) the
    governor should taint before the sink call; both live and replay pass the
    same reconstructed values, so the governor's verdict is deterministic.
    """
    if enforcement == "off":
        return {
            "verdict": "ALLOW", "gate": "taint_floor", "driving_value_id": driving_value_id,
            "reason": "enforcement off (observe-only); observation stays on",
        }
    if not HAS_AXOR_CORE:  # pragma: no cover
        raise UnknownKernelError("axor-core is not installed; cannot use the real kernel backend")

    governor = ToolCallGovernor(**config)  # type: ignore[arg-type]
    for read_tool, value in registrations:
        read_decision = governor.evaluate(read_tool, {})
        governor.register_output(read_decision, value)
    decision = governor.evaluate(sink_tool, sink_args)
    if decision.allowed:
        return {
            "verdict": "ALLOW", "gate": "taint_floor", "driving_value_id": driving_value_id,
            "reason": "axor-core governor: allowed",
        }
    return {
        "verdict": "DENY",
        "gate": GATE_CATEGORY_MAP.get(decision.category, decision.category),
        "driving_value_id": driving_value_id,
        "projection": "untrusted-derived",
        "reason": f"axor-core governor [{decision.category}]: {decision.reason}",
    }
