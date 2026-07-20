"""lab_analysis — the statistics engine (contracts/statistics.md as code).

Aggregates are computed HERE at run time; the UI renders stored fields
verbatim — there is no render-time code path that derives a number.
"""

from .errors import AnalysisError, InsufficientDataError, UnitOfAnalysisError
from .stats import (
    INCONCLUSIVE_MIN_N,
    MissingnessSummary,
    UNIT_RUN,
    UNIT_TRIAL,
    binary_aggregate,
    is_inconclusive,
    mcnemar_exact,
    mcnemar_test,
    missingness,
    paired_bootstrap_ci,
    two_proportion_test,
    wilson_interval,
)

__all__ = [
    "AnalysisError",
    "INCONCLUSIVE_MIN_N",
    "InsufficientDataError",
    "MissingnessSummary",
    "UNIT_RUN",
    "UNIT_TRIAL",
    "UnitOfAnalysisError",
    "binary_aggregate",
    "is_inconclusive",
    "mcnemar_exact",
    "mcnemar_test",
    "missingness",
    "paired_bootstrap_ci",
    "two_proportion_test",
    "wilson_interval",
]
