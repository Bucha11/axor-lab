"""The statistical contract (contracts/statistics.md) as code.

Every aggregate carries {metric, estimate, interval{method,low,high}, n,
unit_of_analysis, test} computed HERE at run time — the UI renders stored
fields verbatim; there is no render-time code path that derives a number.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

from .errors import InsufficientDataError, UnitOfAnalysisError

WILSON_Z_95 = 1.959963984540054
INCONCLUSIVE_MIN_N = 10
UNIT_TRIAL = "trial"
UNIT_RUN = "run"
_VALID_UNITS = frozenset({UNIT_TRIAL, UNIT_RUN})
DEFAULT_BOOTSTRAP_RESAMPLES = 2000
_BIAS_CONCENTRATION_SHARE = 0.75


def wilson_interval(successes: int, n: int, z: float = WILSON_Z_95) -> tuple[float, float]:
    """Wilson score 95% interval — well-behaved near 0/1, where ASR lives."""
    if n <= 0:
        raise InsufficientDataError("n must be positive")
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value over the discordant pairs (binomial).

    Requires the stored pairing (b = baseline-success/treated-fail,
    c = baseline-fail/treated-success) — two marginal proportions are
    insufficient, which is why the bundle stores discordant counts.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def paired_bootstrap_ci(
    values: Sequence[float],
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = 0,
) -> tuple[float, float]:
    """Bootstrap 95% CI of the mean, resampling RUNS (never rounds)."""
    if not values:
        raise InsufficientDataError("no values")
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples)
    )
    low_idx = int(0.025 * resamples)
    high_idx = min(resamples - 1, int(0.975 * resamples))
    return means[low_idx], means[high_idx]


def binary_aggregate(
    metric: str,
    condition_id: str,
    successes: int,
    n: int,
    unit_of_analysis: str = UNIT_TRIAL,
    test: dict[str, object] | None = None,
) -> dict[str, object]:
    """A bundle/v1 aggregate for a binary per-trial outcome (ASR, utility)."""
    if unit_of_analysis not in _VALID_UNITS:
        raise UnitOfAnalysisError(
            f"unit_of_analysis must be one of {sorted(_VALID_UNITS)}, got {unit_of_analysis!r} "
            "(rounds within a run are serially correlated, not independent observations)"
        )
    low, high = wilson_interval(successes, n)
    aggregate: dict[str, object] = {
        "metric": metric,
        "condition_id": condition_id,
        "estimate": successes / n,
        "interval": {"method": "wilson", "low": low, "high": high},
        "n": n,
        "unit_of_analysis": unit_of_analysis,
    }
    if test is not None and not is_inconclusive(aggregate):
        aggregate["test"] = test
    return aggregate


def mcnemar_test(pairs: Sequence[tuple[bool, bool]], vs: str) -> dict[str, object]:
    """The paired test payload stored in an aggregate. ``pairs`` are
    (baseline_outcome, treated_outcome) per trial, same scenario/seed."""
    b = sum(1 for base, treated in pairs if base and not treated)
    c = sum(1 for base, treated in pairs if not base and treated)
    return {
        "name": "mcnemar",
        "vs": vs,
        "discordant": {"b": b, "c": c},
        "p": mcnemar_exact(b, c),
    }


def is_inconclusive(aggregate: dict[str, object]) -> bool:
    """n < 10 → 'inconclusive — raise repeats'; significance suppressed."""
    return int(aggregate["n"]) < INCONCLUSIVE_MIN_N  # type: ignore[arg-type]


@dataclass(frozen=True)
class MissingnessSummary:
    """Denominator honesty: every result reports n_completed/n_total and why."""

    n_total: int
    n_completed: int
    n_missing: int
    reasons: tuple[tuple[str, int], ...]
    potentially_biased: bool

    def display(self) -> str:
        parts = [f"n={self.n_completed}/{self.n_total}"]
        if self.n_missing:
            reasons = ", ".join(f"{count} {reason}" for reason, count in self.reasons)
            parts.append(f"{self.n_missing} excluded: {reasons}")
        if self.potentially_biased:
            parts.append("flagged potentially biased (missingness is non-random)")
        return "; ".join(parts)


def missingness(trials: Sequence[dict[str, object]]) -> MissingnessSummary:
    """Summarize failed/excluded trials; flag plausibly NON-random missingness
    (failures concentrated on one scenario) instead of silently computing
    over survivors."""
    total = len(trials)
    missing = [t for t in trials if t["status"] != "completed"]
    reasons: dict[str, int] = {}
    by_scenario: dict[str, int] = {}
    for trial in missing:
        reason = str(trial.get("failure_reason", "unspecified"))
        reasons[reason] = reasons.get(reason, 0) + 1
        scenario = str(trial["scenario_id"])
        by_scenario[scenario] = by_scenario.get(scenario, 0) + 1
    scenario_count = len({str(t["scenario_id"]) for t in trials})
    concentrated = bool(
        missing
        and scenario_count > 1
        and max(by_scenario.values()) / len(missing) >= _BIAS_CONCENTRATION_SHARE
    )
    return MissingnessSummary(
        n_total=total,
        n_completed=total - len(missing),
        n_missing=len(missing),
        reasons=tuple(sorted(reasons.items())),
        potentially_biased=concentrated,
    )
