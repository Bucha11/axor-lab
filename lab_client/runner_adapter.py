"""RunnerAgentAdapter — a concrete `AgentAdapter` that actually runs a trial.

This is the reference agent connection: given the TrialUnit coordinate Lab assigned
(scenario_id, condition_id, seed, repeat_index), it executes THAT trial **locally**
through the real axor-core kernel (`lab_runner.run_trial`) — the agent decides, the
kernel governs, provenance is constructed — and returns the genuine `trace/v1` it
produced. Nothing is canned; the trace's verdict is whatever the local kernel decided
for the local agent under that condition.

The runtime side holds the scenarios + manifests + conditions + kernel + agent
locally (Lab never executes the agent, agent-connection.md). A framework adapter
(axor-claude, axor-langchain) or a model-backed BYOK agent plugs in as the `agent`.
"""

from __future__ import annotations

from .adapter import AgentDescription, AgentInput, AgentRunResult, ExecutionContext


class RunnerAgentAdapter:
    """Executes an assigned trial via `lab_runner.run_trial` and returns its trace.

    `scenarios` / `conditions` are keyed by id; `agent` is a `lab_runner` agent
    (default `ScriptedAgent` — a deterministic reference agent). A different `agent`
    (a model-backed BYOK adapter, a framework adapter) swaps the executed behaviour
    without changing this connection layer."""

    def __init__(self, scenarios: dict[str, dict], manifests: dict[str, dict],
                 conditions: dict[str, dict], kernel_registry: object,
                 agent: object | None = None, name: str = "runner",
                 kind: str | None = None) -> None:
        self.scenarios = scenarios
        self.manifests = manifests
        self.conditions = conditions
        self.kernel_registry = kernel_registry
        self._agent = agent
        self.name = name
        self._kind = kind

    def _agent_or_default(self) -> object:
        if self._agent is None:
            from lab_runner import ScriptedAgent
            self._agent = ScriptedAgent()
        return self._agent

    async def describe(self) -> AgentDescription:
        kind = self._kind or type(self._agent_or_default()).__name__
        return AgentDescription(name=self.name, adapter_kind=kind,
                                models=[], tools=[])

    async def reset(self) -> None:
        # a stateless reference agent needs no reset; a stateful adapter would clear
        # its conversation here between trials
        return None

    async def run(self, input: AgentInput,  # noqa: A002 (protocol name)
                  ctx: ExecutionContext) -> AgentRunResult:
        from lab_runner import resolve_kernel, run_trial

        coord = ctx.trial
        scenario_id = str(coord["scenario_id"])
        condition_id = str(coord["condition_id"])
        scenario = self.scenarios.get(scenario_id)
        condition = self.conditions.get(condition_id)
        if scenario is None or condition is None:
            return AgentRunResult(
                output={"error": f"unknown scenario/condition {scenario_id}/{condition_id}"},
                trace={}, status="failed", provenance_fidelity="heuristic_attribution")

        kernel = resolve_kernel(
            str(condition["kernel"]), self.manifests, condition.get("policy"),
            self.kernel_registry, scenario.get("inputs", {}))
        # run_trial does the two provenance writes (mint-on-tool-result with
        # untrusted_fields, mint-on-model-output with conservative-join) and gates
        # BEFORE dispatch — the two interception points of adapters.md §2/§7/§8.
        outcome = run_trial(
            scenario, self.manifests, condition, kernel,
            run_id=str(coord["run_id"]), seed=str(coord["seed"]),
            repeat_index=int(coord["repeat_index"]), agent=self._agent_or_default())
        # events leave ONLY through the trace_sink (adapters.md §6/§11) — never our
        # own HTTP; the driving client ships them. run_trial stamps the trace's
        # `trial` block with exactly this coordinate, so Lab's unit-binding passes.
        events = outcome.trace.get("events", [])
        if isinstance(events, list):
            ctx.trace_sink.extend(events)
        return AgentRunResult(
            output={"violation": outcome.violation, "task_success": outcome.task_success},
            trace=outcome.trace, status="completed",
            provenance_fidelity="explicit_flow_tracked")
