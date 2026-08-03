"""The reference local runner: one trial → trace/v1; one experiment → bundle.

The agent is SCRIPTED (a deterministic function of the seed), standing in for
the stochastic model layer so the whole pipeline — fixtures → ledger → gate →
trace → aggregate — is exercised end-to-end without an LLM. The seed decides
whether the agent follows the injection, so paired ungoverned/governed trials
on the same seed produce the discordant pairs McNemar needs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from lab_contracts.canonical import (
    CONFIG_COMPILER_VERSION,
    content_hash,
    runtime_config_hash,
    world_digest,
)

from .agents import AgentAdapter, DrivingAgent, ScriptedAgent
from .axor_backend import AxorKernel, gate_with_governor, resolve_kernel
from .errors import CostCeilingReached
from .kernel import Kernel, KernelRegistry, default_registry
from .ledger import ValueLedger
from .predicates import evaluate
from .simulator import SimulatedToolHost
from .verdicts import executed_under

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
    # what the trial COST — recorded at execution, per bundle/v1 trial.metrics.
    # A metric that was not measured is ABSENT from this dict, never 0: a
    # metric_threshold regression over a missing measurement must error, not pass.
    metrics: dict[str, object] = field(default_factory=dict)


def _trial_metrics(
    started: float, events: list[dict[str, object]], host: SimulatedToolHost
) -> dict[str, object]:
    """Platform-level per-trial metrics.

    Only what this runner can honestly observe. A scripted/simulated agent
    spends no money and makes no provider calls, so `cost_usd`, `tokens_in` and
    `tokens_out` are deliberately omitted rather than reported as 0 — a suite
    that runs a real model backend fills them in, and a budget invariant over an
    unmeasured cost must error rather than silently pass.
    """
    duration_ms = (time.monotonic() - started) * 1000.0
    tool_calls = sum(
        1 for e in events
        if e.get("type") in ("tool_call_intent", "tool_result")
    )
    return {
        "duration_ms": round(duration_ms, 3),
        "steps": len(events),
        "tool_calls": tool_calls,
    }


UNGOVERNED_CONDITION_ID = "ungoverned"


def observe_only_condition(kernel: str | None = None) -> dict[str, object]:
    """The default single arm: UNGOVERNED — wrapped, observed, not enforced.

    `enforcement: "off"` means the gates do not deny. It does NOT mean no kernel
    ran: condition/v1 says so outright — "off = observe-only (proxy records,
    enforces nothing). Observation is always on regardless." The kernel still
    evaluates every call and registers its outputs, which is what builds the
    value ledger an EvidenceCase is read from.

    That distinction is the whole reason this carries a kernel. If an ungoverned
    run bypassed the core, the agent would not be wrapped, and switching
    governance on later would be a re-integration rather than flipping
    `enforcement` — and the ungoverned/governed comparison would be measuring
    two different machines rather than one machine under two policies.

    There is deliberately no kernel-free variant. A run nothing observed cannot
    produce a conformant trace anyway — trace/v1 rules out black-box producers
    precisely because they "cannot emit value lineage" — and locally the
    reference kernel is stdlib and always available, so choosing it costs
    nothing and buys the value ledger, recorded verdicts and exact replay that a
    kernel-free arm threw away.
    """
    resolved = kernel or _default_kernel_version()
    condition: dict[str, object] = {
        "schema_version": "condition/v1",
        "id": UNGOVERNED_CONDITION_ID,
        "label": "ungoverned",
        "enforcement": "off",
    }
    if resolved:
        condition["kernel"] = resolved
    return condition


def _default_kernel_version() -> str | None:
    """The reference kernel: stdlib, always resolvable, no axor-core needed.

    Deliberately NOT the installed real kernel. A locally-synthesized arm must
    be resolvable on any machine, and pinning `axor-core@X` here would make a
    default arm unrunnable wherever that exact build is absent — resolve_kernel
    refuses to substitute under a real-kernel label, and rightly so. A caller
    that wants the real kernel names it (as the connected-runtime path does,
    where the runtime carries it).
    """
    return REFERENCE_KERNEL_VERSION


REFERENCE_KERNEL_VERSION = "reference_taint_floor_kernel"


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
    """Execute one (scenario × condition × seed × repeat) → trace/v1.

    `kernel` may be None: governance is a capability, not a stage (Suite
    Platform RFC §10). With no kernel no gate runs, no gate_decision event is
    emitted and the producer names no kernel_version — the three absences that
    `verify_bundle` checks together. `scenario["injection"]` is likewise
    optional: a budget/performance scenario has no attack model.
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
        # a scenario with no injection has nothing for the scripted agent to
        # follow — it always acts faithfully
        attacked = bool(injection) and agent.follows_injection(str(scenario["name"]), seed)
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
    if kernel is None:
        # No governance capability: no gate, no verdict, no gate_decision event.
        # The call is simulated exactly as an ALLOW would simulate it — the
        # difference is that nothing DECIDED to allow it, and the trace says so
        # by carrying no decision and no kernel_version.
        decision = None
    elif isinstance(kernel, AxorKernel):
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
    if decision is not None:
        events.append(
            {"seq": seq, "node": "root", "type": "gate_decision", "call_id": call_id,
             "decision": decision}
        )
        seq += 1

    # 4. execute only if allowed — simulated either way. With no gate there is
    # nothing to withhold execution, so the call proceeds; and an observe-only
    # arm proceeds through a recorded DENY, which is what makes it a record of
    # what the agent ACTUALLY did rather than of what it was permitted to do.
    if decision is None or executed_under(decision):
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
            # omitted when no gate ran — see _verify_kernel_binding: absence
            # here, on the condition, and of gate_decision events is one fact
            **({"kernel_version": str(condition["kernel"])} if condition.get("kernel") else {}),
            "runtime": RUNTIME_ID,
        },
        "inputs_digest": world_digest(inputs, scenario.get("fixtures", {})),  # type: ignore[arg-type]
        "events": events,
        "values": ledger.values,
    }
    # `violation` is optional — a scenario with no attack model has no breach to
    # evaluate, and False here means "no breach", which is the truth.
    violation_predicate = scenario.get("violation")
    return TrialOutcome(
        trace=trace,
        violation=(
            evaluate(violation_predicate, trace, inputs)  # type: ignore[arg-type]
            if violation_predicate is not None else False
        ),
        task_success=evaluate(scenario["task_success"], trace, inputs),  # type: ignore[arg-type]
        metrics=_trial_metrics(started, events, host),
    )


@dataclass
class ExperimentResult:
    """Everything a run produces before bundling."""

    run_id: str
    trials: list[dict[str, object]] = field(default_factory=list)
    traces: dict[str, dict[str, object]] = field(default_factory=dict)
    outcomes: dict[str, TrialOutcome] = field(default_factory=dict)
    # the conditions the run ACTUALLY executed under. Normally the ones passed
    # in, but a governance-free run declares none and the runner synthesizes the
    # observe-only arm — which the trials then reference by id. The caller needs
    # it to build a bundle whose trials resolve, so it is reported rather than
    # left as an internal detail.
    conditions: list[dict[str, object]] = field(default_factory=list)
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
        # the EXECUTION this unit belongs to (= run_id): the experimental-unit
        # coordinate is (execution_id, scenario, condition, seed, repeat), so two
        # trials that share a (scenario, seed, repeat) but ran in different runs are
        # distinct units — and duplicate coordinates within one execution are a hard
        # error rather than a last-write-wins overwrite in the statistics (review r21)
        "execution_id": run_id,
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
    # record the CONCRETE runtime config identity AT EXECUTION (review r19): the
    # governor config this trial actually ran under, hashed by the same process
    # that ran it. A later CP export proves the runtime config it recommends is the
    # one recorded on the trial — not one reconstructed at export time.
    record = {**base, "status": "completed", "trace_ref": content_hash(outcome.trace),
              "metrics": outcome.metrics}
    if condition.get("kernel"):
        record.update({
            "runtime_config_hash": runtime_config_hash(
                str(condition["kernel"]), condition.get("policy"),
                list(manifests.values()), scenario.get("inputs", {}),  # type: ignore[arg-type]
            ),
            "config_compiler_version": CONFIG_COMPILER_VERSION,
            # the hash above was computed by THIS process AT execution — declare it so
            # config_provenance can distinguish a genuinely execution-recorded hash from
            # one reconstructed post-hoc (an imported incident) or asserted by a
            # hand-built bundle (review r21)
            "runtime_provenance": "recorded_at_execution",
            # the ACTUAL resolved backend's behavior identity — not just the declared
            # condition.kernel string — so a registry that returns a behavior-changed
            # kernel (e.g. taint_floor off) under a version label is auditable (review r20)
            "resolved_kernel_fingerprint": str(
                getattr(kernel, "behavior_version", str(getattr(kernel, "version", "")))
            ),
        })
    result.add(trial_key, record, outcome)


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
    if not conditions:
        # a synthesized arm must be resolvable on ANY machine, so it names the
        # reference kernel and brings a registry that knows it
        conditions = [observe_only_condition()]
        kernel_registry = default_registry((REFERENCE_KERNEL_VERSION,))
    result = ExperimentResult(run_id=run_id, conditions=list(conditions))
    order = 0
    for condition in conditions:
        kernel = (
            resolve_kernel(
                str(condition["kernel"]), manifests, condition.get("policy"), kernel_registry,
                scenario.get("inputs", {}),
            )
            if condition.get("kernel") else None
        )
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
                    value, f"tool_result:{tool_id}:{concrete}",
                    sensitive=is_sensitive, produced_by=tool_id,
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
    if not conditions:
        # a synthesized arm must be resolvable on ANY machine, so it names the
        # reference kernel and brings a registry that knows it
        conditions = [observe_only_condition()]
        kernel_registry = default_registry((REFERENCE_KERNEL_VERSION,))
    result = ExperimentResult(run_id=run_id, conditions=list(conditions))
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
                "execution_id": run_id,
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
    kernels: dict[tuple[str, str], object] = {}
    for index, (scenario, condition, repeat_index) in enumerate(plan):
        cid = str(condition["id"])
        # key the kernel by (condition, scenario): an input-backed allowlist
        # ($inputs.x) resolves against THIS scenario's inputs, so two scenarios
        # under the same condition must not share a kernel with a stale allowlist
        # expansion (review r16)
        cache_key = (cid, str(scenario["name"]))
        if cache_key not in kernels:
            # A condition with no kernel is an observe-only arm: no gate, and
            # crucially NO KERNEL RESOLUTION — a governance-free run must not
            # depend on a kernel registry being able to resolve anything.
            kernels[cache_key] = (
                resolve_kernel(
                    str(condition["kernel"]), manifests, condition.get("policy"),
                    kernel_registry, scenario.get("inputs", {}),
                )
                if condition.get("kernel") else None
            )
        try:
            _run_one(result, scenario, manifests, condition, kernels[cache_key], run_id,
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
