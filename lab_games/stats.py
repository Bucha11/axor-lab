"""Per-run game statistics (statistics.md §2b + §1).

A continuous per-run rate → mean of per-run values with a paired bootstrap CI
that NARROWS with the number of runs. The unit of analysis is the run; passing
round-level data (unit="round") is rejected by lab_analysis, so a game can
never fabricate precision by counting rounds.
"""

from __future__ import annotations

from typing import Sequence

from lab_analysis import UNIT_RUN, paired_bootstrap_ci
from lab_analysis.errors import UnitOfAnalysisError


def game_rate_aggregate(
    metric: str,
    condition_id: str,
    per_run_values: Sequence[float],
    unit_of_analysis: str = UNIT_RUN,
    seed: int = 0,
) -> dict[str, object]:
    """A bundle-shaped aggregate for a per-run continuous rate."""
    if unit_of_analysis != UNIT_RUN:
        raise UnitOfAnalysisError(
            f"a game metric's unit must be '{UNIT_RUN}', got {unit_of_analysis!r} "
            "(rounds within a run are serially correlated, not independent)"
        )
    n = len(per_run_values)
    estimate = sum(per_run_values) / n if n else 0.0
    low, high = paired_bootstrap_ci(per_run_values, seed=seed)
    return {
        "metric": metric,
        "condition_id": condition_id,
        "estimate": estimate,
        "interval": {"method": "paired_bootstrap", "low": low, "high": high},
        "n": n,
        "unit_of_analysis": UNIT_RUN,
    }
