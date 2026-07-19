"""Error hierarchy for lab_runner."""

from __future__ import annotations


class RunnerError(Exception):
    """Base class for every lab_runner error."""


class RealExecutionBlocked(RunnerError):
    """A side-effecting tool was asked to run for real without the full
    opt-in guard set (threat-model §1)."""


class UnsupportedPredicateError(RunnerError):
    """The predicate uses a construct outside the reference evaluator's
    supported subset."""


class UnknownKernelError(RunnerError):
    """A condition pins a kernel version absent from the registry."""


class UnknownAgentError(RunnerError):
    """experiment.agent_ref does not resolve to a registered agent adapter."""


class ExperimentFileError(RunnerError):
    """An .axl experiment file is malformed or fails contract validation.

    Carries stage-tied errors (lifecycle stage: validating).
    """

    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__(f"experiment validation failed: {'; '.join(errors)}")
        self.errors = errors


class ConfirmationRequired(RunnerError):
    """The pre-run estimate was not confirmed (no --yes and no interactive TTY)."""
