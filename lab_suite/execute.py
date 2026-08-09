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

from lab_analysis import binary_aggregate, mcnemar_test, two_proportion_test
from lab_contracts import build_artifact, build_bundle, content_hash, reproducibility_of
from lab_capabilities.governance import gate_for_condition
from lab_runner.invariants import InvariantResult, check_invariant
from lab_capabilities.governance.kernel import KernelRegistry
from lab_runner.loop import LoopOutcome, run_loop_trial
from lab_capabilities.governance import observe_only_condition
from lab_runner.trials import trial_id_for

from .manifest import ResolvedSuite, declared_evaluators, resolve_suite
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
    # what the suite's own extractors produced, per trial, during the run
    evidence_cases: list[dict[str, object]] = field(default_factory=list)
    pinned: list[dict[str, object]] = field(default_factory=list)
    # set when a run-wide cost ceiling stopped the run early; the partial result
    # flows through missingness and analysis honestly rather than looking complete

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
        # the suite's OWN declaration, not a hardcoded True: a suite that ships
        # `artifact.include_traces: false` produces a metrics-only artifact, and
        # claiming exact_replay over a body that carries no traces is a promise
        # nothing can keep. The flag was read by nothing before.
        artifact_cfg: dict[str, object] = self.resolved.manifest.get("artifact") or {}  # type: ignore[assignment]
        traces_included = bool(artifact_cfg.get("include_traces", True))
        return build_artifact(
            artifact_id=artifact_id, created=created, bundle=bundle,
            suite=self.resolved.manifest,
            evidence_cases=list(self.evidence_cases),
            regressions=regressions + list(self.pinned),
            reproduce={
                # `axor-lab run` takes an .axl file; a suite is reproduced with
                # `run-suite <id>`, which is an actual command form that exists.
                # The old default named a command that errored on every artifact.
                "command": command or f"axor-lab run-suite {self.resolved.id}",
                "requires": [],
                "reproducibility": reproducibility_of(bundle, traces_included),
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
    """Execute a suite manifest end to end.

    Omitted, each suite falls back to its scripted stand-in — which exercises
    the pipeline but decides the agent's behaviour in advance, so it cannot
    answer what a model would actually do.
    """
    resolved = resolve_suite(manifest, scenario_registry, tool_manifests)
    if suite is None:
        suite = (registry or builtin_registry()).get(resolved.id)

    # One default everywhere: UNGOVERNED — the kernel observes, enforcement is
    # off. Locally that is the reference kernel, which is stdlib and always
    # resolvable; a kernel-free arm would forfeit the value ledger, the recorded
    # verdicts and exact replay, and buy nothing.
    conditions = list(resolved.conditions) or [observe_only_condition()]
    run = SuiteRun(run_id=run_id, resolved=resolved, conditions=conditions)

    gates: dict[tuple[str, str], object] = {}
    execution: dict[str, object] = manifest.get("execution") or {}  # type: ignore[assignment]
    max_steps = int(execution.get("max_steps", 24))  # type: ignore[arg-type]

    # the FULL plan is materialized up front so a cost stop can record the
    # trials that never ran. Otherwise missingness is computed over only the
    # trials that DID run and reports n=1/1 for a plan of 100 stopped after one.
    plan = [
        (scenario, condition, repeat_index)
        for scenario in resolved.scenarios
        for repeat_index in range(resolved.repeats)
        for condition in conditions
    ]
    for order, (scenario, condition, repeat_index) in enumerate(plan):
        seed = _seed_for(execution, repeat_index)
        cache_key = (str(condition["id"]), str(scenario["name"]))
        if cache_key not in gates:
            # keyed by (condition, scenario): an input-backed allowlist resolves
            # against THIS scenario's inputs, so two scenarios under one
            # condition must not share a gate with a stale expansion
            gates[cache_key] = gate_for_condition(
                condition, resolved.manifests,
                scenario.get("inputs", {}),  # type: ignore[arg-type]
                kernel_registry,
            )
        _run_one(run, suite, scenario, condition, gates[cache_key],
                 seed, repeat_index, order, max_steps)

    run.aggregates = _aggregate(run)
    scenarios_by_id = {str(s["name"]): s for s in resolved.scenarios}
    evaluators = declared_evaluators(resolved.manifest)
    run.invariants = [
        check_invariant(regression, run.trials, run.traces, scenarios_by_id, evaluators)
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
    gate: object,
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
            scenario, run.resolved.manifests, condition, gate,  # type: ignore[arg-type]
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
            "resolved_kernel_fingerprint": _fingerprint(gate),
        })
    run.trials.append(record)
    run.traces[trace_ref] = outcome.trace
    run.outcomes[trial_key] = outcome

    # What the SUITE curates from this trial. Both hooks default to nothing —
    # curation is a human act unless a suite knows better — and a suite that
    # does know is the only thing that can turn a recorded trial into an
    # investigation without a person reading it.
    run.evidence_cases.extend(suite.evidence_for(outcome, record, scenario))
    run.pinned.extend(suite.regressions_for(outcome, record, scenario))


def _fingerprint(gate: object) -> str:
    """The ACTUAL resolved backend's behaviour identity, not the declared
    condition.kernel string — so a registry returning a behaviour-changed kernel
    under a version label stays auditable (review r20)."""
    kernel = getattr(gate, "kernel", None)
    if kernel is None:
        return ""
    return str(getattr(kernel, "behavior_version", getattr(kernel, "version", "")))


def _aggregate(run: SuiteRun) -> list[dict[str, object]]:
    """Compute exactly the aggregations the suite declared — no more."""
    aggregates: list[dict[str, object]] = []
    by_trial = {str(t["trial_id"]): t for t in run.trials if t.get("status") == "completed"}
    # the observe-only / ungoverned arm is the comparison baseline; a declared
    # `test` on a treated arm compares against it.
    baseline = next(
        (str(c["id"]) for c in run.conditions if str(c.get("enforcement", "")) == "off"),
        None,
    )
    for spec in run.resolved.aggregations:
        metric = str(spec["metric"])
        fn = str(spec["fn"])
        unit = str(spec.get("unit_of_analysis", "trial"))
        test_kind = str(spec.get("test", "none"))
        for condition in run.conditions:
            cid = str(condition["id"])
            trials = [t for t in by_trial.values() if str(t["condition_id"]) == cid]
            if not trials:
                continue
            if fn == "rate":
                # a declared comparison test on a treated arm: pair against the
                # baseline and attach the test payload. Validator already
                # guarantees >=2 conditions when a test is declared (manifest.py),
                # so a missing baseline here means every arm enforces — no
                # observe-only reference to compare to, so no test, not a crash.
                test = None
                if (test_kind in ("mcnemar", "two_proportion")
                        and baseline is not None and cid != baseline):
                    test = _comparison_test(
                        test_kind, metric, baseline, cid, by_trial.values(),
                    )
                aggregate = _rate(metric, cid, trials, unit, test=test)
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
    metric: str, condition_id: str, trials: list[dict[str, object]], unit: str,
    test: dict[str, object] | None = None,
) -> dict[str, object] | None:
    """A binary rate with a Wilson interval — the honest estimator for a
    proportion (statistics.md).

    Reads the TRIAL, not a live in-memory outcome: a run collected from a
    connected runtime has trials and traces but no local outcome objects, and
    requiring one silently produced zero aggregates for every remote run.
    """
    successes = 0
    n = 0
    for trial in trials:
        value = _binary_value(metric, trial)
        if value is None:
            continue
        n += 1
        successes += int(value)
    if n == 0:
        return None
    return binary_aggregate(metric, condition_id, successes, n, unit_of_analysis=unit, test=test)


def _comparison_test(
    kind: str, metric: str, baseline: str, treated: str,
    completed_trials: "object",
) -> dict[str, object] | None:
    """The paired/independent test payload comparing `treated` against `baseline`.

    mcnemar needs matched pairs — the same (scenario, repeat) under both arms,
    which the seed policy guarantees for a deterministic agent. A pair is kept
    only when BOTH arms recorded the boolean; a half-measured pair is dropped
    rather than guessed.
    """
    trials = list(completed_trials)
    def _index(cid: str) -> dict[tuple[str, object], bool]:
        out: dict[tuple[str, object], bool] = {}
        for t in trials:
            if str(t["condition_id"]) != cid:
                continue
            value = _binary_value(metric, t)
            if value is not None:
                out[(str(t["scenario_id"]), t.get("repeat_index"))] = value
        return out

    base_by_key, treated_by_key = _index(baseline), _index(treated)
    shared = sorted(base_by_key.keys() & treated_by_key.keys(), key=lambda k: (k[0], str(k[1])))
    if not shared:
        return None
    if kind == "mcnemar":
        pairs = [(base_by_key[k], treated_by_key[k]) for k in shared]
        return mcnemar_test(pairs, vs=baseline)
    base_succ = sum(int(base_by_key[k]) for k in base_by_key)
    treated_succ = sum(int(treated_by_key[k]) for k in treated_by_key)
    return two_proportion_test(
        base_succ, len(base_by_key), treated_succ, len(treated_by_key), vs=baseline,
    )


def _binary_value(metric: str, trial: dict[str, object]) -> bool | None:
    """A rate is only computable over a BOOLEAN metric the trial recorded.

    No special-casing by name: if a suite asks for a rate over something that
    was never recorded as a boolean, the answer is None and no aggregate is
    emitted. Substituting a value here — "well, `ASR` probably means the
    violation predicate" — is how an aggregate ends up describing something the
    suite did not ask to measure.
    """
    raw = (trial.get("metrics") or {}).get(metric)  # type: ignore[union-attr]
    return raw if isinstance(raw, bool) else None
