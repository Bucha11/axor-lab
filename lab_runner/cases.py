"""EvidenceCase extraction — the generic one.

An EvidenceCase is a curated investigation of ONE trial (Suite Platform RFC §9):
a latency spike, a budget overflow, a planner failure, a hallucination, a
secret leakage. `kind` is an open string so a suite can register its own.

The repo had exactly one builder and it was the governance chain — injection →
provenance → gated call → verdict, requiring a governed condition and a kernel.
Every other kind of investigation was unrepresentable in code while being fully
representable in the schema, which is the same inversion the whole Suite
Platform migration is about: the general case was the special case.

Two rules the schema states and this enforces:

  - A case is a VIEW over a recorded trace. Timeline entries POINT AT an event
    by `seq`; they never restate what the event said. A case that carries its
    own copy of the narrative can drift from the trace it claims to describe,
    and then the artifact contains two accounts of one run.
  - Metrics are COPIED for display; `trial.metrics` in the artifact stays
    authoritative. An absent metric stays absent — a case about a budget
    overflow on a run that never priced anything has no `cost_usd` to show.

The governance chain is not replaced by any of this. It becomes the optional
`governance` block on a case, built by the capability
(`lab_capabilities.governance.evidence`), for the runs that have one.
"""

from __future__ import annotations

from typing import Any

SCHEMA = "evidence-case/v1"

# trace/v1 event type -> who acted. `gate_decision` is the only one attributable
# to the gate, and it exists only on a governed run.
_ACTOR_OF: dict[str, str] = {
    "tool_call_intent": "agent",
    "tool_result": "tool",
    "gate_decision": "gate",
    "message_send": "agent",
    "message_recv": "agent",
    "final_output": "agent",
    "spawn": "system",
    "death": "system",
}

_INTERESTING = ("tool_call_intent", "gate_decision", "final_output")


def _label(event: dict[str, Any]) -> str:
    """A short display label DERIVED from the event's own structural fields.

    Never from a payload body: a trace records observations, and a case that
    quoted content would leak whatever the trace deliberately redacted.
    """
    kind = str(event.get("type", ""))
    tool = event.get("tool")
    if kind == "gate_decision":
        decision = event.get("decision") or {}
        verdict = str(decision.get("verdict", "?"))
        gate = str(decision.get("gate", "?"))
        label = f"{verdict} at the {gate} gate" + (f" ({tool})" if tool else "")
        # A DENY the run did not OBEY is the most misreadable line a narrative
        # can carry: the verdict says blocked and the next entry is the tool
        # result. `enforcement: off` means the kernel decided and the caller
        # executed anyway — which is exactly what an ungoverned arm measures —
        # so the label says so rather than letting a reader conclude the call
        # was stopped.
        if verdict == "DENY" and decision.get("enforced") is False:
            label += " — recorded, not enforced"
        return label
    if tool:
        return f"{kind.replace('_', ' ')}: {tool}"
    return kind.replace("_", " ")


def timeline_of(
    trace: dict[str, Any], highlight_seq: int | None = None,
) -> list[dict[str, Any]]:
    """The ordered narrative, one entry per event, pointing at it by `seq`."""
    entries: list[dict[str, Any]] = []
    for event in trace.get("events", ()):
        seq = int(event.get("seq", 0))
        entry: dict[str, Any] = {
            "seq": seq,
            "actor": _ACTOR_OF.get(str(event.get("type", "")), "system"),
            "label": _label(event),
        }
        if highlight_seq is not None and seq == highlight_seq:
            entry["highlight"] = True
        entries.append(entry)
    return entries


def turning_point(trace: dict[str, Any]) -> int | None:
    """The event a case most likely turns on, or None.

    A recorded DENY first — on a governed run that is the whole story. Otherwise
    the last tool call, which is where a latency or budget case ends up. This is
    a DEFAULT for an automatic extraction; a curator overrides it, and a suite
    that knows better passes its own.
    """
    denial = next(
        (
            int(e["seq"]) for e in trace.get("events", ())
            if str(e.get("type")) == "gate_decision"
            and str((e.get("decision") or {}).get("verdict", "")) == "DENY"
        ),
        None,
    )
    if denial is not None:
        return denial
    calls = [
        int(e["seq"]) for e in trace.get("events", ())
        if str(e.get("type")) in _INTERESTING
    ]
    return calls[-1] if calls else None


def case_id_for(trial: dict[str, Any], kind: str) -> str:
    """A deterministic, per-trial case id.

    Derived from the trial id, with the `sha256:` prefix stripped first — every
    trial id carries it, so slicing the prefixed string spends most of the
    budget on nine identical characters and leaves neighbouring trials sharing
    a case id. The kind is folded in so two extractors on one trial do not
    collide.
    """
    raw = str(trial.get("trial_id", ""))
    body = raw.split(":", 1)[1] if ":" in raw else raw
    return f"EC-{kind}-{body[:12]}" if body else f"EC-{kind}"


def build_case(
    *,
    case_id: str,
    kind: str,
    title: str,
    trial: dict[str, Any],
    trace: dict[str, Any],
    run_id: str = "",
    summary: str = "",
    severity: str | None = None,
    metrics: "list[str] | None" = None,
    highlight_seq: int | None = None,
    tags: "list[str] | None" = None,
    created: str | None = None,
) -> dict[str, Any]:
    """An `evidence-case/v1` over one trial. No governance required.

    ``metrics`` names which of the trial's metrics to surface; a name the trial
    did not measure is OMITTED rather than rendered as zero, so a case cannot
    imply a measurement that never happened. Omit the argument to surface every
    metric the trial recorded.
    """
    recorded: dict[str, Any] = dict(trial.get("metrics") or {})
    if metrics is not None:
        recorded = {k: v for k, v in recorded.items() if k in set(metrics)}
    trial_ref: dict[str, Any] = {"trial_id": str(trial.get("trial_id", ""))}
    for source, target in (
        ("scenario_id", "scenario_id"), ("condition_id", "condition_id"),
        ("trace_ref", "trace_ref"),
    ):
        if trial.get(source):
            trial_ref[target] = str(trial[source])
    if run_id:
        trial_ref["run_id"] = run_id

    case: dict[str, Any] = {
        "schema_version": SCHEMA,
        "id": case_id,
        "kind": kind,
        "title": title,
        "trial_ref": trial_ref,
        "timeline": timeline_of(
            trace, highlight_seq if highlight_seq is not None else turning_point(trace),
        ),
    }
    if created:
        case["created"] = created
    if summary:
        case["summary"] = summary
    if severity:
        case["severity"] = severity
        case["impact"] = {"level": severity, "affected_trials": 1}
    if recorded:
        case["metrics"] = recorded
    if tags:
        case["tags"] = list(tags)
    return case


def threshold_case(
    *,
    case_id: str,
    kind: str,
    metric: str,
    limit: float,
    trial: dict[str, Any],
    trace: dict[str, Any],
    run_id: str = "",
    severity: str = "medium",
    created: str | None = None,
) -> "dict[str, Any] | None":
    """A case for a trial whose ``metric`` exceeded ``limit`` — or None.

    Returns None when the metric was not MEASURED, not just when it was within
    bounds. The two are different answers and the distinction is the same one
    `metric_threshold` invariants make: an unmeasured latency is not a fast one,
    and a case asserting an overrun that nobody measured is a fabricated
    finding.
    """
    value = (trial.get("metrics") or {}).get(metric)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if value <= limit:
        return None
    return build_case(
        case_id=case_id, kind=kind,
        title=f"{metric} exceeded {limit} on {trial.get('scenario_id', 'a trial')}",
        trial=trial, trace=trace, run_id=run_id, severity=severity,
        summary=(
            f"The trial recorded {metric} = {value}, over the {limit} the suite "
            f"declared. The timeline points at the recorded events; the trace is "
            f"authoritative for what happened."
        ),
        created=created,
        tags=[kind],
    )
