"""Executable invariants — regression/v1 evaluated over a run.

Suite Platform RFC §9: a regression is an executable invariant derived from a
trial. `latency < threshold`, `budget <= limit`, `task_success == true`,
`forbidden_tool_calls == 0`, a gate verdict sequence and a custom evaluator
outcome are all the same kind of object, differing only in their `rule`.

Two rules run here today:

  - ``metric_threshold`` — a bound on a per-trial metric (bundle/v1
    trial.metrics), either per trial or over an aggregate across trials;
  - ``predicate`` — a typed predicate/v1 over the trial's trace, reusing the
    evaluator that already backs scenario `violation` / `task_success`.

``verdict_sequence`` stays in `regression.py` (it needs kernel resolution and
replay), and ``evaluator_outcome`` waits on the Suite SDK. Both are reported as
``skipped`` here rather than silently passing.

The cardinal rule: **an invariant that could not be evaluated is `error`, never
`passed`.** A missing metric, an unparseable rule or a trial with no trace are
all states of not-knowing, and not-knowing is not the same as knowing the
invariant held. A budget invariant that silently passes because nothing measured
the cost is worse than no invariant at all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

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

# rule kinds this module knows about but cannot run yet — reported, not skipped
# silently, so a suite never believes an unrun invariant held
_ELSEWHERE = {
    "verdict_sequence": "needs kernel resolution + replay (lab_runner.regression)",
    "evaluator_outcome": "needs the Suite SDK evaluator registry",
}


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
) -> InvariantResult:
    """Evaluate one regression/v1 over a run's completed trials.

    `traces` is keyed by trace_ref (as bundle trials cite them). Only COMPLETED
    trials are considered: a failed trial is a missingness fact, not evidence
    that an invariant broke.
    """
    rid = str(regression.get("id", "?"))
    rule: dict[str, object] = regression.get("rule") or {}  # type: ignore[assignment]
    kind = str(rule.get("kind", ""))

    if kind in _ELSEWHERE:
        return InvariantResult(rid, STATUS_SKIPPED, detail=f"{kind}: {_ELSEWHERE[kind]}")
    if kind not in ("metric_threshold", "predicate"):
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


def check_invariants(
    regressions: list[dict[str, object]],
    trials: list[dict[str, object]],
    traces: dict[str, dict[str, object]] | None = None,
    scenarios: dict[str, dict[str, object]] | None = None,
) -> list[InvariantResult]:
    return [check_invariant(r, trials, traces, scenarios) for r in regressions]
