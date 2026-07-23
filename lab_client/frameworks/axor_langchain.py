"""axor-langchain — the framework adapter for LangChain/LangGraph (adapters.md §4).

It knows the LangChain chat-model interface (`bind_tools(...).invoke(...)`): the
model decision is `LangChainBackend` (lab_agent), and the shared wrapped runtime does
the provenance mint + gate-before-dispatch. Add it to an existing agent; downstream
behaviour is identical to the generic path.
"""

from __future__ import annotations

from ..runner_adapter import RunnerAgentAdapter


def adapter(scenarios: dict[str, dict], manifests: dict[str, dict],
            conditions: dict[str, dict], kernel_registry: object, *,
            model: object = None, backend: object | None = None,
            budget: object | None = None, name: str = "axor-langchain") -> RunnerAgentAdapter:
    """Build an `AgentAdapter` that runs each Lab trial through a LangChain model.

    Pass `model=` a LangChain chat model (`ChatAnthropic`, `ChatOpenAI`, …), or
    `backend=` a ready `LangChainBackend` / deterministic backend for offline / CI."""
    from lab_agent import LangChainBackend, WrappedModelAgent

    if backend is not None:
        model_backend = backend
    elif model is not None:
        model_backend = LangChainBackend(model=model)
    else:
        raise ValueError("axor_langchain.adapter needs a `model` or a `backend`")
    agent = WrappedModelAgent(backend=model_backend)  # type: ignore[arg-type]
    return RunnerAgentAdapter(
        scenarios=scenarios, manifests=manifests, conditions=conditions,
        kernel_registry=kernel_registry, agent=agent, name=name, kind="langchain")
