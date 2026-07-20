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
EXPLICIT_UNVERIFIED_WARNING = (
    "provenance fidelity 'explicit_flow_tracked' is SELF-REPORTED by the trace "
    "producer and is NOT cryptographically verified — Lab has no attestation "
    "binding this lineage to a trusted runtime, so it is treated as heuristic "
    "for this evidence view (a forged claim would otherwise render as sound)"
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
    # CLAIMED vs VERIFIED fidelity (review r13). The producer self-declares
    # provenance_fidelity, and the schema only checks the enum — nothing binds an
    # `explicit_flow_tracked` claim to a trusted runtime that actually tracked the
    # flow, and the server's hash/replay checks prove only that the bytes are
    # self-consistent, not that the lineage was soundly assembled. Lab has no
    # attestation mechanism for that yet, so an explicit claim is reported as
    # self_reported (unverified) and STILL carries a warning — otherwise a forged
    # explicit_flow_tracked renders as a sound provenance chain with no caveat.
    claimed = str(producer.get("provenance_fidelity", "heuristic_attribution"))
    # no attestation path exists yet, so an explicit claim is never VERIFIED from
    # stored bytes — it is downgraded to self_reported; a heuristic claim is
    # already honest and stays as-is
    if claimed == "heuristic_attribution":
        verified = "heuristic_attribution"
        case["fidelity_warning"] = FIDELITY_WARNING
    else:
        verified = "self_reported"
        case["fidelity_warning"] = EXPLICIT_UNVERIFIED_WARNING
    case["fidelity"] = {"claimed": claimed, "verified": verified}
    return case


def evidence_condition(
    bundle: dict[str, object], trace: dict[str, object], policy_id: str | None = None
) -> dict[str, object]:
    """The condition to replay this trace's counterfactual under — the ONE
    resolver shared by the CLI and the HTML EvidenceCase (review r13).

    A `policy_id` selection wins when it names an enforcing condition; otherwise
    the trace's OWN condition when it enforces (never silently swap a governed
    trace's policy for another — e.g. show a `strict` counterfactual for an
    `governed_allowlist` trace); otherwise the first enforcing candidate; else
    the trace's own condition. A named policy_id that isn't an enforcing
    condition is an error, not a silent fallthrough."""
    conditions: list[dict[str, object]] = list(bundle["conditions"])  # type: ignore[arg-type]
    by_id = {str(c["id"]): c for c in conditions}
    if policy_id is not None:
        chosen = by_id.get(policy_id)
        if chosen is None:
            raise ValueError(f"policy {policy_id!r} is not a condition in the bundle")
        if chosen["enforcement"] != "on":
            raise ValueError(f"policy {policy_id!r} is not enforcement-on")
        return chosen
    own = by_id.get(str(trace["trial"]["condition_id"]))  # type: ignore[index]
    if own is not None and own["enforcement"] == "on":
        return own
    for condition in conditions:
        if condition["enforcement"] == "on":
            return condition
    if own is not None:
        return own
    raise ValueError("no condition to replay the counterfactual under")


def validate_twin(
    trace: dict[str, object], twin: dict[str, object], bundle: dict[str, object]
) -> None:
    """A governed twin must be the SAME experimental unit under an enforcing
    policy — not any unrelated trace (review r13).

    The observed_governed_twin mode claims "here is what a governed run of THIS
    case actually did". The CLI only checked the twin id existed, so a trace from
    a different scenario / seed / repeat — or an ungoverned trace — could be
    passed off as the governed twin. Require the twin to share the trial
    coordinate (scenario_id, seed, repeat_index) and to have run under an
    enforcement-on condition."""
    coords = ("scenario_id", "seed", "repeat_index")
    tt: dict[str, object] = trace["trial"]  # type: ignore[assignment]
    wt: dict[str, object] = twin["trial"]  # type: ignore[assignment]
    for coord in coords:
        if str(tt.get(coord)) != str(wt.get(coord)):
            raise ValueError(
                f"twin {coord} {wt.get(coord)!r} != trace {tt.get(coord)!r} — a governed "
                "twin must be the SAME case (scenario/seed/repeat), not an unrelated trace"
            )
    by_id = {str(c["id"]): c for c in bundle["conditions"]}  # type: ignore[union-attr]
    twin_cond = by_id.get(str(wt.get("condition_id")))
    if twin_cond is None or twin_cond["enforcement"] != "on":
        raise ValueError(
            f"twin condition {wt.get('condition_id')!r} is not enforcement-on — an "
            "observed_governed_twin must be a GOVERNED run"
        )


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
    # a fail-closed DENY has driving_value_id=null (no provenance value exists),
    # so its lineage is empty — start from nothing rather than a fake id (r14)
    driving_id = decision.get("driving_value_id")
    lineage: list[dict[str, object]] = []
    frontier = [str(driving_id)] if driving_id is not None else []
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
