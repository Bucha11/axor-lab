"""Error hierarchy for lab_adapters."""

from __future__ import annotations


class AdapterError(Exception):
    """Base class for every lab_adapters error."""


class UnknownSuiteError(AdapterError):
    """A requested benchmark suite is not curated in this adapter."""
