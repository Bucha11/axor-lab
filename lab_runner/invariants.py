"""Executable invariants — regression/v1 evaluated over a run.

Suite Platform RFC §9: a regression is an executable invariant derived from a
trial. `latency < threshold`, `budget <= limit`, `task_success == true`,
`forbidden_tool_calls == 0`, a gate verdict sequence and a custom evaluator
outcome are all the same kind of object, differing only in their `rule`.

Three of the four rule kinds run here:

  - ``metric_threshold`` — a bound on a per-trial metric (bundle/v1
    trial.metrics), either per trial or over an aggregate across trials;
  - ``predicate`` — a typed predicate/v1 over the trial's trace, reusing the
    evaluator that already backs scenario `violation` / `task_success`;
  - ``evaluator_outcome`` — the result of an evaluator the SUITE declared
    (`suite/v1 evaluation.evaluators`), compared to `expect` by canonical
    equality.

``verdict_sequence`` stays in the governance capability — it needs kernel
resolution and replay — and is reported as ``skipped`` here rather than
silently passing.

The cardinal rule: **an invariant that could not be evaluated is `error`, never
`passed`.** A missing metric, an unparseable rule or a trial with no trace are
all states of not-knowing, and not-knowing is not the same as knowing the
invariant held. A budget invariant that silently passes because nothing measured
the cost is worse than no invariant at all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from lab_contracts import content_hash

from .predicates import evaluate

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"

_OPS = {
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}

_AGGREGATORS = {
    "mean": statistics.fmean,
    "median": statistics.median,
    "sum": sum,
    "min": min,
    "max": max,
}

# rule kinds this module knows about but does not run — reported, not skipped
# silently, so a suite never believes an unrun invariant held
_ELSEWHERE = {
    "verdict_sequence": "needs kernel resolution + replay (lab_capabilities.governance.regression)",
}

_RUNS_HERE = ("metric_threshold", "predicate", "evaluator_outcome")


@dataclass(frozen=True)
class InvariantResult:
    """One evaluation of one regression against one run — a regression/v1
    `history` entry, plus the detail a UI needs to explain it."""

    regression_id: str
    status: str
    detail: str = ""
    observed: object = None
    trials_checked: int = 0
    trials_failed: int = 0
    failing_trial_ids: tuple[str, ...] = field(default_factory=tuple)

    def as_history_entry(self, run_id: str, at: str) -> dict[str, object]:
        entry: dict[str, object] = {
            "run_id": run_id, "at": at, "status": self.status,
            "trials_checked": self.trials_checked, "trials_failed": self.trials_failed,
        }
        if self.detail:
            entry["detail"] = self.detail
        if self.observed is not None:
            entry["observed"] = self.observed
        return entry


def in_scope(regression: dict[str, object], trial: dict[str, object]) -> bool:
    """Whether an invariant applies to a trial. No scope = every trial."""
    scope: dict[str, object] = regression.get("scope") or {}  # type: ignore[assignment]
    scenarios = scope.get("scenario_ids")
    if scenarios and str(trial.get("scenario_id")) not in scenarios:  # type: ignore[operator]
        return False
    conditions = scope.get("condition_ids")
    if conditions and str(trial.get("condition_id")) not in conditions:  # type: ignore[operator]
        return False
    return True


def check_invariant(
    regression: dict[str, object],
    trials: list[dict[str, object]],
    traces: dict[str, dict[str, object]] | None = None,
    scenarios: dict[str, dict[str, object]] | None = None,
    evaluators: dict[str, dict[str, object]] | None = None,
) -> InvariantResult:
    """Evaluate one regression/v1 over a run's completed trials.

    `traces` is keyed by trace_ref (as bundle trials cite them). Only COMPLETED
    trials are considered: a failed trial is a missingness fact, not evidence
    that an invariant broke.

    `evaluators` is the suite's declared evaluator table, keyed by id, needed
    only by an `evaluator_outcome` rule. Absent, such a rule is an `error`
    naming the evaluator it could not resolve — never a pass.
    """
    rid = str(regression.get("id", "?"))
    rule: dict[str, object] = regression.get("rule") or {}  # type: ignore[assignment]
    kind = str(rule.get("kind", ""))

    if kind in _ELSEWHERE:
        return InvariantResult(rid, STATUS_SKIPPED, detail=f"{kind}: {_ELSEWHERE[kind]}")
    if kind not in _RUNS_HERE:
        return InvariantResult(rid, STATUS_ERROR, detail=f"unknown rule kind {kind!r}")

    scoped = [
        t for t in trials
        if str(t.get("status")) == "completed" and in_scope(regression, t)
    ]
    if not scoped:
        return InvariantResult(
            rid, STATUS_ERROR,
            detail="no completed trial in scope — the invariant was never evaluated",
        )

    if kind == "metric_threshold":
        return _check_metric_threshold(rid, regression, rule, scoped)
    if kind == "evaluator_outcome":
        return _check_evaluator_outcome(
            rid, regression, rule, scoped, traces or {}, scenarios or {},
            evaluators or {},
        )
    return _check_predicate(rid, regression, rule, scoped, traces or {}, scenarios or {})


def _quantifier(regression: dict[str, object]) -> str:
    scope: dict[str, object] = regression.get("scope") or {}  # type: ignore[assignment]
    return str(scope.get("quantifier", "all"))


def _verdict(
    rid: str,
    holds: list[tuple[str, bool, object]],
    quantifier: str,
    describe: str,
) -> InvariantResult:
    failing = [(tid, obs) for tid, ok, obs in holds if not ok]
    checked = len(holds)
    if quantifier == "any":
        passed = any(ok for _, ok, _ in holds)
        detail = describe if passed else f"{describe} held for no trial"
    else:
        passed = not failing
        detail = describe if passed else f"{describe} violated by {len(failing)}/{checked} trial(s)"
    return InvariantResult(
        rid,
        STATUS_PASSED if passed else STATUS_FAILED,
        detail=detail,
        observed=(failing[0][1] if failing else None),
        trials_checked=checked,
        trials_failed=len(failing),
        failing_trial_ids=tuple(tid for tid, _ in failing),
    )


def _check_metric_threshold(
    rid: str,
    regression: dict[str, object],
    rule: dict[str, object],
    scoped: list[dict[str, object]],
) -> InvariantResult:
    metric = str(rule.get("metric", ""))
    op_name = str(rule.get("op", ""))
    op = _OPS.get(op_name)
    if op is None:
        return InvariantResult(rid, STATUS_ERROR, detail=f"unknown op {op_name!r}")
    threshold = rule.get("value")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        return InvariantResult(rid, STATUS_ERROR, detail="rule.value must be a number")
    aggregate = str(rule.get("aggregate", "value"))

    # Collect the measurements, keeping track of which trials had none. An
    # ABSENT metric is the case that must not silently pass: a trial that never
    # measured its cost is not a trial that cost nothing.
    measured: list[tuple[str, float]] = []
    unmeasured: list[str] = []
    for trial in scoped:
        metrics: dict[str, object] = trial.get("metrics") or {}  # type: ignore[assignment]
        trial_id = str(trial.get("trial_id"))
        raw = metrics.get(metric)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            measured.append((trial_id, float(raw)))
        else:
            unmeasured.append(trial_id)

    if unmeasured:
        return InvariantResult(
            rid, STATUS_ERROR,
            detail=(
                f"metric {metric!r} was not measured on {len(unmeasured)}/{len(scoped)} "
                f"completed trial(s) — an unmeasured value cannot satisfy a threshold"
            ),
            trials_checked=len(scoped),
        )

    if aggregate == "value":
        holds = [(tid, bool(op(v, threshold)), v) for tid, v in measured]
        return _verdict(rid, holds, _quantifier(regression), f"{metric} {op_name} {threshold}")

    values = [v for _, v in measured]
    try:
        observed = _aggregate(aggregate, values)
    except KeyError:
        return InvariantResult(rid, STATUS_ERROR, detail=f"unknown aggregate {aggregate!r}")
    passed = bool(op(observed, threshold))
    return InvariantResult(
        rid,
        STATUS_PASSED if passed else STATUS_FAILED,
        detail=f"{aggregate}({metric}) = {observed}, want {op_name} {threshold}",
        observed=observed,
        trials_checked=len(measured),
        trials_failed=0 if passed else len(measured),
    )


def _aggregate(name: str, values: list[float]) -> float:
    if name in _AGGREGATORS:
        return float(_AGGREGATORS[name](values))
    if name.startswith("p"):
        return _percentile(values, float(name[1:]))
    raise KeyError(name)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    Deliberately not an interpolating percentile: the reported p95 is then a
    latency some trial ACTUALLY exhibited, so a failing invariant can always be
    traced to a real trial rather than to a number no run ever produced.
    """
    if not values:
        raise ValueError("percentile of no values")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct / 100 * len(ordered)) // 1)))
    return ordered[rank - 1]


def _check_predicate(
    rid: str,
    regression: dict[str, object],
    rule: dict[str, object],
    scoped: list[dict[str, object]],
    traces: dict[str, dict[str, object]],
    scenarios: dict[str, dict[str, object]],
) -> InvariantResult:
    predicate = rule.get("predicate")
    if not isinstance(predicate, dict):
        return InvariantResult(rid, STATUS_ERROR, detail="rule.predicate is missing")
    expect = bool(rule.get("expect", True))

    holds: list[tuple[str, bool, object]] = []
    for trial in scoped:
        trial_id = str(trial.get("trial_id"))
        trace = traces.get(str(trial.get("trace_ref")))
        if trace is None:
            return InvariantResult(
                rid, STATUS_ERROR,
                detail=f"trial {trial_id} cites trace_ref {trial.get('trace_ref')!r} "
                       "which is not in the run — the invariant cannot be evaluated",
                trials_checked=len(scoped),
            )
        # each trial is evaluated under ITS OWN scenario's inputs, so an
        # $inputs-referencing predicate is never resolved against the wrong
        # scenario in a multi-scenario run
        scenario = scenarios.get(str(trial.get("scenario_id"))) or {}
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        try:
            actual = evaluate(predicate, trace, inputs)
        except Exception as exc:  # noqa: BLE001 — an unevaluable rule is `error`, not `failed`
            return InvariantResult(
                rid, STATUS_ERROR,
                detail=f"predicate could not be evaluated on trial {trial_id}: "
                       f"{type(exc).__name__}: {exc}",
                trials_checked=len(scoped),
            )
        holds.append((trial_id, actual == expect, actual))

    return _verdict(rid, holds, _quantifier(regression), f"predicate == {expect}")


def _evaluator_value(
    declaration: dict[str, object],
    trial: dict[str, object],
    traces: dict[str, dict[str, object]],
    scenarios: dict[str, dict[str, object]],
) -> tuple[object, str]:
    """The value one declared evaluator produced for one trial.

    Returns (value, error). A NON-EMPTY error means the evaluator could not be
    resolved at all, which is `error` upstream — never a comparison against a
    default.

    `suite_hook` reads the produced name straight off `trial.metrics`: the SDK's
    `metrics_for` hook is where a suite's own code lands its results, so an
    evaluator backed by a hook is already recorded there. Resolving it any other
    way would mean running the suite's code a second time, on a trial that has
    already finished, and possibly disagreeing with what the artifact records.
    """
    kind = str(declaration.get("kind", ""))
    produces = str(declaration.get("produces", ""))
    trial_id = str(trial.get("trial_id"))
    metrics: dict[str, object] = trial.get("metrics") or {}  # type: ignore[assignment]

    if kind in ("trial_metric", "suite_hook"):
        key = str(declaration.get("metric") or produces)
        if key not in metrics:
            source = "trial.metrics" if kind == "trial_metric" else "the suite hook"
            return None, (
                f"evaluator {declaration.get('id')!r} reads {key!r} from {source}, "
                f"which trial {trial_id} did not record — an unmeasured value "
                "cannot be compared to an expectation"
            )
        return metrics[key], ""

    if kind == "predicate":
        predicate = declaration.get("predicate")
        if not isinstance(predicate, dict):
            return None, (
                f"evaluator {declaration.get('id')!r} declares kind=predicate "
                "with no predicate"
            )
        trace = traces.get(str(trial.get("trace_ref")))
        if trace is None:
            return None, (
                f"trial {trial_id} cites trace_ref {trial.get('trace_ref')!r} "
                "which is not in the run"
            )
        scenario = scenarios.get(str(trial.get("scenario_id"))) or {}
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        try:
            return evaluate(predicate, trace, inputs), ""
        except Exception as exc:  # noqa: BLE001 — unevaluable is `error`, not `failed`
            return None, (
                f"evaluator {declaration.get('id')!r} could not be evaluated on "
                f"trial {trial_id}: {type(exc).__name__}: {exc}"
            )

    return None, f"evaluator {declaration.get('id')!r} has unknown kind {kind!r}"


def _check_evaluator_outcome(
    rid: str,
    regression: dict[str, object],
    rule: dict[str, object],
    scoped: list[dict[str, object]],
    traces: dict[str, dict[str, object]],
    scenarios: dict[str, dict[str, object]],
    evaluators: dict[str, dict[str, object]],
) -> InvariantResult:
    """An invariant over a suite-declared evaluator's result.

    This kind was declared in `regression/v1` from the start and never ran: it
    reported `skipped`, which is honest but means a suite could pin its own
    evaluator's outcome and get no answer forever.
    """
    name = str(rule.get("evaluator", ""))
    declaration = evaluators.get(name)
    if declaration is None:
        return InvariantResult(
            rid, STATUS_ERROR,
            detail=(
                f"evaluator {name!r} is not declared by the suite "
                f"(declared: {sorted(evaluators)}) — the invariant names "
                "something that does not exist"
            ),
        )
    expect = rule.get("expect", True)
    expected_hash = content_hash(expect)

    holds: list[tuple[str, bool, object]] = []
    for trial in scoped:
        value, error = _evaluator_value(declaration, trial, traces, scenarios)
        if error:
            return InvariantResult(
                rid, STATUS_ERROR, detail=error, trials_checked=len(scoped),
            )
        # canonical equality (regression/v1: "compared by canonical equality"),
        # so `1` and `1.0` and `True` are not conflated the way `==` conflates
        # them in Python
        holds.append((str(trial.get("trial_id")), content_hash(value) == expected_hash, value))

    return _verdict(
        rid, holds, _quantifier(regression), f"evaluator {name} == {expect!r}",
    )


def check_invariants(
    regressions: list[dict[str, object]],
    trials: list[dict[str, object]],
    traces: dict[str, dict[str, object]] | None = None,
    scenarios: dict[str, dict[str, object]] | None = None,
) -> list[InvariantResult]:
    return [check_invariant(r, trials, traces, scenarios) for r in regressions]
