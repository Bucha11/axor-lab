"""Suite → Run → Artifact.

This is where a manifest stops being a document. It resolves the suite, builds
the trial plan, runs each trial through the general loop, computes the
aggregations the suite DECLARED, checks the invariants it ships with, and
packages the result as an artifact/v1.

Two things it deliberately does not do:

  - **Invent an aggregation.** Only what `evaluation.aggregations` names is
    computed. A platform that helpfully adds a comparison the suite never asked
    for is how McNemar became a default instead of a request.
  - **Fill in a metric.** A metric a suite did not measure stays absent, so an
    invariant over it errors rather than passing on a number nobody produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_analysis import binary_aggregate
from lab_contracts import build_artifact, build_bundle, content_hash, reproducibility_of
from lab_runner.axor_backend import resolve_kernel
from lab_runner.invariants import InvariantResult, check_invariant
from lab_runner.kernel import KernelRegistry, default_registry
from lab_runner.loop import LoopOutcome, run_loop_trial
from lab_runner.runner import observe_only_condition, trial_id_for

from .manifest import ResolvedSuite, resolve_suite
from .sdk import BaseSuite, SuiteRegistry, builtin_registry

_STATS_AGGREGATORS = {
    "mean": lambda vs: sum(vs) / len(vs),
    "sum": sum,
    "min": min,
    "max": max,
}


@dataclass
class SuiteRun:
    """Everything one suite execution produced."""

    run_id: str
    resolved: ResolvedSuite
    trials: list[dict[str, object]] = field(default_factory=list)
    traces: dict[str, dict[str, object]] = field(default_factory=dict)
    outcomes: dict[str, LoopOutcome] = field(default_factory=dict)
    aggregates: list[dict[str, object]] = field(default_factory=list)
    invariants: list[InvariantResult] = field(default_factory=list)
    conditions: list[dict[str, object]] = field(default_factory=list)

    def bundle(self, bundle_id: str, created: str, environment: dict[str, object]) -> dict[str, object]:
        return build_bundle(
            bundle_id=bundle_id, created=created,
            scenarios=list(self.resolved.scenarios), conditions=self.conditions,
            tool_manifests=list(self.resolved.manifests.values()),
            environment=environment, trials=self.trials,
            aggregates=self.aggregates, traces=self.traces,
        )

    def artifact(
        self, artifact_id: str, created: str, environment: dict[str, object],
        bundle_id: str | None = None, command: str | None = None,
    ) -> dict[str, object]:
        bundle = self.bundle(bundle_id or f"b_{self.run_id}", created, environment)
        regressions = [
            {**dict(regression),
             "history": [result.as_history_entry(self.run_id, created)]}
            for regression, result in zip(self.resolved.regressions, self.invariants)
        ]
        return build_artifact(
            artifact_id=artifact_id, created=created, bundle=bundle,
            suite=self.resolved.manifest,
            evidence_cases=[], regressions=regressions,
            reproduce={
                "command": command or f"axor-lab run {self.resolved.id}",
                "requires": [],
                "reproducibility": reproducibility_of(bundle, True),
            },
        )


def run_suite(
    manifest: dict[str, object],
    run_id: str,
    suite: BaseSuite | None = None,
    registry: SuiteRegistry | None = None,
    kernel_registry: KernelRegistry | None = None,
    scenario_registry: dict[str, dict[str, object]] | None = None,
    tool_manifests: dict[str, dict[str, object]] | None = None,
) -> SuiteRun:
    """Execute a suite manifest end to end."""
    resolved = resolve_suite(manifest, scenario_registry, tool_manifests)
    if suite is None:
        suite = (registry or builtin_registry()).get(resolved.id)

    conditions = list(resolved.conditions) or [observe_only_condition()]
    run = SuiteRun(run_id=run_id, resolved=resolved, conditions=conditions)

    kernels: dict[tuple[str, str], object] = {}
    execution: dict[str, object] = manifest.get("execution") or {}  # type: ignore[assignment]
    max_steps = int(execution.get("max_steps", 24))  # type: ignore[arg-type]

    order = 0
    for scenario in resolved.scenarios:
        for repeat_index in range(resolved.repeats):
            seed = _seed_for(execution, repeat_index)
            for condition in conditions:
                cache_key = (str(condition["id"]), str(scenario["name"]))
                if cache_key not in kernels:
                    kernels[cache_key] = (
                        resolve_kernel(
                            str(condition["kernel"]), resolved.manifests,
                            condition.get("policy"),
                            kernel_registry or default_registry((str(condition["kernel"]),)),
                            scenario.get("inputs", {}),  # type: ignore[arg-type]
                        )
                        if condition.get("kernel") else None
                    )
                _run_one(run, suite, scenario, condition, kernels[cache_key],
                         seed, repeat_index, order, max_steps)
                order += 1

    run.aggregates = _aggregate(run)
    run.invariants = [
        check_invariant(regression, run.trials, run.traces, {
            str(s["name"]): s for s in resolved.scenarios
        })
        for regression in resolved.regressions
    ]
    return run


def _seed_for(execution: dict[str, object], repeat_index: int) -> str:
    policy = str(execution.get("seed_policy", "per_repeat"))
    if policy == "fixed":
        seeds = list(execution.get("seeds") or [])  # type: ignore[arg-type]
        if seeds:
            return str(seeds[repeat_index % len(seeds)])
    return f"s{repeat_index:03d}"


def _run_one(
    run: SuiteRun,
    suite: BaseSuite,
    scenario: dict[str, object],
    condition: dict[str, object],
    kernel: object,
    seed: str,
    repeat_index: int,
    order: int,
    max_steps: int,
) -> None:
    scenario_id = str(scenario["name"])
    trial_key = trial_id_for(run.run_id, scenario_id, str(condition["id"]), seed, repeat_index)
    base: dict[str, object] = {
        "trial_id": trial_key, "scenario_id": scenario_id,
        "condition_id": str(condition["id"]), "seed": seed,
        "repeat_index": repeat_index, "execution_id": run.run_id,
        "execution_order": order,
    }
    try:
        outcome = run_loop_trial(
            scenario, run.resolved.manifests, condition, kernel,  # type: ignore[arg-type]
            run.run_id, seed, repeat_index,
            suite.program_for(scenario, seed, run.resolved),
            max_steps=max_steps,
        )
    except Exception as exc:  # noqa: BLE001 — one bad trial must not sink the run
        run.trials.append({**base, "status": "failed",
                           "failure_reason": f"{type(exc).__name__}: {exc}"})
        return

    # platform metrics first, then the suite's — a suite may ADD metrics but
    # must not overwrite what the runtime measured about its own execution.
    # task_success / ASR are recorded as metrics rather than kept on the side:
    # the scenario's own typed predicates ARE this run's evaluation, so putting
    # their results where every other metric lives means an aggregation and an
    # invariant read them the same way, from one place.
    outcome_metrics: dict[str, object] = {"task_success": outcome.task_success}
    if scenario.get("violation") is not None:
        outcome_metrics["ASR"] = outcome.violation
    metrics = {**suite.metrics_for(outcome, scenario), **outcome_metrics, **outcome.metrics}
    trace_ref = content_hash(outcome.trace)
    record = {**base, "status": "completed", "trace_ref": trace_ref, "metrics": metrics}
    if condition.get("kernel"):
        from lab_contracts.canonical import CONFIG_COMPILER_VERSION, runtime_config_hash

        record.update({
            "runtime_config_hash": runtime_config_hash(
                str(condition["kernel"]), condition.get("policy"),
                list(run.resolved.manifests.values()), scenario.get("inputs", {}),  # type: ignore[arg-type]
            ),
            "config_compiler_version": CONFIG_COMPILER_VERSION,
            "runtime_provenance": "recorded_at_execution",
            "resolved_kernel_fingerprint": str(
                getattr(kernel, "behavior_version", str(getattr(kernel, "version", "")))
            ),
        })
    run.trials.append(record)
    run.traces[trace_ref] = outcome.trace
    run.outcomes[trial_key] = outcome


def _aggregate(run: SuiteRun) -> list[dict[str, object]]:
    """Compute exactly the aggregations the suite declared — no more."""
    aggregates: list[dict[str, object]] = []
    by_trial = {str(t["trial_id"]): t for t in run.trials if t.get("status") == "completed"}
    for spec in run.resolved.aggregations:
        metric = str(spec["metric"])
        fn = str(spec["fn"])
        unit = str(spec.get("unit_of_analysis", "trial"))
        for condition in run.conditions:
            cid = str(condition["id"])
            trials = [t for t in by_trial.values() if str(t["condition_id"]) == cid]
            if not trials:
                continue
            if fn == "rate":
                aggregate = _rate(run, metric, cid, trials, unit, spec)
                if aggregate is not None:
                    aggregates.append(aggregate)
                continue
            values = [
                float(t["metrics"][metric])  # type: ignore[index,arg-type]
                for t in trials
                if isinstance((t.get("metrics") or {}).get(metric), (int, float))  # type: ignore[union-attr]
                and not isinstance((t.get("metrics") or {}).get(metric), bool)  # type: ignore[union-attr]
            ]
            if not values or fn not in _STATS_AGGREGATORS:
                # an aggregation over a metric nobody measured produces NOTHING,
                # not a zero — the Run Report shows a gap, which is the truth
                continue
            aggregates.append({
                "metric": metric, "condition_id": cid,
                "estimate": float(_STATS_AGGREGATORS[fn](values)),
                # a point summary of a continuous metric carries no interval;
                # naming the method 'none' is honest, inventing a CI is not
                "interval": {"method": "none", "low": float(min(values)),
                             "high": float(max(values))},
                "n": len(values), "unit_of_analysis": unit,
            })
    return aggregates


def _rate(
    run: SuiteRun, metric: str, condition_id: str, trials: list[dict[str, object]],
    unit: str, spec: dict[str, object],
) -> dict[str, object] | None:
    """A binary rate with a Wilson interval — the honest estimator for a
    proportion (statistics.md)."""
    successes = 0
    n = 0
    for trial in trials:
        outcome = run.outcomes.get(str(trial["trial_id"]))
        if outcome is None:
            continue
        value = _binary_value(metric, outcome, trial)
        if value is None:
            continue
        n += 1
        successes += int(value)
    if n == 0:
        return None
    return binary_aggregate(metric, condition_id, successes, n, unit_of_analysis=unit)


def _binary_value(
    metric: str, outcome: LoopOutcome, trial: dict[str, object]
) -> bool | None:
    """A rate is only computable over a BOOLEAN metric the trial recorded.

    No special-casing by name: if a suite asks for a rate over something that
    was never recorded as a boolean, the answer is None and no aggregate is
    emitted. Substituting a value here — "well, `ASR` probably means the
    violation predicate" — is how an aggregate ends up describing something the
    suite did not ask to measure.
    """
    raw = (trial.get("metrics") or {}).get(metric)  # type: ignore[union-attr]
    return raw if isinstance(raw, bool) else None
