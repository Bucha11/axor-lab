"""lab_analysis — the statistics engine (contracts/statistics.md as code).

Aggregates are computed HERE at run time; the UI renders stored fields
verbatim — there is no render-time code path that derives a number.
"""

from .errors import AnalysisError, InsufficientDataError, UnitOfAnalysisError
from .stats import (
    DERIVED_METRIC_OUTCOMES,
    INCONCLUSIVE_MIN_N,
    MissingnessSummary,
    UNIT_RUN,
    UNIT_TRIAL,
    NUMERIC_ESTIMATORS,
    binary_aggregate,
    numeric_aggregate,
    is_inconclusive,
    mcnemar_exact,
    mcnemar_test,
    metric_is_derived,
    missingness,
    paired_bootstrap_ci,
    two_proportion_test,
    wilson_interval,
)

__all__ = [
    "AnalysisError",
    "DERIVED_METRIC_OUTCOMES",
    "metric_is_derived",
    "INCONCLUSIVE_MIN_N",
    "InsufficientDataError",
    "MissingnessSummary",
    "UNIT_RUN",
    "UNIT_TRIAL",
    "UnitOfAnalysisError",
    "NUMERIC_ESTIMATORS",
    "binary_aggregate",
    "numeric_aggregate",
    "is_inconclusive",
    "mcnemar_exact",
    "mcnemar_test",
    "missingness",
    "paired_bootstrap_ci",
    "two_proportion_test",
    "wilson_interval",
]
