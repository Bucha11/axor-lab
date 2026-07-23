"""The AgentAdapter interface (contracts/adapters.md).

An adapter wraps an existing agent entrypoint and does exactly three things:
describe the agent, intercept the two decision points (model call + tool call) so the
kernel builds provenance and gates effects, and emit a `trace/v1` through the
`trace_sink`. Framework adapters (axor-claude, axor-langchain) pre-fill the
interception; the generic/custom path implements this protocol by hand — a
first-class scenario, not a fallback. The adapter contains NO Lab/Control-Plane
business logic and opens NO network egress: events leave only via `trace_sink`.
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


@dataclass
class TraceSink:
    """Append-only egress for kernel events (adapters.md §6/§11). The adapter emits
    events here and NOWHERE else — it never opens its own HTTP connection; the
    driving client (LabRuntimeClient) is what ships them."""
    events: list[dict[str, object]] = field(default_factory=list)

    def emit(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def extend(self, events: list[dict[str, object]]) -> None:
        self.events.extend(events)


@dataclass
class Limits:
    """Per-run budget (adapters.md §6). None means unbounded for that axis."""
    wall_time_s: float | None = None
    tokens: int | None = None
    tool_calls: int | None = None


@dataclass
class CancelToken:
    """Cooperative cancellation (adapters.md §6/§12). A cancelled run stops without
    losing already-completed trials."""
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass
class ExecutionContext:
    """What an adapter receives for one run (adapters.md §6). Built by whoever drives
    the run — `LabRuntimeClient` for a Lab trial. `condition.enforcement == "off"` is
    observe-only (the kernel still labels + records, never blocks); `tools` is the
    SIMULATED registry in Lab (a side-effecting tool never dispatches for real unless
    its manifest opts in). The `trial` coordinate is Lab's assigned TrialUnit, which
    the produced trace's `trial` block must equal exactly."""
    condition: dict[str, object]                       # enforcement + kernel_ref/policy_ref (thin)
    trace_sink: TraceSink
    run_id: str = ""
    trial_id: str = ""
    trial: dict[str, object] = field(default_factory=dict)  # the assigned coordinate
    tools: object | None = None                        # bound/simulated tool registry
    fixtures: dict[str, object] | None = None          # canned tool results with $injection
    limits: Limits = field(default_factory=Limits)
    cancel: CancelToken = field(default_factory=CancelToken)


@dataclass
class AgentRunResult:
    output: object
    trace: dict[str, object]                # a trace/v1 body
    status: str = "completed"               # completed | failed
    # explicit_flow_tracked (both provenance writes done) | heuristic_attribution
    provenance_fidelity: str = "explicit_flow_tracked"


@runtime_checkable
class AgentAdapter(Protocol):
    """Lab's runtime client sees ONLY this — never the concrete framework."""

    async def describe(self) -> AgentDescription:
        ...

    async def run(self, input: AgentInput, ctx: ExecutionContext) -> AgentRunResult:  # noqa: A002
        ...

    async def reset(self) -> None:
        ...
