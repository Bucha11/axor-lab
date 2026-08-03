"""A general multi-step agent loop.

The reference `run_trial` in `runner.py` is a fixture-driven TWO-step slice —
read a tool, decide one sink call — built to study gates, not to run arbitrary
agents. This module is the general case: an agent proposes any tool call with
any arguments, observes the result, and continues until it finishes or a budget
stops it.

It is a SEPARATE path on purpose. The slice's trace shape is pinned by
canonicalization vectors, replay tests and already-published content hashes, so
generalizing it in place would change bytes that other people's hashes cover.
Both paths emit the same `trace/v1` and the same `TrialOutcome`, so everything
downstream — predicates, metrics, bundling, invariants — is shared.

Provenance is assigned by the RUNTIME, never self-reported by the agent. Three
rules, in order, for every argument the agent supplies:

  1. **Byte-identical to a declared scenario input → `prompt_given`.** The value
     came from the user's own declared ground truth. This is checked by the
     runtime against `scenario.inputs`, so an agent cannot claim it; and it is
     sound because a sink driven by exactly the value the user declared is the
     intended action no matter what the agent read on the way there.
  2. **Otherwise, if the agent has observed ANY untrusted value → conservative
     join: `model_extraction` over every untrusted value seen so far.** The
     agent cannot launder taint by rephrasing, re-deriving or laundering a value
     through a scratch tool call.
  3. **Otherwise → `prompt_given` constant.** Nothing untrusted has been read.

Rule 1 is the only relaxation of the slice's stricter "everything after an
untrusted read is tainted", and without it a governed general loop would DENY
even the correct action and every task would fail. It is a narrow, verifiable
exemption on whole-value equality — never on substrings or partial structures.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, Union

from lab_contracts.canonical import world_digest

from .ledger import ValueLedger
from .predicates import evaluate
from .simulator import SimulatedToolHost

RUNTIME_ID = "lab-runner@0.1"
DEFAULT_MAX_STEPS = 24
DEFAULT_MAX_TOOL_CALLS = 16

STOP_FINISHED = "finished"
STOP_MAX_STEPS = "max_steps"
STOP_MAX_TOOL_CALLS = "max_tool_calls"
STOP_PROGRAM_EXHAUSTED = "program_exhausted"


@dataclass(frozen=True)
class ToolCall:
    """The agent wants to call `tool` with `args`."""

    tool: str
    args: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Finish:
    """The agent is done."""

    output: object = None


Action = Union[ToolCall, Finish]


@dataclass(frozen=True)
class Observation:
    """What the agent learned from one tool call."""

    tool: str
    args: dict[str, object]
    result: object
    allowed: bool
    verdict: str | None = None


@dataclass(frozen=True)
class LoopContext:
    """Everything the agent may see when choosing its next action.

    Deliberately NOT the ledger: an agent must not be able to read or set
    provenance. It sees results, exactly as a real agent would.
    """

    task: str
    inputs: dict[str, object]
    manifests: dict[str, dict[str, object]]
    observations: tuple[Observation, ...]
    step: int
    seed: str


class Gate(Protocol):
    """Decides whether a proposed tool call may execute.

    The loop knows this much and no more. It does not know what a kernel is,
    what a policy is, or that governance exists — a gate is simply something
    that may return a decision. That is what makes governance a capability
    rather than a stage: with no gate there is no gate_decision event, no
    kernel_version on the producer, and nothing to strip out.

    Returning None means "no opinion", which the loop treats as allowed.
    """

    def decide(
        self,
        tool: str,
        manifest: dict[str, object],
        args: dict[str, object],
        arg_bindings: dict[str, str],
        ledger: ValueLedger,
        inputs: dict[str, object],
    ) -> dict[str, object] | None: ...


class AgentProgram(Protocol):
    """Chooses the next action. A scripted plan, or a model backend.

    A program MAY also expose `metrics() -> dict`, which the loop calls once the
    trial ends and merges beneath the platform's own measurements. That is how
    tokens and spend reach a trial: only the program talks to a provider, so
    only the program can measure what the call cost. The loop stays agnostic —
    it asks, it does not assume.
    """

    def next_action(self, ctx: LoopContext) -> Action: ...


def program_metrics(program: AgentProgram) -> dict[str, object]:
    """What the program measured, or nothing. Never invents a key: a program
    that reports no cost means the cost is UNMEASURED, and an invariant over it
    must error rather than pass on a zero nobody observed."""
    hook = getattr(program, "metrics", None)
    if not callable(hook):
        return {}
    measured = hook()
    return dict(measured) if measured else {}


@dataclass
class ScriptedProgram:
    """A deterministic plan — the loop's testable, no-LLM agent.

    `steps` may hold ToolCall/Finish directly, or callables taking the context
    so a step can branch on what the previous one returned.
    """

    steps: Sequence[Action | "object"]

    def next_action(self, ctx: LoopContext) -> Action:
        if ctx.step >= len(self.steps):
            return Finish()
        step = self.steps[ctx.step]
        if callable(step):
            return step(ctx)  # type: ignore[operator,no-any-return]
        return step  # type: ignore[return-value]


@dataclass(frozen=True)
class LoopOutcome:
    trace: dict[str, object]
    violation: bool
    task_success: bool
    metrics: dict[str, object]
    stop_reason: str


def run_loop_trial(
    scenario: dict[str, object],
    manifests: dict[str, dict[str, object]],
    condition: dict[str, object],
    gate: "Gate | None",
    run_id: str,
    seed: str,
    repeat_index: int,
    program: AgentProgram,
    host: SimulatedToolHost | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> LoopOutcome:
    """Run one trial as a general multi-step loop → trace/v1.

    `gate` may be None (no governance capability): nothing decides, no
    gate_decision is emitted, and the producer names no kernel_version.
    """
    started = time.monotonic()
    inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
    injection: dict[str, object] = scenario.get("injection") or {}  # type: ignore[assignment]
    if host is None:
        host = SimulatedToolHost(
            manifests=manifests,
            fixtures=scenario.get("fixtures", {}),  # type: ignore[arg-type]
            injection_text=str(injection.get("text", "")),
        )

    ledger = ValueLedger()
    events: list[dict[str, object]] = []
    observations: list[Observation] = []
    seq = 0
    tool_calls = 0
    model_calls = 0
    stop_reason = STOP_FINISHED

    while True:
        if len(events) >= max_steps:
            stop_reason = STOP_MAX_STEPS
            break
        ctx = LoopContext(
            task=str(scenario.get("task", "")), inputs=inputs, manifests=manifests,
            observations=tuple(observations), step=model_calls, seed=seed,
        )
        model_calls += 1
        action = program.next_action(ctx)
        if isinstance(action, Finish):
            if action.output is not None:
                events.append({"seq": seq, "node": "root", "type": "final_output"})
                seq += 1
            break
        if tool_calls >= max_tool_calls:
            stop_reason = STOP_MAX_TOOL_CALLS
            break
        if action.tool not in manifests:
            raise KeyError(
                f"agent proposed tool {action.tool!r} which the scenario does not declare"
            )

        arg_bindings = {
            name: _mint_argument(ledger, name, value, inputs)
            for name, value in action.args.items()
        }
        call_id = f"call_root_{seq}"
        events.append({
            "seq": seq, "node": "root", "type": "tool_call_intent",
            "tool": action.tool, "call_id": call_id, "arg_bindings": arg_bindings,
        })
        seq += 1
        tool_calls += 1

        decision = (
            gate.decide(action.tool, manifests[action.tool], action.args,
                        arg_bindings, ledger, inputs)
            if gate is not None else None
        )
        if decision is not None:
            events.append({
                "seq": seq, "node": "root", "type": "gate_decision",
                "call_id": call_id, "decision": decision,
            })
            seq += 1

        allowed = decision is None or decision["verdict"] == "ALLOW"
        result: object = None
        if allowed:
            result = host.execute(action.tool, action.args)
            produced = _mint_untrusted(ledger, manifests[action.tool], action.tool, result)
            events.append({
                "seq": seq, "node": "root", "type": "tool_result",
                "tool": action.tool, "produces_value_ids": produced,
            })
            seq += 1
        observations.append(Observation(
            tool=action.tool, args=dict(action.args), result=result,
            allowed=allowed,
            verdict=None if decision is None else str(decision["verdict"]),
        ))

    trace: dict[str, object] = {
        "schema_version": "trace/v1",
        "trace_id": (
            f"t_{run_id}_{scenario['name']}_{condition['id']}_{seed}_r{repeat_index}"
        ),
        "trial": {
            "run_id": run_id,
            "scenario_id": str(scenario["name"]),
            "condition_id": str(condition["id"]),
            "seed": seed,
            "repeat_index": repeat_index,
        },
        "producer": {
            "mode": "wrapped_code",
            "provenance_fidelity": "explicit_flow_tracked",
            **({"kernel_version": str(condition["kernel"])} if condition.get("kernel") else {}),
            "runtime": RUNTIME_ID,
        },
        "inputs_digest": world_digest(inputs, scenario.get("fixtures", {})),  # type: ignore[arg-type]
        "events": events,
        "values": ledger.values,
    }

    violation_predicate = scenario.get("violation")
    return LoopOutcome(
        trace=trace,
        violation=(
            evaluate(violation_predicate, trace, inputs)  # type: ignore[arg-type]
            if violation_predicate is not None else False
        ),
        task_success=evaluate(scenario["task_success"], trace, inputs),  # type: ignore[arg-type]
        # the program's measurements first, the platform's over them: a program
        # may report what only it can see (tokens, spend), never overwrite what
        # the runtime observed about its own execution
        metrics={
            **program_metrics(program),
            "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
            "steps": len(events),
            "tool_calls": tool_calls,
            "model_calls": model_calls,
        },
        stop_reason=stop_reason,
    )


def _mint_argument(
    ledger: ValueLedger, name: str, value: object, inputs: dict[str, object]
) -> str:
    """Assign provenance to one agent-supplied argument. See the module docstring.

    The declared-input check is on WHOLE-VALUE typed equality, deliberately: a
    substring or partial-structure match would let an attacker smuggle tainted
    data inside an otherwise-legitimate-looking argument and have it read as
    prompt_given.
    """
    for key, declared in inputs.items():
        if _same_value(value, declared):
            return ledger.mint_constant(value, f"prompt:{key}")
        if isinstance(declared, list) and any(_same_value(value, item) for item in declared):
            return ledger.mint_constant(value, f"prompt:{key}")
    untrusted = ledger.untrusted_ids()
    if untrusted:
        # conservative join: the agent produced this AFTER reading untrusted
        # content, so the value is untrusted-derived regardless of how it looks
        return ledger.mint_model_extraction(value, context_value_ids=tuple(untrusted))
    return ledger.mint_constant(value, f"model:{name}")


def _same_value(a: object, b: object) -> bool:
    """Typed equality: 1 is not True, and 1 is not "1"."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, str) != isinstance(b, str):
        return False
    return a == b


def _mint_untrusted(
    ledger: ValueLedger, manifest: dict[str, object], tool_id: str, result: object
) -> list[str]:
    from .runner import _mint_untrusted_fields

    return _mint_untrusted_fields(ledger, manifest, tool_id, result)


