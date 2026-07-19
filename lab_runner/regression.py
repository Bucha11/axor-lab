"""RegressionCase: pin (trace, expected verdict); SURFACE any change.

Not "must DENY forever" — policy can intentionally change. Future kernels are
re-run over the frozen trace; a differing verdict is surfaced as
`differs_from_pinned_expected` for the user to label regression (unintended)
or approved baseline update (intended). Never a silent pass, never an
exception.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts.canonical import content_hash

from .kernel import Kernel
from .replay import replay_trace

STATUS_MATCHES = "matches_pinned_expected"
STATUS_DIFFERS = "differs_from_pinned_expected"
STATUS_MISSING = "pinned_trace_missing"
STATUS_TAMPERED = "pinned_trace_tampered"


@dataclass(frozen=True)
class RegressionPin:
    trace_id: str
    trace_ref: str
    expected_verdict: str
    # the ORDERED verdicts expected over the whole trace, not just the last one
    # (review §5.3); optional for back-compat, defaults to [expected_verdict].
    expected_sequence: tuple[str, ...] = ()


def pin(trace: dict[str, object], expected_verdict: str) -> RegressionPin:
    recorded = tuple(
        str(e["decision"]["verdict"]) for e in trace["events"]  # type: ignore[index,union-attr]
        if e.get("type") == "gate_decision"
    )
    return RegressionPin(
        trace_id=str(trace["trace_id"]),
        trace_ref=content_hash(trace),
        expected_verdict=expected_verdict,
        expected_sequence=recorded or (expected_verdict,),
    )


def check_pins(
    pins: tuple[RegressionPin, ...],
    traces: dict[str, dict[str, object]],
    condition: dict[str, object],
    kernel: object,
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
) -> list[dict[str, object]]:
    """Re-run every pinned trace under `kernel`; report, never raise.

    Before replay, the pinned trace is (1) located — a missing trace is a
    reported status, not a KeyError — and (2) verified against its pinned
    content hash — a trace edited under the same id is surfaced as tampered
    rather than silently re-run (review §5.3)."""
    version = getattr(kernel, "version", "unknown")
    results: list[dict[str, object]] = []
    by_id = {str(t["trace_id"]): t for t in traces.values()}
    for pinned in pins:
        trace = by_id.get(pinned.trace_id)
        if trace is None:
            results.append(_result(pinned, "TRACE_MISSING", version, STATUS_MISSING))
            continue
        if content_hash(trace) != pinned.trace_ref:
            results.append(_result(pinned, "TRACE_TAMPERED", version, STATUS_TAMPERED))
            continue
        recomputed, _ = replay_trace(trace, condition, kernel, manifests, inputs)
        actual_sequence = tuple(str(d["verdict"]) for d in recomputed)
        expected = pinned.expected_sequence or (pinned.expected_verdict,)
        matches = actual_sequence == expected
        actual = actual_sequence[-1] if actual_sequence else "NO_DECISION"
        result = _result(
            pinned, actual, version, STATUS_MATCHES if matches else STATUS_DIFFERS
        )
        result["actual_sequence"] = list(actual_sequence)
        result["expected_sequence"] = list(expected)
        results.append(result)
    return results


def _result(pinned: RegressionPin, actual: str, version: str, status: str) -> dict[str, object]:
    return {
        "trace_id": pinned.trace_id,
        "expected": pinned.expected_verdict,
        "actual": actual,
        "kernel": version,
        "status": status,
        "resolution": None if status == STATUS_MATCHES else "user_labels_required",
    }
