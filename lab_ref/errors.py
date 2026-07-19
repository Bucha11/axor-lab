"""Error hierarchy for the lab_ref reference implementation."""

from __future__ import annotations


class LabRefError(Exception):
    """Base class for every lab_ref error."""


class ScenarioValidationError(LabRefError):
    """A scenario failed author-time validation (acceptance test 1).

    Carries the full list of specific, stage-tied errors — validation
    failures are actionable, never a bare boolean.
    """

    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__(f"scenario validation failed: {'; '.join(errors)}")
        self.errors = errors


class RealExecutionBlocked(LabRefError):
    """A side-effecting tool was asked to run for real without the full
    opt-in guard set (threat-model §1)."""


class BundleIntegrityError(LabRefError):
    """A bundle's content hashes do not match its artifacts."""


class ClaimTypingError(LabRefError):
    """A publication claim was typed against the claims.md boundary
    (e.g. a behavioral delta declared exactly_replayable)."""


class UnsupportedPredicateError(LabRefError):
    """The predicate uses a construct outside the reference evaluator's
    supported subset."""


class UnknownKernelError(LabRefError):
    """A condition pins a kernel version absent from the registry."""
