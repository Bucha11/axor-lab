"""Instrumented endpoint → conformant trace/v1 + gate decision.

The agent emits value-carrying events (tool results mint values with labels;
tool-call intents bind args to value ids). Lab assembles these into a
`trace/v1` with `producer.mode = instrumented_endpoint` and gates it with the
SAME pure kernel the local runner and replay use — so an instrumented endpoint
gets real governance, EvidenceCase, and replay, exactly like wrapped code.

Fidelity honesty: `explicit_flow_tracked` only when the SDK carries labels;
if only events arrive without labels, the trace is `heuristic_attribution`
and flagged (never presented as sound).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_contracts import world_digest
from lab_runner.kernel import Kernel

from .gating import gated_args, normalize_value_hash, provenance_fidelity

PRODUCER_MODE = "instrumented_endpoint"


@dataclass
class EmittedEvent:
    """One event the instrumented agent emits over the gateway."""

    type: str  # "tool_result" | "tool_call_intent"
    tool: str
    # tool_result: values minted from the result, each {value_id, labels, sources, ...}
    values: list[dict[str, object]] = field(default_factory=list)
    # tool_call_intent: arg name -> value_id
    arg_bindings: dict[str, str] = field(default_factory=dict)
    # tool_call_intent: arg name -> concrete value (for effect resolution)
    args: dict[str, object] = field(default_factory=dict)


def assemble_and_gate(
    emitted: list[EmittedEvent],
    condition: dict[str, object],
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
    kernel: Kernel,
    run_id: str,
    scenario_id: str,
    seed: str = "s000",
    labels_carried: bool = True,
    fixtures: dict[str, object] | None = None,
    trusted_runtime: bool = False,
) -> dict[str, object]:
    """Build a trace/v1 from emitted events and gate each sink call intent.

    Like the HTTP gateway, the gate decides on the args assembled from the
    bindings (`gated_args`), NOT the caller's concrete `item.args`; a mismatched
    concrete arg raises GatingError (fail closed), so a clean binding paired with
    a malicious value cannot launder an ALLOW between live decision and replay
    (review r9). Provenance fidelity comes from the trusted-runtime context, not
    a bare boolean."""
    values: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    seq = 0
    seen: set[str] = set()
    for item in emitted:
        if item.type == "tool_result":
            for value in item.values:
                if value["value_id"] not in seen:
                    seen.add(str(value["value_id"]))
                    # every trace value must carry an authoritative
                    # canonical_value_hash (contracts trace_semantics, r13)
                    values.append(normalize_value_hash(value))
            events.append({
                "seq": seq, "node": "root", "type": "tool_result", "tool": item.tool,
                "produces_value_ids": [str(v["value_id"]) for v in item.values],
            })
            seq += 1
        elif item.type == "tool_call_intent":
            events.append({
                "seq": seq, "node": "root", "type": "tool_call_intent", "tool": item.tool,
                "arg_bindings": dict(item.arg_bindings),
            })
            seq += 1
            values_by_id = {str(v["value_id"]): v for v in values}
            # authoritative args from the bindings; a conflicting concrete
            # assertion fails closed (shared with the HTTP gateway)
            authoritative = gated_args(
                manifests[item.tool], dict(item.arg_bindings), values_by_id,
                asserted=item.args or None,
            )
            arg_labels = {
                name: tuple(_labels_of(values, vid)) for name, vid in item.arg_bindings.items()
            }
            decision = kernel.decide(
                enforcement=str(condition["enforcement"]),
                manifest=manifests[item.tool],
                args=authoritative,
                arg_labels=arg_labels,
                arg_bindings=item.arg_bindings,
                inputs=inputs,
                policy=condition.get("policy"),  # type: ignore[arg-type]
            )
            events.append({"seq": seq, "node": "root", "type": "gate_decision", "decision": decision})
            seq += 1

    return {
        "schema_version": "trace/v1",
        "trace_id": f"t_{run_id}_ep_{seed}",
        "trial": {
            "run_id": run_id, "scenario_id": scenario_id,
            "condition_id": str(condition["id"]), "seed": seed, "repeat_index": 0,
        },
        "producer": {
            "mode": PRODUCER_MODE,
            "provenance_fidelity": provenance_fidelity(trusted_runtime, labels_carried),
            "kernel_version": str(condition["kernel"]),
            "runtime": "lab-gateway@0.1",
        },
        "inputs_digest": world_digest(inputs, fixtures),
        "events": events,
        "values": values,
    }


def _labels_of(values: list[dict[str, object]], value_id: str) -> tuple[str, ...]:
    for value in values:
        if value["value_id"] == value_id:
            return tuple(value["labels"])  # type: ignore[arg-type]
    return ()
