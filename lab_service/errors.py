"""Error hierarchy for lab_service.

The service layer does not invent new failure modes: it re-raises the domain's
own errors (``RunnerError``, ``ContractsError``, ``SuiteError``, …) untouched so
every face maps them the same way. ``ServiceError`` exists for the few failures
that belong to the service boundary itself — a caller asking for an output
location it may not write, say — and never to wrap a domain error in a second
skin.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base for every lab_service failure."""


class OutputRefused(ServiceError):
    """The requested output location cannot be written without data loss.

    A non-empty export directory is refused rather than merged into: a stale
    file from an earlier export would linger, and the manifest verifier would
    either flag it or — worse — a reader would trust it.
    """
