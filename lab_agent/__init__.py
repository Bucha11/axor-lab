"""lab_agent — the BYOK model-backed agent runtime (plan block B1).

A wrapped-code runtime that drives a real tool-calling loop through the
lab_runner value ledger, behind a `ModelBackend` protocol. `CassetteBackend`
covers it deterministically offline; `AnthropicBackend` is the real BYOK path
(optional dependency + optional key). Provenance stays the runtime's — a
backend chooses what the model does, never how a value is labeled.
"""

from .backends import (
    FINAL,
    TOOL_CALL,
    AnthropicBackend,
    CassetteBackend,
    ModelAction,
    ModelBackend,
)
from .cost import CostEstimate, estimate_cost
from .errors import AgentError, BackendUnavailable, CassetteExhausted, ProtocolViolation
from .langchain_backend import LangChainBackend
from .wrapped import FileCassetteAgent, WrappedModelAgent

__all__ = [
    "AgentError",
    "AnthropicBackend",
    "BackendUnavailable",
    "CassetteBackend",
    "CassetteExhausted",
    "CostEstimate",
    "FileCassetteAgent",
    "FINAL",
    "LangChainBackend",
    "ModelAction",
    "ModelBackend",
    "ProtocolViolation",
    "TOOL_CALL",
    "WrappedModelAgent",
    "estimate_cost",
]
