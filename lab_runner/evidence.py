"""EvidenceCase: the investigation view over ONE trial's trace.

Three modes, per claims.md — never two, because "governed" is ambiguous:
  1. observed            — the trajectory actually recorded (this trace);
  2. counterfactual_policy_replay — the verdict the gate WOULD return over
     the same frozen events (exact for the verdict; does NOT assert the
     governed agent reached an identical call);
  3. observed_governed_twin — present ONLY if a governed run was actually
     executed; otherwise absent, not faked.
"""

from __future__ import annotations

from .kernel import Kernel
from .replay import replay_trace

FIDELITY_WARNING = (
    "provenance fidelity is heuristic_attribution: lineage is best-effort, "
    "not sound; this chain is not presented as a guarantee"
)
COUNTERFACTUAL_CAVEAT = (
    "exact for the verdict over the recorded events; does not assert the "
    "governed agent would have reached an identical call (behavioral "
    "continuation after a DENY is live/stochastic)"
)
EXPLICIT_FLOW_NOTE = (
    "this verdict is content-independent — it turns on provenance, not "
    "wording; provenance covers explicit flow only"
)


def build_evidence_case(
    trace: dict[str, object],
    scenario: dict[str, object],
    governed_condition: dict[str, object],
    kernel: Kernel,
    manifests: dict[str, dict[str, object]],
    governed_twin: dict[str, object] | None = None,
) -> dict[str, object]:
    """Render the EvidenceCase dict for one trial's trace."""
    inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
    chain = _chain(trace, scenario)
    recomputed, _ = replay_trace(trace, governed_condition, kernel, manifests, inputs)
    modes: dict[str, object] = {
        "observed": {
            "kind": "observed",
            "condition_id": str(trace["trial"]["condition_id"]),  # type: ignore[index]
            "trace_id": str(trace["trace_id"]),
            "verdicts": _recorded_verdicts(trace),
        },
        "counterfactual_policy_replay": {
            "kind": "counterfactual",
            "label": "Counterfactual: policy replay",
            "verdicts": [str(d["verdict"]) for d in recomputed],
            "claim_kind": "exactly_replayable",
            "caveat": COUNTERFACTUAL_CAVEAT,
        },
    }
    if governed_twin is not None:
        modes["observed_governed_twin"] = {
            "kind": "observed",
            "condition_id": str(governed_twin["trial"]["condition_id"]),  # type: ignore[index]
            "trace_id": str(governed_twin["trace_id"]),
            "verdicts": _recorded_verdicts(governed_twin),
        }
    case: dict[str, object] = {
        "trace_id": str(trace["trace_id"]),
        "chain": chain,
        "modes": modes,
        "note": EXPLICIT_FLOW_NOTE,
    }
    producer: dict[str, object] = trace["producer"]  # type: ignore[assignment]
    if producer.get("provenance_fidelity") == "heuristic_attribution":
        case["fidelity_warning"] = FIDELITY_WARNING
    return case


def _chain(trace: dict[str, object], scenario: dict[str, object]) -> dict[str, object]:
    """injection → provenance lineage → gated call → verdict.

    The chain is built for ONE gate decision correlated to ITS intent by call_id
    — the first DENY when there is one (the decision publication claims are built
    from), else the first decision. Picking `first intent` + `first decision`
    independently showed call A's chain for a DENY on call B in a multi-call
    trace (review r12)."""
    values = {str(v["value_id"]): v for v in trace["values"]}  # type: ignore[union-attr]
    events: list[dict[str, object]] = list(trace["events"])  # type: ignore[arg-type]
    decisions = [e for e in events if e.get("type") == "gate_decision"]
    if not decisions:
        raise ValueError("trace has no gate_decision to build an EvidenceCase for")
    decision_event = next(
        (e for e in decisions if e["decision"]["verdict"] == "DENY"),  # type: ignore[index]
        decisions[0],
    )
    target_call_id = decision_event.get("call_id")
    intents = [e for e in events if e.get("type") == "tool_call_intent"]
    call_event = None
    if target_call_id is not None:
        call_event = next((e for e in intents if e.get("call_id") == target_call_id), None)
    if call_event is None:  # legacy traces without call_ids: only safe if there's one
        call_event = intents[0] if len(intents) == 1 else {}
    decision: dict[str, object] = decision_event["decision"]  # type: ignore[assignment]
    driving_id = str(decision["driving_value_id"])
    lineage: list[dict[str, object]] = []
    frontier = [driving_id]
    seen: set[str] = set()
    while frontier:
        vid = frontier.pop(0)
        if vid in seen or vid not in values:
            continue
        seen.add(vid)
        lineage.append(values[vid])
        frontier.extend(str(d) for d in values[vid].get("derived_from", []))  # type: ignore[union-attr]
    return {
        "injection": scenario["injection"],
        "provenance": lineage,
        "gated_call": {"tool": call_event.get("tool"), "arg_bindings": call_event.get("arg_bindings")},
        "verdict": decision,
    }


def _recorded_verdicts(trace: dict[str, object]) -> list[str]:
    return [
        str(e["decision"]["verdict"])  # type: ignore[index]
        for e in trace["events"]  # type: ignore[union-attr]
        if e.get("type") == "gate_decision"
    ]
