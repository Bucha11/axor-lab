"""Error hierarchy for lab_server."""

from __future__ import annotations


class ServerError(Exception):
    """Base class for every lab_server error."""


class PublishRejected(ServerError):
    """A publish request failed a server-side gate (schema, hash, or replay).

    Carries an HTTP status so the API layer stays thin.
    """

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


class NotFound(ServerError):
    """A requested publication or trace does not exist."""
