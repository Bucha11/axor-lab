"""Framework adapters (contracts/adapters.md §4).

Pre-built `AgentAdapter` conveniences for a supported framework: `axor_claude` wraps
the Anthropic tool-use loop, `axor_langchain` wraps a LangChain chat model. Both are
thin — they only supply the model DECISION (`ModelBackend`); the shared wrapped
runtime does the two provenance writes and gates BEFORE dispatch, so downstream
behaviour is identical to the generic path. The generic/custom `RunnerAgentAdapter`
remains first-class, not a fallback.
"""

from . import axor_claude, axor_langchain

__all__ = ["axor_claude", "axor_langchain"]
