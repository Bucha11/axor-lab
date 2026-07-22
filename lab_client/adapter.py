"""The AgentAdapter interface (agent-connection.md).

A custom agent implements `AgentAdapter`; framework adapters (axor-claude,
axor-langchain) are ready-made implementations of the same protocol, not the only
path. The adapter wraps the user's EXISTING entrypoint and returns a `trace/v1`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolDescription:
    name: str
    description: str = ""


@dataclass(frozen=True)
class AgentDescription:
    name: str
    adapter_kind: str                       # e.g. "claude", "langchain", "custom"
    models: list[str] = field(default_factory=list)
    tools: list[ToolDescription] = field(default_factory=list)


@dataclass(frozen=True)
class AgentInput:
    """What Lab hands the runtime for one trial — the scenario task + inputs."""
    task: str = ""
    inputs: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionContext:
    """Per-trial context the runtime builds from the claimed job (agent-connection.md
    `build_lab_context`). Carries the assigned TrialUnit coordinate so the adapter can
    stamp the trace's `trial` block to match Lab's assignment exactly — Lab binds the
    uploaded trace to this unit on `complete`."""
    run_id: str
    trial_id: str
    trial: dict[str, object] = field(default_factory=dict)  # {run_id,scenario_id,condition_id,seed,repeat_index}
    condition: dict[str, object] = field(default_factory=dict)


@dataclass
class AgentRunResult:
    output: object
    trace: dict[str, object]                # a trace/v1 body
    status: str = "completed"               # completed | failed


@runtime_checkable
class AgentAdapter(Protocol):
    """Lab's runtime client sees ONLY this — never the concrete framework."""

    async def describe(self) -> AgentDescription:
        ...

    async def run(self, input: AgentInput, execution_context: ExecutionContext) -> AgentRunResult:
        ...

    async def reset(self) -> None:
        ...
