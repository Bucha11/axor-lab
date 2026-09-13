"""Investigating one trial, pinning a verdict, and checking the pins later.

These three verbs share one thing: they all read a bundle and have to resolve,
for a given trace, WHICH scenario and WHICH condition it belongs to. Getting
that resolution wrong is not cosmetic — replaying every pin under the first
scenario's inputs, or rendering a strict counterfactual for an allowlist trace,
produces a confident answer to a question nobody asked. The resolution lives
here once, so every face asks it the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .outcomes import Outcome


@dataclass(frozen=True)
class PinResult:
    """A recorded pin, and the pin set it belongs to."""

    outcome: Outcome
    trace_id: str
    expected_sequence: tuple[str, ...]
    record: dict[str, object]
    pins: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class EvidenceResult:
    """One trial's EvidenceCase."""

    outcome: Outcome
    case: dict[str, object]


@dataclass(frozen=True)
class RegressionResult:
    """What re-running every pin under a candidate kernel concluded.

    ANY status other than a clean match is unresolved — a differing verdict, a
    missing/tampered/malformed trace, or an unsupported kernel. A malformed trace
    whose recomputed sequence coincidentally equals the pin must NOT pass
    (review r13).
    """

    outcome: Outcome
    results: tuple[dict[str, object], ...] = field(default_factory=tuple)
    differs: tuple[dict[str, object], ...] = field(default_factory=tuple)
    unresolved: tuple[dict[str, object], ...] = field(default_factory=tuple)

    @property
    def other_unresolved(self) -> tuple[dict[str, object], ...]:
        """Unresolved for a reason OTHER than a changed verdict."""
        from lab_capabilities.governance.regression import STATUS_DIFFERS

        return tuple(r for r in self.unresolved if r["status"] != STATUS_DIFFERS)


def scenario_for(bundle: dict[str, object], trace: dict[str, object]) -> dict[str, object]:
    from lab_runner.errors import RunnerError

    scenario_id = str(trace["trial"]["scenario_id"])  # type: ignore[index]
    for scenario in bundle["scenarios"]:  # type: ignore[union-attr]
        if scenario["name"] == scenario_id:
            return scenario
    raise RunnerError(f"scenario {scenario_id} not in bundle")


def enforcing_condition(
    bundle: dict[str, object], condition_id: str | None
) -> dict[str, object]:
    from lab_runner.errors import RunnerError

    conditions: list[dict[str, object]] = bundle["conditions"]  # type: ignore[assignment]
    if condition_id is not None:
        for condition in conditions:
            if condition["id"] == condition_id:
                return condition
        raise RunnerError(f"condition {condition_id} not in bundle")
    for condition in conditions:
        if condition["enforcement"] == "on":
            return condition
    raise RunnerError("bundle has no enforcement-on condition")


def first_denied_trace(
    traces: dict[str, dict[str, object]]
) -> dict[str, object] | None:
    """The first trace where a denial was actually ENFORCED.

    Not merely "verdict == DENY". An observe-only arm records real denials and
    executes the call anyway, so a bare verdict match would happily pin a
    regression on a trace where nothing was contained — asserting the kernel must
    keep denying, on the evidence of a run that denied nothing.
    """
    from lab_runner.verdicts import contained

    for trace in sorted(traces.values(), key=lambda t: str(t["trace_id"])):
        for event in trace["events"]:  # type: ignore[union-attr]
            if event.get("type") == "gate_decision" and contained(event["decision"]):  # type: ignore[index,arg-type]
                return trace
    return None


def _require_trace(
    traces: dict[str, dict[str, object]], trace_id: str, label: str = "trace"
) -> dict[str, object]:
    from lab_runner.errors import RunnerError

    trace = traces.get(trace_id)
    if trace is None:
        raise RunnerError(f"{label} {trace_id} not found in bundle")
    return trace


def pin_trace(
    traces: dict[str, dict[str, object]],
    trace_id: str,
    expected: str,
    *,
    existing: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
) -> PinResult:
    """Pin a trace's verdict SEQUENCE, replacing any earlier pin for it.

    The WHOLE ordered sequence is recorded, not just the final verdict:
    persisting only `expected_verdict` made a later check compare a multi-call
    trace's real sequence (ALLOW, ALLOW, DENY) against a singleton (DENY) and cry
    regression on an unchanged trace and kernel (review r12).
    """
    from lab_capabilities.governance import pin
    from lab_runner.errors import RunnerError

    trace = _require_trace(traces, trace_id)
    # pin() also rejects an expected_verdict that contradicts the trace's final
    # recorded verdict (review r13) — surface that as a clean error.
    try:
        recorded = pin(trace, expected)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc
    record: dict[str, object] = {
        "trace_id": recorded.trace_id,
        "trace_ref": recorded.trace_ref,
        "expected_verdict": recorded.expected_verdict,
        "expected_sequence": list(recorded.expected_sequence),
    }
    kept = [p for p in existing if p["trace_id"] != trace_id]
    return PinResult(
        outcome=Outcome.OK,
        trace_id=recorded.trace_id,
        expected_sequence=tuple(recorded.expected_sequence),
        record=record,
        pins=tuple([*kept, record]),
    )


def build_evidence(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    trace_id: str,
    *,
    twin_id: str | None = None,
    policy: str | None = None,
) -> EvidenceResult:
    """The three-mode EvidenceCase over ONE trial's trace."""
    from lab_capabilities.governance import (
        build_evidence_case,
        default_registry,
        evidence_condition,
        resolve_kernel,
        validate_twin,
    )
    from lab_runner.errors import RunnerError

    trace = _require_trace(traces, trace_id)
    twin = _require_trace(traces, twin_id, "twin trace") if twin_id else None
    if twin is not None:
        # a governed twin must be the SAME case under an enforcing policy — not
        # any unrelated trace the caller happened to name (review r13)
        try:
            validate_twin(trace, twin, bundle)
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc
    scenario = scenario_for(bundle, trace)
    # the SAME condition resolver the HTML EvidenceCase uses: an explicit policy
    # wins, else the trace's own enforcing condition, else the first enforcing
    # one — never just "the first enforcement-on condition", which rendered a
    # strict counterfactual for an allowlist trace (review r13)
    try:
        condition = evidence_condition(bundle, trace, policy)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    # resolve the SAME kernel replay/regress use — the REAL axor-core governor
    # when the condition pins the installed build — and pass THIS trace's scenario
    # inputs so a real-kernel `$inputs` allowlist expands to the concrete values,
    # not the symbolic ref (review r12/r17).
    version = str(condition["kernel"])
    kernel = resolve_kernel(
        version, manifests, condition.get("policy"),  # type: ignore[arg-type]
        default_registry((version,)), scenario.get("inputs", {}),  # type: ignore[union-attr]
    )
    return EvidenceResult(
        outcome=Outcome.OK,
        case=build_evidence_case(
            trace, scenario, condition, kernel, manifests, governed_twin=twin
        ),
    )


def check_regression(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    pins_raw: list[dict[str, object]],
    *,
    condition_id: str | None = None,
    kernel_override: str | None = None,
    disable_taint_floor: bool = False,
) -> RegressionResult:
    """Re-run every pin under the CANDIDATE kernel and surface any change."""
    from lab_capabilities.governance import (
        RegressionPin,
        check_pins,
        default_registry,
        resolve_candidate_kernel_for_trace,
        resolve_kernel,
    )
    from lab_capabilities.governance.axor_backend import AxorKernel, governor_config
    from lab_capabilities.governance.regression import STATUS_DIFFERS, STATUS_MATCHES

    pins = tuple(
        RegressionPin(
            trace_id=str(p["trace_id"]),
            trace_ref=str(p["trace_ref"]),
            expected_verdict=str(p["expected_verdict"]),
            # restore the pinned ORDERED sequence (default to the singleton for
            # older pin files) so a multi-call trace is compared correctly (r12)
            expected_sequence=tuple(str(v) for v in p.get("expected_sequence", ())),
        )
        for p in pins_raw
    )
    condition = enforcing_condition(bundle, condition_id)
    version = kernel_override or str(condition["kernel"])
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    kernel_for: Callable[[dict[str, object]], object] | None = None
    if disable_taint_floor:
        # An explicit real-kernel VARIANT demonstration: the same installed build,
        # but with its egress-sink declarations dropped so the taint /
        # confidentiality floor is never armed — the exfiltration the pinned run
        # DENIED is now ALLOWED, which is exactly the regression a pin exists to
        # catch. The fingerprint marks it a different kernel (behavior_version
        # gains `+taint_floor=off`), so the report names the variant, not the
        # pinned build (review r4).
        cfg = governor_config(manifests, condition.get("policy"), None)  # type: ignore[arg-type]
        cfg.pop("egress_sinks", None)
        cfg.pop("value_policies", None)
        kernel: object = AxorKernel(version=version, config=cfg, taint_floor_enabled=False)
    else:
        # regress under the CANDIDATE kernel — the one named by the override or
        # the chosen regression condition — NOT the kernel the trace was recorded
        # under (review r18). Each pin resolves the candidate against its OWN
        # scenario inputs so a real-kernel allowlist expands per scenario.
        registry = default_registry((version,))
        kernel = resolve_kernel(version, manifests, condition.get("policy"), registry)  # type: ignore[arg-type]

        def kernel_for(trace: dict[str, object]) -> object:
            return resolve_candidate_kernel_for_trace(
                bundle, trace, condition, kernel_override, registry
            )

    # each pinned trace replays against ITS OWN scenario's inputs — a single
    # shared inputs dict would replay every pin under the first scenario's
    # allowlist / effect-resolution inputs (review r12)
    results = check_pins(
        pins, traces, condition, kernel, manifests,
        inputs_for=lambda trace: scenario_for(bundle, trace).get("inputs", {}),  # type: ignore[union-attr,arg-type]
        kernel_for=kernel_for,
    )
    differs = tuple(r for r in results if r["status"] == STATUS_DIFFERS)
    unresolved = tuple(r for r in results if r["status"] != STATUS_MATCHES)
    return RegressionResult(
        outcome=Outcome.REGRESSION_DIFFERS if unresolved else Outcome.OK,
        results=tuple(results),
        differs=differs,
        unresolved=unresolved,
    )
