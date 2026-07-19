"""Error hierarchy for lab_contracts."""

from __future__ import annotations


class ContractsError(Exception):
    """Base class for every lab_contracts error."""


class SchemaValidationError(ContractsError):
    """An artifact failed JSON-Schema validation against a contract schema."""

    def __init__(self, schema_name: str, errors: tuple[str, ...]) -> None:
        super().__init__(f"{schema_name}: {'; '.join(errors)}")
        self.schema_name = schema_name
        self.errors = errors


class ScenarioValidationError(ContractsError):
    """A scenario failed author-time semantic validation (acceptance test 1).

    Carries the full list of specific, stage-tied errors — validation
    failures are actionable, never a bare boolean.
    """

    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__(f"scenario validation failed: {'; '.join(errors)}")
        self.errors = errors


class BundleIntegrityError(ContractsError):
    """A bundle's content hashes do not match its artifacts."""


class ClaimTypingError(ContractsError):
    """A publication claim was typed against the claims.md boundary
    (e.g. a behavioral delta declared exactly_replayable)."""


class UnresolvedInputError(ContractsError, KeyError):
    """An `$inputs.x` reference does not resolve against scenario.inputs."""
