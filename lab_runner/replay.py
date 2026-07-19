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


@dataclass(frozen=True)
class ReplayReport:
    """Recorded vs recomputed decisions for a set of traces."""

    decisions: tuple[tuple[str, tuple[dict[str, object], ...]], ...]
    bit_identical: bool

    def verdicts(self) -> dict[str, tuple[str, ...]]:
        return {
            trace_id: tuple(str(d["verdict"]) for d in decisions)
            for trace_id, decisions in self.decisions
        }

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
    """Recompute this trace's decisions; return (recomputed, matches_recorded)."""
    values = {str(v["value_id"]): v for v in trace["values"]}  # type: ignore[union-attr]
    events: list[dict[str, object]] = list(trace["events"])  # type: ignore[arg-type]
    recomputed: list[dict[str, object]] = []
    matches = True
    pending_call: dict[str, object] | None = None
    for event in events:
        if event.get("type") == "tool_call_intent":
            pending_call = event
        elif event.get("type") == "gate_decision":
            if pending_call is None:
                matches = False
                continue
            bindings: dict[str, str] = pending_call.get("arg_bindings", {})  # type: ignore[assignment]
            args = {name: _arg_value(values[vid]) for name, vid in bindings.items()}
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
                matches = False
            pending_call = None
    return tuple(recomputed), matches


def replay_bundle(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    kernels: dict[str, Kernel],
) -> ReplayReport:
    """Replay every trace referenced by the bundle's trials."""
    conditions = {str(c["id"]): c for c in bundle["conditions"]}  # type: ignore[union-attr]
    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    out: list[tuple[str, tuple[dict[str, object], ...]]] = []
    all_match = True
    for trace in sorted(traces.values(), key=lambda t: str(t["trace_id"])):
        trial: dict[str, object] = trace["trial"]  # type: ignore[assignment]
        condition = conditions[str(trial["condition_id"])]
        scenario = scenarios[str(trial["scenario_id"])]
        kernel = kernels[str(condition["kernel"])]
        recomputed, matches = replay_trace(
            trace, condition, kernel, manifests, scenario.get("inputs", {})  # type: ignore[arg-type]
        )
        out.append((str(trace["trace_id"]), recomputed))
        all_match = all_match and matches
    return ReplayReport(decisions=tuple(out), bit_identical=all_match)


def _arg_value(value: dict[str, object]) -> object:
    preview = value.get("preview")
    if isinstance(preview, str):
        try:
            number = float(preview)
            return int(number) if number.is_integer() else number
        except ValueError:
            return preview
    return preview


def _verdict_core(decision: dict[str, object]) -> dict[str, object]:
    """The replay-comparable core: verdict + gate + driving value.

    (`reason`/`projection` prose may evolve without changing the verdict.)"""
    return {
        "verdict": decision["verdict"],
        "gate": decision["gate"],
        "driving_value_id": decision["driving_value_id"],
    }
