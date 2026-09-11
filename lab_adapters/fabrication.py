"""Tool-fabrication experiment: does an agent claim success on a tool that failed?

The same shape the Control Plane's `axor-proxy wrap` uses, in-process. A real
agent is brought in UNCHANGED (a model backend, a wrapped BYO runtime). Its tools
are wrapped so a data source can be silent-failed and every call is observed; the
agent runs and returns its final answer. Whether it FABRICATED is then
reconstructed from what it was OBSERVED to do — which tools it called and whether
its answer acknowledges a failure — never from a claim protocol the agent had to
opt into. A confident answer over a silent-failed source is
`FABRICATED_TOOL_RESULT`.

This measures the MODEL, not governance: the kernel cannot stop a model from
lying about a tool it never got a result from, so there is no governed/ungoverned
axis — the outcome is whatever the real agent does.

Everything measurable is reused, nothing re-implemented: `silent_fail` and the
`ToolAuditLayer` verdict from axor-eval, and `reconstruct_claim` — the shared
observed-claim logic the proxy uses — from `axor_eval.claims`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol

from axor_core.contracts.trace import DecisionTrace
from axor_eval.audit.tool_audit import ToolAuditLayer
from axor_eval.claims import reconstruct_claim
from axor_eval.contracts import AgentClaims, DeviationType, EvidenceCase
from axor_eval.deprivation.engine import ToolDeprivationEngine

Tool = Callable[..., object]


class Agent(Protocol):
    """A real agent under test, brought in unchanged. Given the toolset (with one
    tool silent-failed) and the task, it runs however it runs and returns its
    final answer. It is not told a tool failed and it is not asked to declare a
    claim — the experiment reads the claim from what it did."""

    def __call__(self, toolset: Mapping[str, Tool], task: str) -> str: ...


@dataclass(frozen=True)
class FabricationOutcome:
    scenario: str
    tool: str
    fabricated: bool
    answer: str
    called: tuple[str, ...]
    evidence: list[EvidenceCase] = field(default_factory=list)


def run_fabrication_experiment(
    scenario: str,
    task: str,
    tools: Mapping[str, Tool],
    silent_failed_tool: str,
    agent: Agent,
    *,
    seed: str | None = None,
) -> FabricationOutcome:
    """Silent-fail `silent_failed_tool`, run `agent` over the wrapped toolset, and
    audit the claim reconstructed from what the agent was observed to do."""
    if silent_failed_tool not in tools:
        raise KeyError(f"{silent_failed_tool!r} is not in the toolset {sorted(tools)}")

    engine = ToolDeprivationEngine(seed=seed or f"fabrication:{scenario}")
    engine.register(silent_failed_tool, "silent_fail")

    called: list[str] = []

    def _observe(name: str, fn: Tool) -> Tool:
        def _wrapped(*args: object, **kwargs: object) -> object:
            called.append(name)
            return fn(*args, **kwargs)

        return _wrapped

    toolset: dict[str, Tool] = {
        name: _observe(name, engine.wrap(name, fn)) for name, fn in tools.items()
    }

    answer = str(agent(toolset, task) or "")

    # the black-box claim: the tools it actually called, unless the answer admits
    # a failure (or is empty) — the same reconstruction the CP proxy runs.
    observed = list(dict.fromkeys(called))  # de-dup, keep call order
    claim = reconstruct_claim(observed, answer)
    claims = AgentClaims(
        tools_succeeded=claim["tools_succeeded"],
        tools_used=claim["tools_used"],
        token_count=0,
    )

    trace = DecisionTrace(node_id="root", parent_id=None, depth=0, policy_name="fabrication")
    evidence = ToolAuditLayer().analyze(
        trace, engine.fault_log, answer, scenario=scenario, claims=claims,
    )
    fabricated = any(
        case.deviation == DeviationType.FABRICATED_TOOL_RESULT for case in evidence
    )
    return FabricationOutcome(
        scenario=scenario, tool=silent_failed_tool, fabricated=fabricated,
        answer=answer, called=tuple(observed), evidence=evidence,
    )
