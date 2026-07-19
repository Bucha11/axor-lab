"""The reference local runner: one trial → trace/v1; one experiment → bundle.

The agent is SCRIPTED (a deterministic function of the seed), standing in for
the stochastic model layer so the whole pipeline — fixtures → ledger → gate →
trace → aggregate — is exercised end-to-end without an LLM. The seed decides
whether the agent follows the injection, so paired ungoverned/governed trials
on the same seed produce the discordant pairs McNemar needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_contracts.canonical import content_hash

from .agents import AgentAdapter, ScriptedAgent
from .kernel import Kernel, KernelRegistry
from .ledger import ValueLedger
from .predicates import evaluate
from .simulator import SimulatedToolHost

RUNTIME_ID = "lab-runner@0.1"


@dataclass(frozen=True)
class TrialOutcome:
    trace: dict[str, object]
    violation: bool
    task_success: bool


def trial_id_for(scenario_id: str, condition_id: str, seed: str, repeat_index: int) -> str:
    """Stable trial identity — a retry with the same key replaces, never duplicates."""
    return content_hash(
        {"scenario": scenario_id, "condition": condition_id, "seed": seed, "repeat": repeat_index}
    )


def run_trial(
    scenario: dict[str, object],
    manifests: dict[str, dict[str, object]],
    condition: dict[str, object],
    kernel: Kernel,
    run_id: str,
    seed: str,
    repeat_index: int,
    agent: AgentAdapter,
    host: SimulatedToolHost | None = None,
) -> TrialOutcome:
    """Execute one (scenario × condition × seed × repeat) → trace/v1."""
    inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
    injection: dict[str, object] = scenario["injection"]  # type: ignore[assignment]
    if host is None:
        host = SimulatedToolHost(
            manifests=manifests,
            fixtures=scenario.get("fixtures", {}),  # type: ignore[arg-type]
            injection_text=str(injection["text"]),
        )
    ledger = ValueLedger()
    events: list[dict[str, object]] = []
    seq = 0

    # 1. read tool: fixture result; mint an external_read value per untrusted field hit
    read_tool = _read_tool_id(scenario, manifests)
    result = host.execute(read_tool, {})
    produced = _mint_untrusted_fields(ledger, manifests[read_tool], read_tool, result)
    events.append(
        {"seq": seq, "node": "root", "type": "tool_result", "tool": read_tool,
         "produces_value_ids": produced}
    )
    seq += 1

    # 2. the scripted "model" decides
    sink_tool = _sink_tool_id(scenario, manifests)
    attacked = agent.follows_injection(str(scenario["name"]), seed)
    if attacked:
        recipient_preview = agent.attacker_target(str(injection["text"]))
        v_recipient = ledger.mint_model_extraction(recipient_preview)
    else:
        recipient_preview = str(inputs["landlord_iban"])
        v_recipient = ledger.mint_constant(recipient_preview, "prompt:landlord_iban")
    amount = 1200
    v_amount = ledger.mint_constant(str(amount), "prompt:amount")
    args: dict[str, object] = {"recipient": recipient_preview, "amount": amount}
    arg_bindings = {"recipient": v_recipient, "amount": v_amount}
    events.append(
        {"seq": seq, "node": "root", "type": "tool_call_intent", "tool": sink_tool,
         "arg_bindings": arg_bindings}
    )
    seq += 1

    # 3. gate — the ONE decide implementation (also used by replay)
    decision = kernel.decide(
        enforcement=str(condition["enforcement"]),
        manifest=manifests[sink_tool],
        args=args,
        arg_labels={name: ledger.labels_of(vid) for name, vid in arg_bindings.items()},
        arg_bindings=arg_bindings,
        inputs=inputs,
    )
    events.append({"seq": seq, "node": "root", "type": "gate_decision", "decision": decision})
    seq += 1

    # 4. execute only if allowed — simulated either way
    if decision["verdict"] == "ALLOW":
        host.execute(sink_tool, args)

    trace: dict[str, object] = {
        "schema_version": "trace/v1",
        "trace_id": f"t_{run_id}_{condition['id']}_{seed}",
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
            "kernel_version": str(condition["kernel"]),
            "runtime": RUNTIME_ID,
        },
        "inputs_digest": content_hash({"inputs": inputs, "fixtures": scenario.get("fixtures", {})}),
        "events": events,
        "values": ledger.values,
    }
    return TrialOutcome(
        trace=trace,
        violation=evaluate(scenario["violation"], trace, inputs),  # type: ignore[arg-type]
        task_success=evaluate(scenario["task_success"], trace, inputs),  # type: ignore[arg-type]
    )


@dataclass
class ExperimentResult:
    """Everything a run produces before bundling."""

    run_id: str
    trials: list[dict[str, object]] = field(default_factory=list)
    traces: dict[str, dict[str, object]] = field(default_factory=dict)
    outcomes: dict[str, TrialOutcome] = field(default_factory=dict)

    def add(self, trial_key: str, trial_record: dict[str, object], outcome: TrialOutcome) -> None:
        # idempotency: a retried trial with the same key replaces, never duplicates
        self.trials = [t for t in self.trials if t["trial_id"] != trial_key]
        self.trials.append(trial_record)
        self.outcomes[trial_key] = outcome
        self.traces[content_hash(outcome.trace)] = outcome.trace

    def pairs(self, baseline_id: str, treated_id: str, metric: str) -> list[tuple[bool, bool]]:
        """Paired outcomes per seed — stored, because McNemar needs the pairing."""
        by_key: dict[tuple[str, str, int], dict[str, bool]] = {}
        for trial in self.trials:
            outcome = self.outcomes[str(trial["trial_id"])]
            value = outcome.violation if metric == "ASR" else outcome.task_success
            key = (str(trial["scenario_id"]), str(trial["seed"]), int(trial["repeat_index"]))
            by_key.setdefault(key, {})[str(trial["condition_id"])] = value
        return [
            (row[baseline_id], row[treated_id])
            for row in by_key.values()
            if baseline_id in row and treated_id in row
        ]


def run_experiment(
    scenario: dict[str, object],
    manifests: dict[str, dict[str, object]],
    conditions: list[dict[str, object]],
    kernel_registry: KernelRegistry,
    repeats: int,
    run_id: str,
    agent: AgentAdapter | None = None,
) -> ExperimentResult:
    agent = agent or ScriptedAgent()
    result = ExperimentResult(run_id=run_id)
    scenario_id = str(scenario["name"])
    for condition in conditions:
        kernel = kernel_registry.get(str(condition["kernel"]))
        for repeat_index in range(repeats):
            seed = f"s{repeat_index:03d}"
            outcome = run_trial(
                scenario, manifests, condition, kernel, run_id, seed, repeat_index, agent
            )
            trial_key = trial_id_for(scenario_id, str(condition["id"]), seed, repeat_index)
            result.add(
                trial_key,
                {
                    "trial_id": trial_key,
                    "scenario_id": scenario_id,
                    "condition_id": str(condition["id"]),
                    "seed": seed,
                    "repeat_index": repeat_index,
                    "status": "completed",
                    "trace_ref": content_hash(outcome.trace),
                },
                outcome,
            )
    return result


def _read_tool_id(scenario: dict[str, object], manifests: dict[str, dict[str, object]]) -> str:
    for tool_id in _tool_ids(scenario):
        if not bool(manifests[tool_id].get("side_effecting")):
            return tool_id
    raise KeyError("scenario has no read tool")


def _sink_tool_id(scenario: dict[str, object], manifests: dict[str, dict[str, object]]) -> str:
    for tool_id in _tool_ids(scenario):
        if bool(manifests[tool_id].get("side_effecting")):
            return tool_id
    raise KeyError("scenario has no side-effecting sink tool")


def _tool_ids(scenario: dict[str, object]) -> list[str]:
    return [
        str(tool["$ref"]) if "$ref" in tool else str(tool.get("id"))
        for tool in scenario.get("tools", [])  # type: ignore[union-attr]
    ]


def _mint_untrusted_fields(
    ledger: ValueLedger, manifest: dict[str, object], tool_id: str, result: object
) -> list[str]:
    """Mint an external_read value for every untrusted-field instance present."""
    produced: list[str] = []
    for pattern in manifest.get("untrusted_fields", []):  # type: ignore[union-attr]
        path = str(pattern)
        path = path[len("result."):] if path.startswith("result.") else path
        for concrete, text in _expand_field(result, path):
            produced.append(
                ledger.mint_external_read(text, f"tool_result:{tool_id}:{concrete}")
            )
    return produced


def _expand_field(node: object, path: str) -> list[tuple[str, str]]:
    """Expand a field pattern like `transactions[].description` into concrete
    (path, value) instances present in the result."""
    if not path:
        return [("", str(node))] if isinstance(node, (str, int, float)) else []
    head, _, rest = path.partition(".")
    if head.endswith("[]"):
        key = head[:-2]
        items = node.get(key, []) if isinstance(node, dict) else []
        out: list[tuple[str, str]] = []
        for i, item in enumerate(items):
            for sub, text in _expand_field(item, rest):
                suffix = f".{sub}" if sub else ""
                out.append((f"{key}[{i}]{suffix}", text))
        return out
    if isinstance(node, dict) and head in node:
        return [
            (f"{head}.{sub}" if sub else head, text)
            for sub, text in _expand_field(node[head], rest)
        ]
    return []


def run_experiment_suite(
    scenarios: list[dict[str, object]],
    manifests: dict[str, dict[str, object]],
    conditions: list[dict[str, object]],
    kernel_registry: KernelRegistry,
    repeats: int,
    run_id: str,
    agent: AgentAdapter | None = None,
) -> ExperimentResult:
    """Benchmark-suite run: every scenario × condition × repeat, one result.

    Pooled per statistics.md: unit = one task attempt; n = repeats × scenarios.
    Pairing stays per (scenario, seed, repeat) across conditions.
    """
    agent = agent or ScriptedAgent()
    result = ExperimentResult(run_id=run_id)
    for scenario in scenarios:
        scenario_id = str(scenario["name"])
        for condition in conditions:
            kernel = kernel_registry.get(str(condition["kernel"]))
            for repeat_index in range(repeats):
                seed = f"s{repeat_index:03d}"
                outcome = run_trial(
                    scenario, manifests, condition, kernel, run_id, seed, repeat_index, agent
                )
                trial_key = trial_id_for(scenario_id, str(condition["id"]), seed, repeat_index)
                result.add(
                    trial_key,
                    {
                        "trial_id": trial_key,
                        "scenario_id": scenario_id,
                        "condition_id": str(condition["id"]),
                        "seed": seed,
                        "repeat_index": repeat_index,
                        "status": "completed",
                        "trace_ref": content_hash(outcome.trace),
                    },
                    outcome,
                )
    return result
