"""axor-claude — the framework adapter for the Anthropic Messages API (adapters.md §4).

It knows the Anthropic loop (`tool_use` blocks, tool results): the model decision is
`AnthropicBackend` (lab_agent), and the shared wrapped runtime does the provenance
mint + gate-before-dispatch. Point it at a model + key; downstream behaviour is
identical to the generic path — the framework only pre-fills the interception.
"""

from __future__ import annotations

from ..runner_adapter import RunnerAgentAdapter


def adapter(scenarios: dict[str, dict], manifests: dict[str, dict],
            conditions: dict[str, dict], kernel_registry: object, *,
            model: str = "claude-opus-4-8", api_key_env: str = "ANTHROPIC_API_KEY",
            backend: object | None = None, budget: object | None = None,
            name: str = "axor-claude") -> RunnerAgentAdapter:
    """Build an `AgentAdapter` that runs each Lab trial through Claude locally.

    The Anthropic SDK + key are needed only when a trial actually calls the model;
    constructing the adapter is cheap and dependency-light. Pass `backend=` to inject
    a deterministic `CassetteBackend` (offline / CI) instead of the live model."""
    from lab_agent import AnthropicBackend, WrappedModelAgent

    model_backend = backend if backend is not None else AnthropicBackend(
        model=model, api_key_env=api_key_env)
    agent = WrappedModelAgent(backend=model_backend, budget=budget, model=model)  # type: ignore[arg-type]
    return RunnerAgentAdapter(
        scenarios=scenarios, manifests=manifests, conditions=conditions,
        kernel_registry=kernel_registry, agent=agent, name=name, kind="claude")
