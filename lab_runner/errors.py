"""Error hierarchy for lab_runner."""

from __future__ import annotations


class RunnerError(Exception):
    """Base class for every lab_runner error."""


class RealExecutionBlocked(RunnerError):
    """A side-effecting tool was asked to run for real without the full
    opt-in guard set (threat-model §1)."""


class SimulationError(RunnerError):
    """A tool cannot be honestly simulated: its manifest declares simulation
    unsupported / an unknown adapter, or a call's args do not match args_schema.
    The host must NOT fake a successful result in these cases (review r6)."""


class UnsupportedPredicateError(RunnerError):
    """The predicate uses a construct outside the reference evaluator's
    supported subset."""


class UnknownKernelError(RunnerError):
    """A condition pins a kernel version absent from the registry."""


class UnknownAgentError(RunnerError):
    """experiment.agent_ref does not resolve to a registered agent adapter."""


class IncidentImportError(RunnerError):
    """An incident package failed validation (schema, semantics, cross-refs or
    config hash) — nothing was written."""


class IncidentReplayMismatch(IncidentImportError):
    """The incident trace does not replay under its recorded condition.

    Carries a structured `detail` (replay status + recorded vs recomputed
    verdict cores) so an HTTP surface can report the divergence honestly."""

    def __init__(self, message: str, detail: dict[str, object]) -> None:
        super().__init__(message)
        self.detail = detail


class ExperimentFileError(RunnerError):
    """An .axl experiment file is malformed or fails contract validation.

    Carries stage-tied errors (lifecycle stage: validating).
    """

    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__(f"experiment validation failed: {'; '.join(errors)}")
        self.errors = errors


class ConfirmationRequired(RunnerError):
    """The pre-run estimate was not confirmed (no --yes and no interactive TTY)."""


class CostCeilingReached(RunnerError):
    """A hard cost ceiling was reached DURING a trial's agent loop (before a
    provider call), so the whole run must stop — not just fail this one trial.

    `overshot` distinguishes a clean stop AT the ceiling (we refused the next
    call while already at/over the limit) from the honest case where the last
    completed call pushed actual usage strictly PAST a ceiling (review r12)."""

    def __init__(self, reason: str, *, overshot: bool = False) -> None:
        state = "overshot" if overshot else "reached"
        super().__init__(f"cost ceiling {state}: {reason}")
        self.reason = reason
        self.overshot = overshot
