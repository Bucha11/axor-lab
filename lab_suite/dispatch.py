"""Suite → runtime assignment → collected run.

The **main entry point**, and the one the architecture boundary mandates
(`contracts/architecture-boundary.md`): the user wraps their own agent with the
Axor runtime (axor-wrap), it runs on THEIR machine against THEIR tools under
THEIR governor, and pushes traces outward. Lab hands out assignments and reads
what comes back. Lab never connects to, executes, or proxies the agent.

That is what distinguishes this from `execute.run_suite`, which runs the agent
locally against simulated tools. Both are legitimate — `lifecycle.md` lists demo
and offline_runner alongside connected_runtime — but only this one is "bring
your own agent". The other is "bring your own model key".

Because the traces now come from a machine Lab does not control, the collection
side is written as if the runtime were untrusted:

  - a trace for a trial that was never planned is REFUSED, not absorbed;
  - a trace whose trial coordinate disagrees with the plan is refused;
  - aggregates are RECOMPUTED here from the returned traces — a runtime-supplied
    aggregate is never adopted, because a result nobody can recheck is not a
    result;
  - invariants are evaluated by Lab, for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts import content_hash, validate_artifact
from lab_runner.invariants import check_invariant
from lab_runner.predicates import evaluate
from lab_runner.runner import observe_only_condition

from .errors import SuiteError
from .execute import SuiteRun, _aggregate
from .manifest import ResolvedSuite, resolve_suite


class DispatchError(SuiteError):
    """The runtime returned something the plan does not account for."""


@dataclass(frozen=True)
class SuiteAssignment:
    """What Lab handed to a runtime, and what it expects back."""

    run_id: str
    runtime_ref: str
    resolved: ResolvedSuite
    conditions: tuple[dict[str, object], ...]
    planned: tuple[str, ...]
    assignment: dict[str, object]


def plan_trials(
    resolved: ResolvedSuite, conditions: list[dict[str, object]]
) -> list[str]:
    """The trial units this suite expands to, deterministically.

    Same shape as `runtime_jobs.plan_experiment` — `scenario:condition:repeat` —
    so a runtime that already speaks the runtime-jobs protocol needs no new
    vocabulary to execute a suite.
    """
    return [
        f"{scenario['name']}:{condition['id']}:{index}"
        for scenario in resolved.scenarios
        for condition in conditions
        for index in range(resolved.repeats)
    ]


def build_assignment(
    manifest: dict[str, object],
    runtime_ref: str,
    scenario_registry: dict[str, dict[str, object]] | None = None,
    tool_manifests: dict[str, dict[str, object]] | None = None,
) -> SuiteAssignment:
    """Resolve a suite into an assignment a connected runtime can execute.

    The assignment carries the RESOLVED suite — scenario bodies, tool manifests
    and conditions, not references — because the runtime has no access to Lab's
    registries, and because freezing them here is what stops a later registry
    edit from changing what a finished run claims to have executed.
    """
    resolved = resolve_suite(manifest, scenario_registry, tool_manifests)
    # A connected runtime IS the wrap: the agent runs under axor-core whether
    # or not gates enforce. So a suite that declares no conditions gets the
    # UNGOVERNED arm — enforcement off, kernel present — and never a
    # kernel-free one. Otherwise the agent would not be observed, switching
    # governance on later would mean re-integrating rather than flipping
    # `enforcement`, and an ungoverned/governed comparison would contrast two
    # different machines instead of one machine under two policies.
    conditions = list(resolved.conditions) or [observe_only_condition()]
    planned = plan_trials(resolved, conditions)
    assignment: dict[str, object] = {
        "schema_version": "experiment/v1",
        "id": f"exp_{resolved.id}",
        "type": "benchmark",
        "suite_ref": resolved.id,
        "scenario_ids": [str(s["name"]) for s in resolved.scenarios],
        "repeats": resolved.repeats,
        "agent_ref": _agent_ref(resolved),
        # the executable payload — everything the runtime needs to run locally
        "suite": resolved.manifest,
        "scenarios": list(resolved.scenarios),
        "tool_manifests": list(resolved.manifests.values()),
        "planned_trials": planned,
    }
    # ALWAYS the arms actually planned, including a synthesized ungoverned one.
    # Sending only suite-declared conditions left the runtime with trial units
    # naming an arm the assignment never described — it could not know which
    # kernel to wrap under or whether to enforce, and would have to guess.
    assignment["conditions"] = list(conditions)
    return SuiteAssignment(
        run_id="", runtime_ref=runtime_ref, resolved=resolved,
        conditions=tuple(conditions), planned=tuple(planned), assignment=assignment,
    )


def assign_suite(
    manifest: dict[str, object],
    runtime_ref: str,
    store: object,
    scenario_registry: dict[str, dict[str, object]] | None = None,
    tool_manifests: dict[str, dict[str, object]] | None = None,
    require_confirmation: bool = False,
) -> SuiteAssignment:
    """Register the assignment with a RuntimeJobStore; the runtime claims it."""
    built = build_assignment(manifest, runtime_ref, scenario_registry, tool_manifests)
    created = store.create_run(  # type: ignore[attr-defined]
        runtime_ref, built.assignment, list(built.planned),
        require_confirmation=require_confirmation,
        estimate={"trials": len(built.planned),
                  "scenarios": len(built.resolved.scenarios),
                  "conditions": len(built.conditions),
                  "repeats": built.resolved.repeats},
    )
    return SuiteAssignment(
        run_id=str(created["run_id"]), runtime_ref=runtime_ref,
        resolved=built.resolved, conditions=built.conditions,
        planned=built.planned, assignment=built.assignment,
    )


def collect_suite_run(assignment: SuiteAssignment, store: object) -> SuiteRun:
    """Assemble a SuiteRun from what the runtime pushed back.

    Treats the runtime as untrusted: every trace is schema-checked and matched
    against the plan before it is admitted, and every number Lab reports is
    recomputed here rather than accepted.
    """
    results: dict[str, object] = store.results(assignment.run_id)  # type: ignore[attr-defined]
    run = SuiteRun(
        run_id=assignment.run_id, resolved=assignment.resolved,
        conditions=list(assignment.conditions),
    )
    planned = set(assignment.planned)
    by_unit = {
        f"{s['name']}:{c['id']}:{i}": (s, c, i)
        for s in assignment.resolved.scenarios
        for c in assignment.conditions
        for i in range(assignment.resolved.repeats)
    }

    reported = {str(t["trial_id"]): t for t in results.get("trials", [])}  # type: ignore[union-attr]
    traces_by_unit: dict[str, dict[str, object]] = {}
    for trace in results.get("traces", []):  # type: ignore[union-attr]
        unit = _unit_of(trace)
        if unit not in planned:
            raise DispatchError(
                f"runtime returned a trace for {unit!r}, which is not in the plan — "
                "a run may only contain trials Lab assigned"
            )
        errors = validate_artifact(trace, "trace")
        if errors:
            raise DispatchError(f"runtime returned an invalid trace for {unit!r}: {errors[:3]}")
        _require_wrapped(unit, trace, by_unit[unit][1])
        traces_by_unit[unit] = trace

    for unit in assignment.planned:
        scenario, condition, index = by_unit[unit]
        trial_record = _trial_record(assignment, unit, scenario, condition, index)
        trace = traces_by_unit.get(unit)
        if trace is None:
            # a planned trial with no trace is MISSING, and recorded as such —
            # dropping it would shrink the denominator and flatter the result
            status = str(reported.get(unit, {}).get("status", "failed"))  # type: ignore[union-attr]
            run.trials.append({
                **trial_record, "status": "failed" if status != "excluded" else "excluded",
                "failure_reason": "no trace returned by the runtime",
            })
            continue
        trace_ref = content_hash(trace)
        run.trials.append({
            **trial_record, "status": "completed", "trace_ref": trace_ref,
            # the runtime's MEASUREMENTS (duration, tokens, cost) it alone could
            # take, plus Lab's own EVALUATION of the scenario's predicates over
            # the returned trace. The split is the point: Lab cannot time a run
            # on someone else's machine, but it must never take a verdict about
            # success or breach on trust — that is the whole claim the artifact
            # publishes, and it has to be recomputable from the trace.
            "metrics": {
                **_metrics_of(reported.get(unit, {})),  # type: ignore[arg-type]
                **_evaluated(scenario, trace),
            },
        })
        run.traces[trace_ref] = trace

    # Lab recomputes. A runtime-supplied aggregate would be a number nobody can
    # recheck, and `results["aggregates"]` is deliberately ignored here.
    run.aggregates = _aggregate(run)
    run.invariants = [
        check_invariant(regression, run.trials, run.traces,
                        {str(s["name"]): s for s in assignment.resolved.scenarios})
        for regression in assignment.resolved.regressions
    ]
    return run


def _require_wrapped(
    unit: str, trace: dict[str, object], condition: dict[str, object]
) -> None:
    """The trace must agree with the arm about whether a kernel observed it.

    An arm that names a kernel — including the UNGOVERNED one, whose enforcement
    is off but whose observation is not — can only be satisfied by a runtime
    that actually wrapped the agent. A trace coming back with no
    producer.kernel_version means the agent ran unwrapped, and accepting it
    would report an unobserved run as an ungoverned one: no value ledger, no
    replayable verdicts, and no way to turn governance on later without
    re-integrating.

    verify_bundle applies the same rule when a bundle is assembled. Checking it
    HERE means the mismatch is attributed to the runtime that sent it, at the
    moment it arrives, instead of surfacing later as an integrity error on a
    bundle nobody can attribute.
    """
    producer: dict[str, object] = trace.get("producer", {})  # type: ignore[assignment]
    declared, recorded = condition.get("kernel"), producer.get("kernel_version")
    if declared and not recorded:
        raise DispatchError(
            f"trial {unit!r} was assigned to arm {condition.get('id')!r} under kernel "
            f"{declared!r}, but the returned trace names no kernel_version — the agent "
            f"ran unwrapped. An ungoverned arm is still observed by the kernel; a run "
            f"nothing observed cannot be reported as one governance merely permitted"
        )
    if recorded and not declared:
        raise DispatchError(
            f"trial {unit!r} returned a trace claiming kernel {recorded!r}, but arm "
            f"{condition.get('id')!r} declares none"
        )
    if declared and recorded and str(declared) != str(recorded):
        raise DispatchError(
            f"trial {unit!r} ran under kernel {recorded!r} but was assigned {declared!r}"
        )


def _evaluated(scenario: dict[str, object], trace: dict[str, object]) -> dict[str, object]:
    """Lab's own evaluation of the scenario's typed predicates over the trace.

    Not taken from the runtime. A runtime reporting its own task_success would
    be grading its own homework, and the aggregate that ends up in a published
    artifact has to be reproducible by anyone holding the trace.
    """
    inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
    evaluated: dict[str, object] = {}
    success = scenario.get("task_success")
    if success is not None:
        evaluated["task_success"] = bool(evaluate(success, trace, inputs))  # type: ignore[arg-type]
    violation = scenario.get("violation")
    if violation is not None:
        evaluated["ASR"] = bool(evaluate(violation, trace, inputs))  # type: ignore[arg-type]
    return evaluated


def _agent_ref(resolved: ResolvedSuite) -> str:
    agents = list(resolved.manifest.get("agents") or [])
    return str(agents[0].get("ref", "runtime")) if agents else "runtime"


def _unit_of(trace: dict[str, object]) -> str:
    trial: dict[str, object] = trace.get("trial", {})  # type: ignore[assignment]
    return (
        f"{trial.get('scenario_id')}:{trial.get('condition_id')}:"
        f"{trial.get('repeat_index')}"
    )


def _trial_record(
    assignment: SuiteAssignment, unit: str, scenario: dict[str, object],
    condition: dict[str, object], index: int,
) -> dict[str, object]:
    from lab_runner.runner import trial_id_for

    seed = f"s{index:03d}"
    return {
        "trial_id": trial_id_for(
            assignment.run_id, str(scenario["name"]), str(condition["id"]), seed, index,
        ),
        "scenario_id": str(scenario["name"]), "condition_id": str(condition["id"]),
        "seed": seed, "repeat_index": index, "execution_id": assignment.run_id,
        "execution_order": assignment.planned.index(unit),
    }


def _metrics_of(reported: dict[str, object]) -> dict[str, object]:
    """Per-trial metrics the RUNTIME measured, reported BESIDE the trace.

    Not inside it: trace/v1 is axor-core-owned and describes what happened,
    while cost and latency are Lab's experiment metadata. Lab did not execute
    the trial so it cannot time it; what it can do is refuse to invent numbers.
    Only scalars the runtime actually reported are kept, and a missing one stays
    missing so an invariant over it errors instead of passing.
    """
    raw = reported.get("metrics")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value for key, value in raw.items()
        if isinstance(value, (int, float, str, bool))
    }
