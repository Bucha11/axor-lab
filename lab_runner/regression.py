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


@dataclass(frozen=True)
class RegressionPin:
    trace_id: str
    trace_ref: str
    expected_verdict: str


def pin(trace: dict[str, object], expected_verdict: str) -> RegressionPin:
    return RegressionPin(
        trace_id=str(trace["trace_id"]),
        trace_ref=content_hash(trace),
        expected_verdict=expected_verdict,
    )


def check_pins(
    pins: tuple[RegressionPin, ...],
    traces: dict[str, dict[str, object]],
    condition: dict[str, object],
    kernel: Kernel,
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
) -> list[dict[str, object]]:
    """Re-run every pinned trace under `kernel`; report, never raise."""
    results: list[dict[str, object]] = []
    by_id = {str(t["trace_id"]): t for t in traces.values()}
    for pinned in pins:
        trace = by_id[pinned.trace_id]
        recomputed, _ = replay_trace(trace, condition, kernel, manifests, inputs)
        actual = str(recomputed[-1]["verdict"]) if recomputed else "NO_DECISION"
        results.append(
            {
                "trace_id": pinned.trace_id,
                "expected": pinned.expected_verdict,
                "actual": actual,
                "kernel": kernel.version,
                "status": STATUS_MATCHES if actual == pinned.expected_verdict else STATUS_DIFFERS,
                "resolution": None if actual == pinned.expected_verdict else "user_labels_required",
            }
        )
    return results
