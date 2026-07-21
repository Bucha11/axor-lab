"""RegressionCase: pin (trace, expected verdict); SURFACE any change.

Not "must DENY forever" — policy can intentionally change. Future kernels are
re-run over the frozen trace; a differing verdict is surfaced as
`differs_from_pinned_expected` for the user to label regression (unintended)
or approved baseline update (intended). Never a silent pass, never an
exception.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lab_contracts.canonical import content_hash

from .errors import UnknownKernelError
from .replay import (
    REPLAY_MALFORMED_TRACE,
    REPLAY_MATCH,
    REPLAY_UNSUPPORTED_KERNEL,
    replay_trace_status,
)

STATUS_MATCHES = "matches_pinned_expected"
STATUS_DIFFERS = "differs_from_pinned_expected"
STATUS_MISSING = "pinned_trace_missing"
STATUS_TAMPERED = "pinned_trace_tampered"
STATUS_MALFORMED = "pinned_trace_malformed"
STATUS_UNSUPPORTED_KERNEL = "pinned_kernel_unsupported"


@dataclass(frozen=True)
class RegressionPin:
    trace_id: str
    trace_ref: str
    expected_verdict: str
    # the ORDERED verdicts expected over the whole trace, not just the last one
    # (review §5.3); optional for back-compat, defaults to [expected_verdict].
    expected_sequence: tuple[str, ...] = ()


def pin(trace: dict[str, object], expected_verdict: str) -> RegressionPin:
    """Pin a frozen trace to its recorded verdict sequence.

    `expected_verdict` is the headline verdict the user asserts. It MUST equal
    the trace's final recorded verdict — a `pin deny-trace ALLOW` used to produce
    a pin whose `expected_verdict` (ALLOW) contradicted its `expected_sequence`
    ([DENY]); since regression treats the sequence as authoritative, that pin
    reported a MATCH while printing "expected ALLOW", an internally inconsistent
    result (review r13). Reject the contradiction at pin time."""
    recorded = tuple(
        str(e["decision"]["verdict"]) for e in trace["events"]  # type: ignore[index,union-attr]
        if e.get("type") == "gate_decision"
    )
    sequence = recorded or (expected_verdict,)
    if expected_verdict != sequence[-1]:
        raise ValueError(
            f"expected_verdict {expected_verdict!r} does not match the trace's final "
            f"recorded verdict {sequence[-1]!r}; a pin cannot assert a headline verdict "
            "the frozen trace never produced"
        )
    return RegressionPin(
        trace_id=str(trace["trace_id"]),
        trace_ref=content_hash(trace),
        expected_verdict=expected_verdict,
        expected_sequence=sequence,
    )


def check_pins(
    pins: tuple[RegressionPin, ...],
    traces: dict[str, dict[str, object]],
    condition: dict[str, object],
    kernel: object,
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object] | None = None,
    *,
    inputs_for: "Callable[[dict[str, object]], dict[str, object]] | None" = None,
    kernel_for: "Callable[[dict[str, object]], object] | None" = None,
) -> list[dict[str, object]]:
    """Re-run every pinned trace under `kernel`; report, never raise.

    Before replay, the pinned trace is (1) located — a missing trace is a
    reported status, not a KeyError — and (2) verified against its pinned
    content hash — a trace edited under the same id is surfaced as tampered
    rather than silently re-run (review §5.3).

    `inputs_for(trace)` supplies EACH trace's own scenario inputs; a single
    shared `inputs` dict is wrong for a multi-scenario bundle, where pin B would
    replay against scenario A's allowlist / effect-resolution inputs and produce
    a false regression or a false pass (review r12). `inputs` remains as a legacy
    fallback applied to every pin when no resolver is given.

    `kernel_for(trace)` resolves EACH trace's own kernel (review r17): a real
    AxorKernel bakes its `$inputs`-expanded allowlist at resolve time, so a single
    kernel resolved once carries the wrong allowlist for a second scenario. When
    given, it overrides the single `kernel` per pin (which is kept for the version
    fingerprint and as the fallback)."""
    # the fallback fingerprint (used for pins we never actually replay — missing
    # or tampered traces). The REPLAYED fingerprint is taken from the ACTUAL
    # per-trace kernel below, so the report can never claim a kernel that did not
    # decide (review r18).
    def _fingerprint(k: object) -> str:
        return str(getattr(k, "behavior_version", None) or getattr(k, "version", "unknown"))

    fallback_version = _fingerprint(kernel)
    results: list[dict[str, object]] = []
    by_id = {str(t["trace_id"]): t for t in traces.values()}
    for pinned in pins:
        trace = by_id.get(pinned.trace_id)
        if trace is None:
            results.append(_result(pinned, "TRACE_MISSING", fallback_version, STATUS_MISSING))
            continue
        if content_hash(trace) != pinned.trace_ref:
            results.append(_result(pinned, "TRACE_TAMPERED", fallback_version, STATUS_TAMPERED))
            continue
        trace_inputs = inputs_for(trace) if inputs_for is not None else (inputs or {})
        # resolving the per-trace kernel can fail (an unavailable/unknown candidate
        # kernel) — that is a STATUS, not a crash inside the loop (review r18)
        try:
            trace_kernel = kernel_for(trace) if kernel_for is not None else kernel
        except UnknownKernelError:
            results.append(
                _result(pinned, "UNSUPPORTED_KERNEL", fallback_version, STATUS_UNSUPPORTED_KERNEL)
            )
            continue
        # the fingerprint reported for THIS pin is the kernel that actually ran it
        version = _fingerprint(trace_kernel)
        recomputed, replay_status = replay_trace_status(
            trace, condition, trace_kernel, manifests, trace_inputs
        )
        # a MATCH requires the replay to be STRUCTURALLY sound, not just that the
        # recomputed verdict SEQUENCE happens to equal the pin. A malformed trace
        # (e.g. an intent with no decision) still yields a verdict list that can
        # coincide with the pin — reporting that as `matches_pinned_expected`
        # would silently bless a structurally broken trace (review r13). The
        # malformed/unsupported-kernel detection lives in replay; honor it here.
        if replay_status == REPLAY_MALFORMED_TRACE:
            results.append(_result(pinned, "MALFORMED", version, STATUS_MALFORMED))
            continue
        if replay_status == REPLAY_UNSUPPORTED_KERNEL:
            results.append(_result(pinned, "UNSUPPORTED_KERNEL", version, STATUS_UNSUPPORTED_KERNEL))
            continue
        actual_sequence = tuple(str(d["verdict"]) for d in recomputed)
        expected = pinned.expected_sequence or (pinned.expected_verdict,)
        # only a clean REPLAY_MATCH whose sequence equals the pin is a match
        matches = replay_status == REPLAY_MATCH and actual_sequence == expected
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
