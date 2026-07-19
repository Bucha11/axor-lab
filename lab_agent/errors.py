"""Error hierarchy for lab_agent."""

from __future__ import annotations


class AgentError(Exception):
    """Base class for every lab_agent error."""


class BackendUnavailable(AgentError):
    """A model backend's optional dependency or API key is missing."""


class CassetteExhausted(AgentError):
    """A cassette backend ran out of recorded turns."""


class ProtocolViolation(AgentError):
    """The model did not follow the expected tool-calling protocol."""
