"""The reference local runner: one trial → trace/v1; one experiment → bundle.

The agent is SCRIPTED (a deterministic function of the seed), standing in for
the stochastic model layer so the whole pipeline — fixtures → ledger → gate →
trace → aggregate — is exercised end-to-end without an LLM. The seed decides
whether the agent follows the injection, so paired ungoverned/governed trials
on the same seed produce the discordant pairs McNemar needs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from lab_contracts.canonical import content_hash, world_digest

from .agents import AgentAdapter, DrivingAgent, ScriptedAgent
from .axor_backend import AxorKernel, gate_with_governor, resolve_kernel
from .errors import CostCeilingReached
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


def trial_id_for(
    run_id: str, scenario_id: str, condition_id: str, seed: str, repeat_index: int
) -> str:
    """Stable trial identity, SCOPED TO THE RUN.

    Includes run_id so two runs of the same experiment with different agents /
    models (run_id carries the agent fingerprint) do not mint identical trial
    ids — otherwise distinct experiments would look like retries of one trial
    when merged (review r3). Within one run a retry of the same coordinate still
    yields the same id (idempotent replace)."""
    return content_hash(
        {"run": run_id, "scenario": scenario_id, "condition": condition_id,
         "seed": seed, "repeat": repeat_index}
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
            str(scenario["task"]), result, inputs, manifests[sink_tool],
            scenario_id=str(scenario["name"]),
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
    # a deterministic call_id correlates this intent with its gate_decision, so
    # replay pairs them by id (not just node FIFO) and can detect an intent with
    # no decision or a duplicate decision (review r2 §replay)
    call_id = f"call_root_{seq}"
    events.append(
        {"seq": seq, "node": "root", "type": "tool_call_intent", "tool": sink_tool,
         "call_id": call_id, "arg_bindings": arg_bindings}
    )
    seq += 1

    # 3. gate — the ONE decide implementation (also used by replay). The real
    # axor-core governor and the reference kernel share this dispatch.
    if isinstance(kernel, AxorKernel):
        # read the RAW runtime value (in-memory), not decision_value off the
        # serialized dict — a sensitive value is redacted there and has no
        # decision_value, which used to KeyError and fail the whole trial (r7)
        registrations = [
            (read_tool, ledger.runtime_value(vid)) for vid in produced
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
    events.append(
        {"seq": seq, "node": "root", "type": "gate_decision", "call_id": call_id,
         "decision": decision}
    )
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
        "inputs_digest": world_digest(inputs, scenario.get("fixtures", {})),  # type: ignore[arg-type]
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
    # set when a run-wide cost ceiling stopped the run early (review r11); the
    # partial result flows through missingness/analysis honestly
    stopped_reason: str | None = None

    def add(self, trial_key: str, trial_record: dict[str, object], outcome: TrialOutcome) -> None:
        # idempotency: a retried trial with the same key replaces, never
        # duplicates — the replaced attempt is retained in the audit log
        new_ref = content_hash(outcome.trace)
        self._supersede(trial_key, superseded_by=new_ref)
        self.trials.append(trial_record)
        self.outcomes[trial_key] = outcome
        self.traces[new_ref] = outcome.trace

    def add_failure(self, trial_key: str, trial_record: dict[str, object]) -> None:
        """Record a trial that raised — status=failed with a reason — instead of
        aborting the whole experiment (review §4.4). Integrates with missingness."""
        # a failed RETRY of a prior completed attempt also supersedes it: the old
        # trace leaves the publishable set and its stale outcome is cleared
        self._supersede(trial_key, superseded_by=None)
        self.trials.append(trial_record)

    def _supersede(self, trial_key: str, superseded_by: str | None) -> None:
        """Retire any current attempt for this trial key into the superseded
        audit log and REMOVE its trace from the publishable set.

        A stochastic retry produces a new trace with a DIFFERENT content hash;
        if the prior trace stayed in `traces`, the new trial would reference the
        new trace while the old one dangled as an orphan — verify_bundle rejects
        a bundle whose traces don't match its trials (review r8). Superseded
        attempts (and their traces) live only in `superseded`, which is an audit
        record outside the publishable bundle, so both attempts are preserved
        without corrupting the integrity graph. The stale outcome is cleared so
        analysis never scores a superseded attempt."""
        kept: list[dict[str, object]] = []
        for existing in self.trials:
            if existing["trial_id"] == trial_key:
                old_ref = str(existing.get("trace_ref", ""))
                old_trace = self.traces.pop(old_ref, None)
                entry = {**existing, "superseded_by": superseded_by}
                if old_trace is not None:
                    entry["trace"] = old_trace  # keep the attempt's evidence in the log
                self.superseded.append(entry)
            else:
                kept.append(existing)
        self.trials = kept
        self.outcomes.pop(trial_key, None)

    def pairs(self, baseline_id: str, treated_id: str, metric: str) -> list[tuple[bool, bool]]:
        """Paired outcomes per seed — stored, because McNemar needs the pairing."""
        by_key: dict[tuple[str, str, int], dict[str, bool]] = {}
        for trial in self.trials:
            # a failed trial has no outcome — skip it, don't KeyError (review r7).
            # It is excluded from the pair, which is exactly what missingness
            # accounts for; one bad trial must not sink the whole analysis.
            outcome = self.outcomes.get(str(trial["trial_id"]))
            if outcome is None:
                continue
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
    execution_order: int = 0,
) -> None:
    """Execute one trial, capturing a failure as a recorded status=failed trial
    instead of aborting the whole experiment (review §4.4)."""
    scenario_id = str(scenario["name"])
    trial_key = trial_id_for(run_id, scenario_id, str(condition["id"]), seed, repeat_index)
    base = {
        "trial_id": trial_key, "scenario_id": scenario_id,
        "condition_id": str(condition["id"]), "seed": seed, "repeat_index": repeat_index,
        "execution_order": execution_order,
    }
    try:
        outcome = run_trial(scenario, manifests, condition, kernel, run_id, seed, repeat_index, agent)  # type: ignore[arg-type]
    except CostCeilingReached:
        # a budget stop halts the WHOLE run — it is NOT a per-trial failure to
        # capture-and-continue (that would keep spending past the ceiling). Let
        # it propagate to run_experiment_suite, which records stopped_reason.
        raise
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
    order = 0
    for condition in conditions:
        kernel = resolve_kernel(str(condition["kernel"]), manifests, condition.get("policy"), kernel_registry)
        for repeat_index in range(repeats):
            _run_one(result, scenario, manifests, condition, kernel, run_id,
                     f"s{repeat_index:03d}", repeat_index, agent, execution_order=order)
            order += 1
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
    budget_check: "Callable[[], str | None] | None" = None,
) -> ExperimentResult:
    """Benchmark-suite run: every scenario × condition × repeat, one result.

    Pooled per statistics.md: unit = one task attempt; n = repeats × scenarios.
    Pairing stays per (scenario, seed, repeat) across conditions.

    `budget_check` (optional) is called AFTER each trial; if it returns a reason
    string, the run stops immediately — before the next provider call — so a
    run-wide cost ceiling is a hard stop, not an advisory print (review r11).
    """
    agent = agent or ScriptedAgent()
    result = ExperimentResult(run_id=run_id)
    # materialize the FULL plan up front so a cost stop can record the trials
    # that never ran — otherwise missingness computes over only the trials that
    # DID run and reports e.g. n=1/1 for a 100-trial plan stopped after one
    # (review r13). Every not-yet-run trial is recorded status=excluded with
    # failure_reason=cost_ceiling, so the denominator stays honest.
    # BLOCK-BALANCED + COUNTERBALANCED order (review r14/r15): iterate
    # scenario → repeat → condition, so each (scenario, repeat) block runs ALL its
    # conditions back to back (a cost stop then leaves at most one block
    # incomplete, keeping the maximum number of complete matched pairs). Within a
    # block the condition order ALTERNATES every repeat (baseline→governed,
    # governed→baseline, …) so the governance effect is not systematically
    # confounded with position/time-in-run for a live model. The execution order
    # is recorded on each trial so the counterbalancing is auditable.
    plan = [
        (scenario, condition, repeat_index)
        for scenario in scenarios
        for repeat_index in range(repeats)
        for condition in (conditions if repeat_index % 2 == 0 else list(reversed(conditions)))
    ]

    def _exclude_remaining(from_index: int, reason: str) -> None:
        recorded = {str(t["trial_id"]) for t in result.trials}
        for offset, (scenario, condition, repeat_index) in enumerate(plan[from_index:]):
            seed = f"s{repeat_index:03d}"
            trial_key = trial_id_for(
                run_id, str(scenario["name"]), str(condition["id"]), seed, repeat_index
            )
            if trial_key in recorded:
                continue  # a mid-trial stop may have partially recorded this one
            result.add_failure(trial_key, {
                "trial_id": trial_key, "scenario_id": str(scenario["name"]),
                "condition_id": str(condition["id"]), "seed": seed,
                "repeat_index": repeat_index, "status": "excluded",
                "execution_order": from_index + offset,
                "failure_reason": f"cost_ceiling: {reason}",
            })

    # cost check BEFORE the first trial — if usage already sits at a ceiling we
    # must not run even one paid trial (review r12); with a fresh run this is a
    # no-op, but it makes "before the first call" true rather than aspirational
    if budget_check is not None:
        reason = budget_check()
        if reason is not None:
            result.stopped_reason = reason
            _exclude_remaining(0, reason)  # NOTHING ran → n=0/total, not n=0/0
            return result
    kernels: dict[str, object] = {}
    for index, (scenario, condition, repeat_index) in enumerate(plan):
        cid = str(condition["id"])
        if cid not in kernels:
            kernels[cid] = resolve_kernel(
                str(condition["kernel"]), manifests, condition.get("policy"), kernel_registry
            )
        try:
            _run_one(result, scenario, manifests, condition, kernels[cid], run_id,
                     f"s{repeat_index:03d}", repeat_index, agent, execution_order=index)
        except CostCeilingReached as stop:
            # the guard fired mid-trial, BEFORE a provider call — stop the whole
            # run and exclude THIS trial plus every remaining one (review r12/r13)
            result.stopped_reason = str(stop)
            _exclude_remaining(index, stop.reason)
            return result
        if budget_check is not None:
            reason = budget_check()
            if reason is not None:
                # this trial completed; exclude only the ones AFTER it
                result.stopped_reason = reason
                _exclude_remaining(index + 1, reason)
                return result
    return result
