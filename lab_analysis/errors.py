"""Error hierarchy for lab_analysis."""

from __future__ import annotations


class AnalysisError(Exception):
    """Base class for every lab_analysis error."""


class UnitOfAnalysisError(AnalysisError):
    """A number whose unit is 'round' (or any non-trial/run unit) is rejected —
    rounds within a run are serially correlated, not independent observations."""


class InsufficientDataError(AnalysisError):
    """An estimator was asked to run over an empty or invalid sample."""
