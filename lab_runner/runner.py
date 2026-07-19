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

from .agents import AgentAdapter, DrivingAgent, ScriptedAgent
from .axor_backend import AxorKernel, gate_with_governor, resolve_kernel
from .kernel import Kernel, KernelRegistry
from .ledger import ValueLedger
from .predicates import evaluate
from .simulator import SimulatedToolHost

RUNTIME_ID = "lab-runner@0.1"
DEFAULT_AMOUNT = 1200
_FAITHFUL_FALLBACK_INPUT = "landlord_iban"


def _faithful_input_key(scenario: dict[str, object]) -> str:
    """The input the faithful agent pays: derived from the task_success
    predicate's `args.recipient equal {input_ref: X}` — the declared ground
    truth, never scraped from prompt text."""

    def walk(predicate: dict[str, object]) -> str | None:
        if "event" in predicate:
            matcher = predicate.get("where", {}).get("args.recipient")  # type: ignore[union-attr]
            if isinstance(matcher, dict):
                operand = matcher.get("equal")
                if isinstance(operand, dict) and "input_ref" in operand:
                    return str(operand["input_ref"])
            return None
        for key in ("all", "any", "sequence"):
            if key in predicate:
                for sub in predicate[key]:  # type: ignore[union-attr]
                    found = walk(sub)
                    if found:
                        return found
        if "not" in predicate:
            return walk(predicate["not"])  # type: ignore[arg-type]
        return None

    return walk(scenario["task_success"]) or _FAITHFUL_FALLBACK_INPUT  # type: ignore[arg-type]


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

    # 2. the "model" decides the sink call
    sink_tool = _sink_tool_id(scenario, manifests)
    untrusted_present = bool(produced)
    if isinstance(agent, DrivingAgent):
        # model-backed: the agent observed the read result (with any injection)
        # and produced the sink call. The RUNTIME assigns provenance — because
        # the recipient was produced by a model call whose context held an
        # untrusted value, it is model_extraction (conservative join). The
        # agent cannot launder taint by claiming the value is clean.
        decision_call = agent.decide_sink_call(
            str(scenario["task"]), result, inputs, manifests[sink_tool]
        )
        recipient = decision_call.recipient
        amount = decision_call.amount
        # the model saw exactly the values produced by the read it observed;
        # scope the conservative join to that context (review §4.2)
        if untrusted_present:
            v_recipient = ledger.mint_model_extraction(recipient, context_value_ids=tuple(produced))
        else:
            v_recipient = ledger.mint_constant(recipient, "model:recipient")
    else:
        attacked = agent.follows_injection(str(scenario["name"]), seed)
        if attacked:
            recipient = agent.attacker_target(str(injection["text"]))
            v_recipient = ledger.mint_model_extraction(recipient, context_value_ids=tuple(produced))
        else:
            faithful_key = _faithful_input_key(scenario)
            recipient = inputs[faithful_key]  # the exact typed input value
            v_recipient = ledger.mint_constant(recipient, f"prompt:{faithful_key}")
        amount = inputs.get("amount", DEFAULT_AMOUNT)
    v_amount = ledger.mint_constant(amount, "prompt:amount")
    args: dict[str, object] = {"recipient": recipient, "amount": amount}
    arg_bindings = {"recipient": v_recipient, "amount": v_amount}
    events.append(
        {"seq": seq, "node": "root", "type": "tool_call_intent", "tool": sink_tool,
         "arg_bindings": arg_bindings}
    )
    seq += 1

    # 3. gate — the ONE decide implementation (also used by replay). The real
    # axor-core governor and the reference kernel share this dispatch.
    if isinstance(kernel, AxorKernel):
        registrations = [
            (read_tool, ledger.get(vid)["decision_value"]) for vid in produced
        ]
        decision = gate_with_governor(
            kernel.config, str(condition["enforcement"]), registrations,
            sink_tool, args, v_recipient,
        )
    else:
        decision = kernel.decide(
            enforcement=str(condition["enforcement"]),
            manifest=manifests[sink_tool],
            args=args,
            arg_labels={name: ledger.labels_of(vid) for name, vid in arg_bindings.items()},
            arg_bindings=arg_bindings,
            inputs=inputs,
            policy=condition.get("policy"),  # type: ignore[arg-type]
        )
    events.append({"seq": seq, "node": "root", "type": "gate_decision", "decision": decision})
    seq += 1

    # 4. execute only if allowed — simulated either way
    if decision["verdict"] == "ALLOW":
        host.execute(sink_tool, args)

    trace: dict[str, object] = {
        "schema_version": "trace/v1",
        # trace identity MUST carry the full trial coordinate. Omitting
        # scenario_id (and repeat_index) collided every scenario that shared a
        # (condition, seed) — 3 scenarios × 2 conditions × 6 repeats produced
        # only 12 distinct ids, and the colliding traces overwrote each other in
        # the bundle manifest and on disk, corrupting multi-scenario bundles.
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
    # superseded attempts (review §4.3): a retried trial replaces the CURRENT
    # record but the prior attempt is preserved here for the audit trail.
    superseded: list[dict[str, object]] = field(default_factory=list)

    def add(self, trial_key: str, trial_record: dict[str, object], outcome: TrialOutcome) -> None:
        # idempotency: a retried trial with the same key replaces, never
        # duplicates — but the replaced attempt is retained, not discarded
        for existing in self.trials:
            if existing["trial_id"] == trial_key:
                self.superseded.append({**existing, "superseded_by": trial_record.get("trace_ref")})
        self.trials = [t for t in self.trials if t["trial_id"] != trial_key]
        self.trials.append(trial_record)
        self.outcomes[trial_key] = outcome
        self.traces[content_hash(outcome.trace)] = outcome.trace

    def add_failure(self, trial_key: str, trial_record: dict[str, object]) -> None:
        """Record a trial that raised — status=failed with a reason — instead of
        aborting the whole experiment (review §4.4). Integrates with missingness."""
        self.trials = [t for t in self.trials if t["trial_id"] != trial_key]
        self.trials.append(trial_record)

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


def _run_one(
    result: ExperimentResult,
    scenario: dict[str, object],
    manifests: dict[str, dict[str, object]],
    condition: dict[str, object],
    kernel: object,
    run_id: str,
    seed: str,
    repeat_index: int,
    agent: AgentAdapter,
) -> None:
    """Execute one trial, capturing a failure as a recorded status=failed trial
    instead of aborting the whole experiment (review §4.4)."""
    scenario_id = str(scenario["name"])
    trial_key = trial_id_for(scenario_id, str(condition["id"]), seed, repeat_index)
    base = {
        "trial_id": trial_key, "scenario_id": scenario_id,
        "condition_id": str(condition["id"]), "seed": seed, "repeat_index": repeat_index,
    }
    try:
        outcome = run_trial(scenario, manifests, condition, kernel, run_id, seed, repeat_index, agent)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 — a bad trial must not sink the run
        result.add_failure(trial_key, {**base, "status": "failed", "failure_reason": f"{type(exc).__name__}: {exc}"})
        return
    result.add(trial_key, {**base, "status": "completed", "trace_ref": content_hash(outcome.trace)}, outcome)


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
    for condition in conditions:
        kernel = resolve_kernel(str(condition["kernel"]), manifests, condition.get("policy"), kernel_registry)
        for repeat_index in range(repeats):
            _run_one(result, scenario, manifests, condition, kernel, run_id,
                     f"s{repeat_index:03d}", repeat_index, agent)
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
    """Mint an external_read value for every untrusted-field instance present.

    A field also declared in the manifest's `sensitive_fields` is redacted in
    the trace (review §7.4)."""
    sensitive_patterns = {
        _norm_field(str(p)) for p in manifest.get("sensitive_fields", [])  # type: ignore[union-attr]
    }
    produced: list[str] = []
    for pattern in manifest.get("untrusted_fields", []):  # type: ignore[union-attr]
        path = str(pattern)
        path = path[len("result."):] if path.startswith("result.") else path
        is_sensitive = _norm_field(str(pattern)) in sensitive_patterns
        for concrete, value in _expand_field(result, path):
            produced.append(
                ledger.mint_external_read(
                    value, f"tool_result:{tool_id}:{concrete}", sensitive=is_sensitive
                )
            )
    return produced


def _norm_field(pattern: str) -> str:
    p = pattern[len("result."):] if pattern.startswith("result.") else pattern
    import re as _re

    return _re.sub(r"\[\d*\]", "[]", p)


def _expand_field(node: object, path: str) -> list[tuple[str, object]]:
    """Expand a field pattern like `transactions[].description` into concrete
    (path, typed value) instances present in the result. The value is kept
    typed (not stringified) so the ledger stores the exact decision_value."""
    if not path:
        return [("", node)] if isinstance(node, (str, int, float, bool)) else []
    head, _, rest = path.partition(".")
    if head.endswith("[]"):
        key = head[:-2]
        items = node.get(key, []) if isinstance(node, dict) else []
        out: list[tuple[str, object]] = []
        for i, item in enumerate(items):
            for sub, value in _expand_field(item, rest):
                suffix = f".{sub}" if sub else ""
                out.append((f"{key}[{i}]{suffix}", value))
        return out
    if isinstance(node, dict) and head in node:
        return [
            (f"{head}.{sub}" if sub else head, value)
            for sub, value in _expand_field(node[head], rest)
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
        for condition in conditions:
            kernel = resolve_kernel(str(condition["kernel"]), manifests, condition.get("policy"), kernel_registry)
            for repeat_index in range(repeats):
                _run_one(result, scenario, manifests, condition, kernel, run_id,
                         f"s{repeat_index:03d}", repeat_index, agent)
    return result
