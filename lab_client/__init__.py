"""lab_client — the Lab-side runtime client + agent adapter interface.

`LabRuntimeClient` is **owned by / published from the Lab repo** (agent-connection.md):
Control Plane does not own it, and axor-core takes no dependency on axor-lab. It is
the outbound client a runtime uses to reach **Lab** — separate from any Control
Plane `PlaneClient`, with its own `axlab_` token and URL. Lab **assigns**, the
runtime **executes locally** and uploads; Lab never dispatches a tool or calls the
agent.

The `AgentAdapter` protocol is the seam a custom agent implements (custom agents are
a required scenario). The adapter wraps the user's EXISTING entrypoint — the user
does not move their agent's logic into a new Axor runtime. `LabRuntimeClient` sees
only `AgentAdapter`; it never knows whether the agent is Claude, LangChain, OpenAI,
custom Python, or an external-process wrapper.
"""

from .adapter import (
    AgentAdapter,
    AgentDescription,
    AgentInput,
    AgentRunResult,
    CancelToken,
    ExecutionContext,
    Limits,
    ToolDescription,
    TraceSink,
)
from .runner_adapter import RunnerAgentAdapter
from .runtime_client import LabRuntimeClient, LabRuntimeError, run_job_loop

__all__ = [
    "AgentAdapter",
    "AgentDescription",
    "AgentInput",
    "AgentRunResult",
    "CancelToken",
    "ExecutionContext",
    "LabRuntimeClient",
    "LabRuntimeError",
    "Limits",
    "RunnerAgentAdapter",
    "ToolDescription",
    "TraceSink",
    "run_job_loop",
]
