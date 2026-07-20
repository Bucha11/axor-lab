"""Shared claim-text rendering — ONE function, used by both the CLI local
publish and the server publish handshake (review r6: local and hosted must
generate identical claim assertions, not two independent code paths)."""

from __future__ import annotations


def deny_claim_text(trace: dict[str, object]) -> str:
    """Build the exact DENY claim from the RECORDED decision, never a template.

    A kernel can DENY for taint, criticality, a rate limit, budget, missing
    approval, etc. This reports the decision's own gate + driving value + labels
    + recorded reason, and if none is recorded it says so rather than inventing
    causality (the old text always claimed 'the driving argument is
    untrusted_derived')."""
    kernel_version = str(trace["producer"]["kernel_version"])  # type: ignore[index]
    trace_id = str(trace["trace_id"])
    values = {str(v["value_id"]): v for v in trace["values"]}  # type: ignore[union-attr]
    events: list[dict[str, object]] = list(trace["events"])  # type: ignore[arg-type]
    decision: dict[str, object] | None = None
    for event in events:
        if event.get("type") == "gate_decision" and event["decision"]["verdict"] == "DENY":  # type: ignore[index]
            decision = event["decision"]  # type: ignore[assignment]
            break
    if decision is None:
        return f"On trace {trace_id}, {kernel_version} returns DENY."
    gate = str(decision.get("gate", "unknown"))
    tool = next((e.get("tool") for e in events if e.get("type") == "tool_call_intent"), None)
    driving_id = str(decision.get("driving_value_id", ""))
    labels = tuple(values.get(driving_id, {}).get("labels", []))  # type: ignore[union-attr]
    parts = [f"On trace {trace_id}, {kernel_version} returns DENY by gate '{gate}'"]
    if tool:
        parts.append(f"on {tool}")
    if driving_id and driving_id != "v_none":
        parts.append(f"; driving value {driving_id}")
        parts.append(f"labelled [{', '.join(labels)}]" if labels else "has no recorded labels")
    reason = decision.get("reason") or decision.get("projection")
    tail = f" ({reason})" if reason else ". No further causal explanation was recorded."
    return " ".join(parts) + tail
