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
    if successes < 0 or successes > n:
        raise InsufficientDataError(f"successes {successes} must be in [0, {n}]")
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
    if b < 0 or c < 0:
        raise InsufficientDataError(f"discordant counts must be non-negative, got b={b} c={c}")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def two_proportion_test(
    baseline_successes: int, baseline_n: int,
    treated_successes: int, treated_n: int, vs: str,
) -> dict[str, object]:
    """Unpaired comparison of two INDEPENDENT proportions (review r4).

    For live-model runs the two conditions are independently sampled — there is
    no matched pair — so McNemar's paired test does not apply. This reports the
    difference (baseline − treated), a Newcombe score interval for that
    difference (well-behaved near 0/1), and a two-sided two-proportion z-test
    p-value. It is named `two_proportion`, never `mcnemar`, so a reader is never
    told an independent-samples comparison was paired."""
    if baseline_n <= 0 or treated_n <= 0:
        raise InsufficientDataError("both arms need n > 0")
    for s, n in ((baseline_successes, baseline_n), (treated_successes, treated_n)):
        if s < 0 or s > n:
            raise InsufficientDataError(f"successes {s} must be in [0, {n}]")
    p_b, p_t = baseline_successes / baseline_n, treated_successes / treated_n
    diff = p_b - p_t
    # Newcombe method-10 interval for the difference of two independent proportions
    l1, u1 = wilson_interval(baseline_successes, baseline_n)
    l2, u2 = wilson_interval(treated_successes, treated_n)
    low = diff - math.sqrt((p_b - l1) ** 2 + (u2 - p_t) ** 2)
    high = diff + math.sqrt((u1 - p_b) ** 2 + (p_t - l2) ** 2)
    # pooled two-proportion z-test, two-sided
    pooled = (baseline_successes + treated_successes) / (baseline_n + treated_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / baseline_n + 1 / treated_n))
    if se == 0:
        p_value = 1.0
    else:
        z = diff / se
        p_value = math.erfc(abs(z) / math.sqrt(2))  # 2 * (1 - Phi(|z|))
    effective_n = min(baseline_n, treated_n)
    status, reason = _test_status(effective_n)
    test: dict[str, object] = {
        "name": "two_proportion",
        "vs": vs,
        "difference": diff,
        "interval": {"method": "newcombe", "low": max(-1.0, low), "high": min(1.0, high)},
        "p": min(1.0, p_value),
        "design": "independent_samples",
        "effective_n": effective_n,
        "status": status,
    }
    if reason:
        test["reason"] = reason
    return test


def paired_bootstrap_ci(
    values: Sequence[float],
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = 0,
) -> tuple[float, float]:
    """Bootstrap 95% CI of the mean, resampling RUNS (never rounds)."""
    if not values:
        raise InsufficientDataError("no values")
    if resamples <= 0:
        raise InsufficientDataError("resamples must be positive")
    if any(not math.isfinite(v) for v in values):
        raise InsufficientDataError("values must be finite (no NaN/Infinity)")
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
    comparison_design: str | None = None,
) -> dict[str, object]:
    """A bundle/v1 aggregate for a binary per-trial outcome (ASR, utility)."""
    if unit_of_analysis not in _VALID_UNITS:
        raise UnitOfAnalysisError(
            f"unit_of_analysis must be one of {sorted(_VALID_UNITS)}, got {unit_of_analysis!r} "
            "(rounds within a run are serially correlated, not independent observations)"
        )
    if n <= 0 or successes < 0 or successes > n:
        raise InsufficientDataError(f"successes {successes} must be in [0, {n}] with n > 0")
    # a test must have been computed over a sample no larger than this aggregate's:
    # a McNemar over 1 pair must not ride along on an n=100 aggregate (review r4)
    if test is not None:
        paired_n = test.get("paired_n")
        if paired_n is not None and int(paired_n) > n:  # type: ignore[arg-type]
            raise InsufficientDataError(
                f"test paired_n {paired_n} exceeds aggregate n {n} — mismatched sample"
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
    if comparison_design is not None:
        aggregate["comparison_design"] = comparison_design
    # attach the test ONLY when it is powered on its OWN terms: the aggregate's
    # marginal n clears the minimum AND the test's effective_n (DISCORDANT n for
    # McNemar, min-arm for two_proportion) does too — so a McNemar riding on 1
    # discordant pair can't ride a large marginal n and be read as significant
    # (review r15/r16)
    if (
        test is not None
        and not is_inconclusive(aggregate)
        and int(test.get("effective_n", 0)) >= INCONCLUSIVE_MIN_N  # type: ignore[arg-type]
    ):
        aggregate["test"] = test
    return aggregate


def mcnemar_test(pairs: Sequence[tuple[bool, bool]], vs: str) -> dict[str, object]:
    """The paired test payload stored in an aggregate. ``pairs`` are
    (baseline_outcome, treated_outcome) per trial, same scenario/seed.

    Valid ONLY when the pair is a real matched pair (a deterministic agent whose
    behavior is fixed by scenario+seed). For independently-sampled live-model
    runs use two_proportion_test — the pairing there is nominal, not real."""
    b = sum(1 for base, treated in pairs if base and not treated)
    c = sum(1 for base, treated in pairs if not base and treated)
    paired_n = len(pairs)
    # McNemar's POWER comes ONLY from the DISCORDANT pairs (b + c): concordant
    # pairs (both arms same outcome) contribute nothing to the test — the exact
    # binomial is computed over b+c alone. So the effective sample is the
    # discordant n, NOT the total matched-pairs n (review r16). A 200-pair run
    # that is 199 concordant + 1 discordant has the power of ONE observation, and
    # reporting effective_n=200 would badly overstate it. paired_n is retained as
    # context (the matched-pair denominator), but status/power key off discordant_n.
    discordant_n = b + c
    status, reason = _test_status(discordant_n)
    test: dict[str, object] = {
        "name": "mcnemar",
        "vs": vs,
        "discordant": {"b": b, "c": c},
        "paired_n": paired_n,
        "effective_n": discordant_n,
        "status": status,
        "p": mcnemar_exact(b, c),
    }
    if reason:
        test["reason"] = reason
    return test


def _test_status(effective_n: int) -> tuple[str, str]:
    """A statistical test is `inconclusive` below the minimum effective n —
    reported on the test itself, distinct from the aggregate's marginal n."""
    if effective_n < INCONCLUSIVE_MIN_N:
        return "inconclusive", f"effective_n {effective_n} below minimum {INCONCLUSIVE_MIN_N}"
    return "conclusive", ""


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
    # per-condition (condition_id, completed, total) — a partial run whose
    # completions are lopsided across conditions has few matched pairs, so the
    # paired comparison is weak even when the overall n looks healthy (review r14)
    by_condition: tuple[tuple[str, int, int], ...] = ()
    # True when missingness is concentrated on ONE condition — which specifically
    # threatens the matched-pairs comparison (one arm loses its data)
    condition_imbalanced: bool = False

    def display(self) -> str:
        parts = [f"n={self.n_completed}/{self.n_total}"]
        if self.n_missing:
            reasons = ", ".join(f"{count} {reason}" for reason, count in self.reasons)
            parts.append(f"{self.n_missing} excluded: {reasons}")
        if len(self.by_condition) > 1 and self.n_missing:
            per = ", ".join(f"{cid} {done}/{tot}" for cid, done, tot in self.by_condition)
            parts.append(f"by condition: {per}")
        if self.potentially_biased:
            parts.append("flagged potentially biased (missingness is non-random)")
        if self.condition_imbalanced:
            parts.append("flagged condition-imbalanced (missingness concentrated on one arm)")
        return "; ".join(parts)


def missingness(trials: Sequence[dict[str, object]]) -> MissingnessSummary:
    """Summarize failed/excluded trials; flag plausibly NON-random missingness
    (failures concentrated on one scenario, or on one CONDITION — which breaks
    the matched pairs) instead of silently computing over survivors."""
    total = len(trials)
    missing = [t for t in trials if t["status"] != "completed"]
    reasons: dict[str, int] = {}
    by_scenario: dict[str, int] = {}
    missing_by_condition: dict[str, int] = {}
    total_by_condition: dict[str, int] = {}
    completed_by_condition: dict[str, int] = {}
    for trial in trials:
        cid = str(trial["condition_id"])
        total_by_condition[cid] = total_by_condition.get(cid, 0) + 1
        if trial["status"] == "completed":
            completed_by_condition[cid] = completed_by_condition.get(cid, 0) + 1
    for trial in missing:
        reason = str(trial.get("failure_reason", "unspecified"))
        reasons[reason] = reasons.get(reason, 0) + 1
        scenario = str(trial["scenario_id"])
        by_scenario[scenario] = by_scenario.get(scenario, 0) + 1
        cid = str(trial["condition_id"])
        missing_by_condition[cid] = missing_by_condition.get(cid, 0) + 1
    scenario_count = len({str(t["scenario_id"]) for t in trials})
    concentrated = bool(
        missing
        and scenario_count > 1
        and max(by_scenario.values()) / len(missing) >= _BIAS_CONCENTRATION_SHARE
    )
    condition_count = len(total_by_condition)
    condition_imbalanced = bool(
        missing
        and condition_count > 1
        and max(missing_by_condition.values()) / len(missing) >= _BIAS_CONCENTRATION_SHARE
    )
    by_condition = tuple(
        (cid, completed_by_condition.get(cid, 0), total_by_condition[cid])
        for cid in sorted(total_by_condition)
    )
    return MissingnessSummary(
        n_total=total,
        n_completed=total - len(missing),
        n_missing=len(missing),
        reasons=tuple(sorted(reasons.items())),
        potentially_biased=concentrated,
        by_condition=by_condition,
        condition_imbalanced=condition_imbalanced,
    )
