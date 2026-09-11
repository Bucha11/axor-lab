"""Typed errors for the Suite SDK."""

from __future__ import annotations


class SuiteError(Exception):
    """Base for every Suite SDK failure."""


class SuiteValidationError(SuiteError):
    """A manifest is invalid. Carries EVERY failure, not just the first — an
    author fixing a manifest one error per run is a bad authoring loop."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("suite validation failed: " + "; ".join(errors))


class SuiteNotFound(SuiteError):
    """No suite registered under that id."""
