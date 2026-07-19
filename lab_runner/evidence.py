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
    """injection → provenance lineage → gated call → verdict."""
    values = {str(v["value_id"]): v for v in trace["values"]}  # type: ignore[union-attr]
    events: list[dict[str, object]] = list(trace["events"])  # type: ignore[arg-type]
    decision_event = next(e for e in events if e.get("type") == "gate_decision")
    call_event = next(e for e in events if e.get("type") == "tool_call_intent")
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
