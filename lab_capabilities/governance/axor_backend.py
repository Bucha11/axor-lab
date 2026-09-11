"""The REAL axor-core kernel as a selectable backend (review P0.2).

Instead of the reference taint_floor reimplementation, this drives the actual
production `axor_core.governor.ToolCallGovernor` — the same per-value taint
engine and 9-gate sequence the Control Plane enforces. It is the ONLY kernel: a
condition pins a real kernel version (e.g. `axor-core@0.10.2`) and there is no
reference reimplementation to fall back to.

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

from lab_runner.errors import UnknownKernelError

try:
    import axor_core  # noqa: F401
    from axor_core.governor import ToolCallGovernor

    HAS_AXOR_CORE = True
    AXOR_CORE_VERSION = getattr(axor_core, "__version__", "unknown")
except ImportError:  # pragma: no cover - environment without axor-core
    HAS_AXOR_CORE = False
    AXOR_CORE_VERSION = None

# The category -> gate mapping now lives in axor-core, which owns both
# vocabularies. This local copy was keyed on `ssrf`, `consequence`,
# `positional`, `carrier` — names the kernel does not emit (it emits
# `ssrf_gate`, `consequence_gate`, ...) — so seven of eleven categories fell
# through unmapped and the raw category landed in `decision.gate`, which accepts
# only a gate name. Any denial outside the taint floor produced a trace that
# failed Lab's own schema.
if HAS_AXOR_CORE:  # pragma: no branch - axor-core is a required dependency
    from axor_core.governor import gate_of
else:  # pragma: no cover
    def gate_of(category: str) -> str:
        raise UnknownKernelError("axor-core is required to name a denial's gate")


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
    """

    version: str
    config: dict[str, object] = field(default_factory=dict)
    taint_floor_enabled: bool = True  # only for regression-style variants

    def is_real(self) -> bool:
        return True

    @property
    def behavior_version(self) -> str:
        """Identity that reflects behavior-changing flags, so the trial can
        record the ACTUAL resolved backend it ran under, not just the declared
        version string (review r20)."""
        if not self.taint_floor_enabled:
            return f"{self.version}+taint_floor=off"
        return self.version


@dataclass(frozen=True)
class KernelRegistry:
    """Maps pinned kernel version strings to real-kernel backends.

    The reference kernel is gone: the ONLY kernel is the installed axor-core, so
    a registry holds `AxorKernel` handles. It is retained for API compatibility
    (callers that build `{version: kernel}` maps and pass them to `replay_bundle`)
    — `resolve_kernel` rebuilds a real kernel's config from the manifests
    directly and never consults the registry for it, so a registry entry's empty
    config is only ever a placeholder identity."""

    kernels: tuple[AxorKernel, ...]

    def get(self, version: str) -> AxorKernel:
        for kernel in self.kernels:
            if kernel.version == version:
                return kernel
        raise UnknownKernelError(version)


def default_registry(versions: tuple[str, ...]) -> KernelRegistry:
    """A registry naming the real kernel for every pinned version.

    Yields the real kernel only — there is no reference variant to build. A
    version that is not the installed build is still surfaced here (as a handle);
    it is `resolve_kernel` that refuses to run a build that is missing or does
    not match, never a silent substitution."""
    return KernelRegistry(kernels=tuple(AxorKernel(version=v) for v in dict.fromkeys(versions)))


def resolve_kernel(
    version: str,
    manifests: dict[str, dict[str, object]],
    policy: dict[str, object] | None,
    registry: object,
    inputs: dict[str, object] | None = None,
) -> object:
    """Pick the kernel for a condition. A REAL-kernel pin (`axor-core@X`) is
    satisfied ONLY by the exact installed build.

    The real kernel is the ONLY kernel: a version that is not an `axor-core@X`
    pin, or a real pin that is missing or does not match the installed build,
    raises UnknownKernelError — which the replay layer surfaces as
    REPLAY_UNSUPPORTED_KERNEL — rather than falling back to anything. `registry`
    is retained for signature compatibility and is not consulted."""
    if not is_real_kernel_version(version):
        raise UnknownKernelError(
            f"{version} is not an installed axor-core build; the reference kernel "
            "was removed — a run governs through the real kernel or not at all"
        )
    if not HAS_AXOR_CORE:
        raise UnknownKernelError(
            f"{version} is pinned but axor-core is not installed"
        )
    if version != real_kernel_version():
        raise UnknownKernelError(
            f"{version} is pinned but the installed build is {real_kernel_version()} — "
            "refusing to run a different build than pinned"
        )
    return AxorKernel(version=version, config=governor_config(manifests, policy, inputs))


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
        # a sensitive source ARMS THE CONFIDENTIALITY FLOOR, which restricts
        # egress for the rest of the session whether or not the sink argument
        # derives from the secret. Never passing it meant Lab's real-kernel runs
        # ran with that gate switched off: a secret read followed by an egress
        # to an attacker URL was ALLOWED here and DENIED by the same kernel under
        # axor-wrap, which does declare it.
        "sensitive_sources": set(canon["sensitive_sources"]),  # type: ignore[arg-type]
        "driving_args": canon["driving_args"],
    }
    if canon["value_policies"]:
        config["value_policies"] = _value_predicates(canon["value_policies"])  # type: ignore[arg-type]
    if canon["consequence_overrides"]:
        config["consequence_overrides"] = _consequence_classes(
            canon["consequence_overrides"],  # type: ignore[arg-type]
        )
    return config


def _value_predicates(compiled: dict[str, dict[str, dict[str, object]]]) -> dict[str, object]:
    """The canonical `{sink: {arg: {"enum": [...]}}}` → the governor's predicates.

    axor-core wants `dict[str, list[ValuePredicate]]` — objects with `.check()`.
    Handing it the nested dict meant `check_value_policies` iterated a dict and
    got its KEYS, so the first "predicate" was the string `"recipient"` and the
    call died on `'str' object has no attribute 'check'`. Every condition
    carrying an allowlist crashed under the real kernel — including the
    enum-supersession path this repo describes as the sound, paraphrase-proof
    control the kernel supersedes taint with.

    The compiled form stays a plain dict because it is what the executable
    config hash is taken over; predicates are built here, at construction.
    """
    from axor_core.policy.value_policy import enum as enum_predicate

    predicates: dict[str, object] = {}
    for sink, by_arg in compiled.items():
        built = []
        for arg, spec in by_arg.items():
            allowed = spec.get("enum")
            if allowed is None:
                raise UnknownKernelError(
                    f"value policy for {sink}.{arg} declares {sorted(spec)}, and only "
                    f"'enum' is compiled today — refusing to drop a control silently"
                )
            built.append(enum_predicate(arg, list(allowed)))  # type: ignore[arg-type]
        predicates[sink] = built
    return predicates


def _consequence_classes(overrides: dict[str, str]) -> dict[str, object]:
    """`policy.criticality_overrides` → the governor's consequence table.

    These were compiled into the config hash and then dropped, so a condition
    declaring them ran without them. The reference kernel rejects them outright
    as "hashed but ignored"; the real-kernel branch skipped that check on the
    premise that a real axor-core build executes its own policy — which was
    true of the kernel and false of what Lab handed it.
    """
    from axor_core.contracts.canonical import ConsequenceClass

    classes: dict[str, object] = {}
    for sink, name in overrides.items():
        try:
            classes[sink] = ConsequenceClass[str(name).upper()]
        except KeyError:
            raise UnknownKernelError(
                f"policy.criticality_overrides[{sink!r}] is {name!r}, which is not a "
                f"consequence class ({[c.name for c in ConsequenceClass]})"
            ) from None
    return classes


def gate_with_governor(
    config: dict[str, object],
    enforcement: str,
    registrations: list[tuple[str, object]],
    sink_tool: str,
    sink_args: dict[str, object],
    driving_value_id: str | None,
) -> dict[str, object]:
    """The single decision path (live AND replay).

    ``registrations`` is the ordered list of (read_tool, untrusted_value) the
    governor should taint before the sink call; both live and replay pass the
    same reconstructed values, so the governor's verdict is deterministic.

    `enforcement` does NOT select the verdict — it is recorded as `enforced` so
    the caller knows whether to obey it. This used to return an unconditional
    ALLOW when enforcement was off, without constructing a governor at all: the
    ungoverned arm never reached axor-core, so it recorded ALLOW for the very
    call the governed arm denied. Two arms that disagree on every verdict are
    not one machine under two policies, and there was no "governance would have
    blocked this" evidence to show for an ungoverned run.
    """
    if not HAS_AXOR_CORE:  # pragma: no cover
        raise UnknownKernelError("axor-core is not installed; cannot use the real kernel backend")

    governor = ToolCallGovernor(**config)  # type: ignore[arg-type]
    for read_tool, value in registrations:
        read_decision = governor.evaluate(read_tool, {})
        governor.register_output(read_decision, value)
    decision = governor.evaluate(sink_tool, sink_args)
    enforced = enforcement != "off"
    if decision.allowed:
        return {
            "verdict": "ALLOW", "gate": "taint_floor", "driving_value_id": driving_value_id,
            "reason": "axor-core governor: allowed", "enforced": enforced,
        }
    return {
        "verdict": "DENY",
        "gate": gate_of(str(decision.category)),
        "driving_value_id": driving_value_id,
        "projection": "untrusted-derived",
        "reason": f"axor-core governor [{decision.category}]: {decision.reason}",
        "enforced": enforced,
    }
