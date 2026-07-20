"""Exact decision replay over frozen traces (`axor lab replay`).

Recomputes every recorded gate_decision through the SAME `Kernel.decide`
the live runner used — no model calls, no tools, offline, bit-identical.
Replay reproduces the VERDICTS; it never claims the counterfactual
continuation (claims.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts.canonical import canonical_json

from .kernel import Kernel

# Replay outcome per trace — a single bool conflated "the recomputed verdict
# differs" with "the trace is structurally broken" (an intent with no decision,
# a decision with no intent, a duplicate decision). Those are different facts:
# MISMATCH is a real governance divergence; MALFORMED_TRACE means the trace
# cannot be replayed at all and must never be reported as reproduced.
REPLAY_MATCH = "match"
REPLAY_MISMATCH = "mismatch"
REPLAY_MALFORMED_TRACE = "malformed_trace"
REPLAY_UNSUPPORTED_KERNEL = "unsupported_kernel"


@dataclass(frozen=True)
class ReplayReport:
    """Recorded vs recomputed decisions for a set of traces.

    `bit_identical` is precise about WHAT is identical: (a) the recomputed
    verdict-core (verdict + gate + driving value id) matches the recorded one —
    `reason`/`projection` prose may evolve without changing the verdict — and
    (b) the recomputed report is byte-identical across machines/processes given
    the same pinned kernel (the `canonical()` witness). It is NOT a claim that
    the full free-text decision object is byte-equal (review §5.2).

    `bit_identical` is True only when EVERY trace replays with status MATCH — a
    malformed trace (missing/duplicate decision) makes it False, and its status
    is recorded in `statuses` so a caller can tell corruption from divergence."""

    decisions: tuple[tuple[str, tuple[dict[str, object], ...]], ...]
    bit_identical: bool
    statuses: tuple[tuple[str, str], ...] = ()

    def verdicts(self) -> dict[str, tuple[str, ...]]:
        return {
            trace_id: tuple(str(d["verdict"]) for d in decisions)
            for trace_id, decisions in self.decisions
        }

    def status_of(self) -> dict[str, str]:
        return dict(self.statuses)

    def malformed(self) -> tuple[str, ...]:
        return tuple(tid for tid, s in self.statuses if s == REPLAY_MALFORMED_TRACE)

    def canonical(self) -> str:
        """Canonical bytes of the recomputed decisions — the cross-machine
        bit-identity witness."""
        return canonical_json([[tid, list(ds)] for tid, ds in self.decisions])


def replay_trace(
    trace: dict[str, object],
    condition: dict[str, object],
    kernel: Kernel,
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
) -> tuple[tuple[dict[str, object], ...], bool]:
    """Recompute this trace's decisions; return (recomputed, matches_recorded).

    `matches_recorded` is True only for a well-formed trace whose recomputed
    verdict-core equals the recorded one — a MISMATCH or a MALFORMED trace both
    return False. Use `replay_trace_status` when you need to tell them apart."""
    recomputed, status = replay_trace_status(trace, condition, kernel, manifests, inputs)
    return recomputed, status == REPLAY_MATCH


def replay_trace_status(
    trace: dict[str, object],
    condition: dict[str, object],
    kernel: Kernel,
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
) -> tuple[tuple[dict[str, object], ...], str]:
    """Recompute this trace's decisions; return (recomputed, REPLAY_* status).

    Drives the SAME decision path the live run used: the reference kernel's pure
    `decide`, or the real axor-core governor (reconstructing its taint
    registrations from the frozen tool_result values). Deterministic either way,
    so replay cannot diverge from the recorded run (architecture rule 0).

    Structural validation (review r2 §replay): every intent must have exactly
    one decision and every decision exactly one intent. Intents/decisions pair
    by `call_id` when present, else FIFO within a node. A decision with no
    matching intent, a duplicate decision for one intent, or an intent left with
    no decision at end of trace ⇒ MALFORMED_TRACE. An incomplete trace is NEVER
    reported as reproduced."""
    from .axor_backend import AxorKernel, gate_with_governor

    values = {str(v["value_id"]): v for v in trace["values"]}  # type: ignore[union-attr]
    events: list[dict[str, object]] = list(trace["events"])  # type: ignore[arg-type]
    recomputed: list[dict[str, object]] = []
    matched = True
    malformed = False
    # per-node FIFO queues of unmatched intents — a single `pending_call` breaks
    # on interleaved nodes / parallel calls / several intents before decisions
    # (review §5.1). With call_ids we pair by id; without, by node FIFO.
    pending: dict[str, list[dict[str, object]]] = {}
    by_call_id: dict[str, dict[str, object]] = {}
    decided_call_ids: set[str] = set()
    # accumulate the governor's taint registrations from tool_result events
    registrations: list[tuple[str, object]] = []
    for event in events:
        node = str(event.get("node", "root"))
        etype = event.get("type")
        if etype == "tool_result":
            for vid in event.get("produces_value_ids", []) or []:
                value = values.get(str(vid), {})
                if "untrusted_derived" in value.get("labels", []) and "decision_value" in value:
                    registrations.append((str(event.get("tool")), value["decision_value"]))
        elif etype == "tool_call_intent":
            pending.setdefault(node, []).append(event)
            cid = event.get("call_id")
            if cid is not None:
                by_call_id[str(cid)] = event
        elif etype == "gate_decision":
            cid = event.get("call_id")
            pending_call = _match_intent(event, node, pending, by_call_id, decided_call_ids)
            if pending_call is None:
                # decision with no matching intent, or a duplicate decision
                malformed = True
                continue
            bindings: dict[str, str] = pending_call.get("arg_bindings", {})  # type: ignore[assignment]
            args = {name: _arg_value(values[vid]) for name, vid in bindings.items()}
            if isinstance(kernel, AxorKernel):
                driving = pending_call.get("arg_bindings", {}).get("recipient", "v_none")  # type: ignore[union-attr]
                decision = gate_with_governor(
                    kernel.config, str(condition["enforcement"]), registrations,
                    str(pending_call["tool"]), args, str(driving),
                )
            else:
                labels = {
                    name: tuple(values[vid]["labels"])  # type: ignore[arg-type]
                    for name, vid in bindings.items()
                }
                decision = kernel.decide(
                    enforcement=str(condition["enforcement"]),
                    manifest=manifests[str(pending_call["tool"])],
                    args=args,
                    arg_labels=labels,
                    arg_bindings=bindings,
                    inputs=inputs,
                    policy=condition.get("policy"),  # type: ignore[arg-type]
                )
            recomputed.append(decision)
            recorded = event["decision"]
            if canonical_json(_verdict_core(decision)) != canonical_json(_verdict_core(recorded)):  # type: ignore[arg-type]
                matched = False
    # any intent left unpaired at end of trace ⇒ incomplete/malformed
    if any(queue for queue in pending.values()):
        malformed = True
    if malformed:
        return tuple(recomputed), REPLAY_MALFORMED_TRACE
    return tuple(recomputed), REPLAY_MATCH if matched else REPLAY_MISMATCH


def _match_intent(
    decision_event: dict[str, object],
    node: str,
    pending: dict[str, list[dict[str, object]]],
    by_call_id: dict[str, dict[str, object]],
    decided_call_ids: set[str],
) -> dict[str, object] | None:
    """Pair a gate_decision with its intent; return None if malformed."""
    cid = decision_event.get("call_id")
    if cid is not None:
        cid = str(cid)
        intent = by_call_id.get(cid)
        if intent is None or cid in decided_call_ids:
            return None  # unknown call_id, or a second decision for one call
        decided_call_ids.add(cid)
        queue = pending.get(node, [])
        if intent in queue:
            queue.remove(intent)
        return intent
    # legacy trace without call_ids: earliest unmatched intent on this node
    queue = pending.get(node, [])
    if not queue:
        return None
    return queue.pop(0)


def replay_bundle(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    kernels: dict[str, Kernel],
) -> ReplayReport:
    """Replay every trace referenced by the bundle's trials."""
    from .axor_backend import resolve_kernel

    class _Shim:
        def get(self, version: str) -> object:
            return kernels[version]

    conditions = {str(c["id"]): c for c in bundle["conditions"]}  # type: ignore[union-attr]
    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    out: list[tuple[str, tuple[dict[str, object], ...]]] = []
    statuses: list[tuple[str, str]] = []
    all_match = True
    for trace in sorted(traces.values(), key=lambda t: str(t["trace_id"])):
        trial: dict[str, object] = trace["trial"]  # type: ignore[assignment]
        condition = conditions[str(trial["condition_id"])]
        scenario = scenarios[str(trial["scenario_id"])]
        trace_id = str(trace["trace_id"])
        # the same kernel selection as the live run: real axor-core when pinned.
        # If the pinned kernel is unavailable, that trace is UNSUPPORTED_KERNEL —
        # distinct from a divergence, and never counted as reproduced.
        try:
            kernel = resolve_kernel(
                str(condition["kernel"]), manifests, condition.get("policy"), _Shim()  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001 — unavailable/unknown kernel is a status, not a crash
            out.append((trace_id, ()))
            statuses.append((trace_id, REPLAY_UNSUPPORTED_KERNEL))
            all_match = False
            continue
        recomputed, status = replay_trace_status(
            trace, condition, kernel, manifests, scenario.get("inputs", {})  # type: ignore[arg-type]
        )
        out.append((trace_id, recomputed))
        statuses.append((trace_id, status))
        all_match = all_match and status == REPLAY_MATCH
    return ReplayReport(decisions=tuple(out), bit_identical=all_match, statuses=tuple(statuses))


def _arg_value(value: dict[str, object]) -> object:
    """The exact typed value the gate must see on replay.

    Reads the replay-authoritative `decision_value` (any JSON type), NEVER the
    truncated `preview`. A value with no `decision_value` (redacted/sensitive)
    yields a sentinel so a policy that turns on the value fails closed rather
    than silently replaying against a wrong reconstruction."""
    if "decision_value" in value:
        return value["decision_value"]
    return {"__redacted__": value.get("canonical_value_hash")}


def _verdict_core(decision: dict[str, object]) -> dict[str, object]:
    """The replay-comparable core: verdict + gate + driving value.

    (`reason`/`projection` prose may evolve without changing the verdict.)"""
    return {
        "verdict": decision["verdict"],
        "gate": decision["gate"],
        "driving_value_id": decision["driving_value_id"],
    }
