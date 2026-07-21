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

from lab_contracts import compiled_governor_config

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

    @property
    def behavior_version(self) -> str:
        """Identity that reflects behavior-changing flags (mirrors the reference
        Kernel), so the trial can record the ACTUAL resolved backend it ran under,
        not just the declared version string (review r20)."""
        if not self.taint_floor_enabled:
            return f"{self.version}+taint_floor=off"
        return self.version


def resolve_kernel(
    version: str,
    manifests: dict[str, dict[str, object]],
    policy: dict[str, object] | None,
    registry: object,
    inputs: dict[str, object] | None = None,
) -> object:
    """Pick the kernel for a condition. A REAL-kernel pin (`axor-core@X`) is
    satisfied ONLY by the exact installed build; the reference kernel is used
    ONLY for a genuine reference version — it NEVER masquerades as `axor-core@X`.

    The round-15 code fell through to `registry.get(version)` for any version,
    and `default_registry` builds a reference `Kernel(version=...)` for ANY string
    — so a bundle pinning `axor-core@0.9.2` on a machine without it replayed under
    the one-gate reference kernel yet still claimed the pinned build (review r16
    P0). Now a real-kernel pin that is missing or mismatched raises
    UnknownKernelError, which the replay layer surfaces as
    REPLAY_UNSUPPORTED_KERNEL — never a silent substitution."""
    if is_real_kernel_version(version):
        if not HAS_AXOR_CORE:
            raise UnknownKernelError(
                f"{version} is pinned but axor-core is not installed — refusing to "
                "substitute the reference kernel under a real-kernel version label"
            )
        if version != real_kernel_version():
            raise UnknownKernelError(
                f"{version} is pinned but the installed build is {real_kernel_version()} — "
                "refusing to run a different build than pinned"
            )
        return AxorKernel(version=version, config=governor_config(manifests, policy, inputs))
    # a genuine reference version → the reference registry (which raises
    # UnknownKernelError for a version it does not know)
    return registry.get(version)  # type: ignore[attr-defined]


def _scenario_inputs_for(bundle: dict[str, object], trace: dict[str, object]) -> dict[str, object]:
    trial: dict[str, object] = trace.get("trial", {})  # type: ignore[assignment]
    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    scenario = scenarios.get(str(trial.get("scenario_id")), {})
    return scenario.get("inputs", {})  # type: ignore[union-attr,return-value]


def resolve_recorded_kernel_for_trace(
    bundle: dict[str, object], trace: dict[str, object], registry: object | None = None,
) -> object:
    """Resolve the EXACT kernel a trace RAN under — for exact replay (review r18).

    Uses the trace's OWN recorded condition (its `trial.condition_id`) and its own
    scenario inputs, so `$inputs.*` allowlists expand against the world the trace
    was produced in. This is the RECORDED-kernel resolver: it answers "what
    decided this?", never "what WOULD a candidate decide?"."""
    from .kernel import default_registry

    trial: dict[str, object] = trace.get("trial", {})  # type: ignore[assignment]
    conditions = {str(c["id"]): c for c in bundle["conditions"]}  # type: ignore[union-attr]
    condition = conditions[str(trial["condition_id"])]
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    version = str(condition["kernel"])
    reg = registry if registry is not None else default_registry((version,))
    return resolve_kernel(
        version, manifests, condition.get("policy"), reg, _scenario_inputs_for(bundle, trace),
    )


# retained name (r17). The exact-replay resolver is the one it always was.
resolve_kernel_for_trace = resolve_recorded_kernel_for_trace


def resolve_candidate_kernel_for_trace(
    bundle: dict[str, object],
    trace: dict[str, object],
    candidate_condition: dict[str, object],
    candidate_version: str | None = None,
    registry: object | None = None,
) -> object:
    """Resolve the CANDIDATE kernel a counterfactual replay should run — for
    regression against a future/other kernel and for policy-override EvidenceCase
    (review r18).

    This deliberately does NOT read the trace's recorded condition/kernel. It
    takes the POLICY/enforcement from the chosen `candidate_condition`, the VERSION
    from `candidate_version` (a `--kernel` override) or that condition's kernel, and
    the INPUTS from the trace's OWN recorded scenario — so `axor-lab regress
    --kernel axor-core@X` actually runs axor-core@X, not the reference kernel the
    trace was recorded under. Conflating this with the recorded resolver silently
    replayed under the wrong backend while labelling the result with the other."""
    from .kernel import default_registry

    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    version = str(candidate_version) if candidate_version else str(candidate_condition["kernel"])
    reg = registry if registry is not None else default_registry((version,))
    return resolve_kernel(
        version, manifests, candidate_condition.get("policy"), reg,
        _scenario_inputs_for(bundle, trace),
    )


def governor_config(
    manifests: dict[str, dict[str, object]],
    policy: dict[str, object] | None,
    inputs: dict[str, object] | None = None,
) -> dict[str, object]:
    """Map Lab tool manifests + condition policy → ToolCallGovernor kwargs.

    - egress_sinks: tools whose effect can resolve to EXPORT/EXEC;
    - untrusted_sources: tools declaring untrusted result fields;
    - driving_args: each sink's effect.driving_args;
    - value_policies: an allowlist becomes an enum destination policy
      (the sound, paraphrase-proof control the kernel supersedes taint with).

    The compilation is the SAME canonical mapping the executable_config_hash is
    taken over (lab_contracts.compiled_governor_config), so the fingerprint and
    the config the governor actually runs cannot drift (review r16). ``$inputs.x``
    allowlist refs are expanded against the scenario inputs — parity with the
    reference kernel's per-trial `_resolve_allowlist`, so a real-kernel run with
    an input-backed allowlist enforces the concrete destinations, not the literal
    ``"$inputs.known_ibans"`` string."""
    canon = compiled_governor_config("", policy, list(manifests.values()), inputs)
    config: dict[str, object] = {
        "egress_sinks": set(canon["egress_sinks"]),  # type: ignore[arg-type]
        "untrusted_sources": set(canon["untrusted_sources"]),  # type: ignore[arg-type]
        "driving_args": canon["driving_args"],
    }
    if canon["value_policies"]:
        config["value_policies"] = canon["value_policies"]
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
