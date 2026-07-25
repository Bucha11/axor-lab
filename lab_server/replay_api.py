"""Replay an uploaded bundle — the "reproduce a run, no agent needed" promise.

The landing page has always offered this as the lowest-barrier path, and the
agent-ingest screen offered "upload traces → reproduce governance verdicts
bit-identical". Neither was reachable: replay existed only in the CLI, so a
browser could parse a trace file and count its events, and that was all.

What replay actually proves is narrow and worth stating exactly. Re-running the
recorded traces through the pinned kernel recomputes each decision's
verdict-core — verdict, gate, driving value id — and compares it to what was
recorded. Prose (`reason`, `projection`) may evolve without changing a verdict,
so it is deliberately outside the comparison. The behavioural layer is NOT
reproduced: the model's choices were sampled once and frozen into the traces.
Replay reproduces governance, not behaviour.

A malformed trace is kept distinct from a diverging one. `bit_identical` is true
only when every trace replays with status `match`, and each trace's own status
travels back so a caller can tell corruption from disagreement — collapsing the
two would let a broken upload read as a governance regression.
"""
from __future__ import annotations

from typing import Any

# Replaying is CPU work over untrusted input, so the surface is bounded by trace
# count rather than left open to whatever a request carries.
MAX_REPLAY_TRACES = 2000


class ReplayRefused(ValueError):
    """The upload cannot be replayed. Carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _traces_by_id(raw: Any) -> dict[str, dict[str, Any]]:
    """Accept both shapes the ecosystem produces: the `{trace_id: trace}` map a
    bundle route returns, and the `[trace, …]` list a bundle directory reads as."""
    if isinstance(raw, dict):
        items = list(raw.values())
    elif isinstance(raw, list):
        items = list(raw)
    else:
        raise ReplayRefused(400, "`traces` must be a list of traces or a {id: trace} map")

    traces: dict[str, dict[str, Any]] = {}
    for trace in items:
        if not isinstance(trace, dict) or "trace_id" not in trace:
            raise ReplayRefused(400, "every trace must be an object carrying a trace_id")
        trace_id = str(trace["trace_id"])
        if trace_id in traces:
            raise ReplayRefused(400, f"duplicate trace_id {trace_id!r} — the upload is corrupt")
        traces[trace_id] = trace
    if not traces:
        raise ReplayRefused(400, "no traces to replay")
    if len(traces) > MAX_REPLAY_TRACES:
        raise ReplayRefused(
            413, f"{len(traces)} traces exceeds the replay ceiling of {MAX_REPLAY_TRACES}"
        )
    return traces


def replay_upload(body: dict[str, Any]) -> dict[str, Any]:
    """Replay `{bundle, traces}` and report what matched.

    The bundle and every trace are validated against the real schemas first: a
    replay verdict over an unvalidated artifact would be answering a question
    about a shape nobody checked.
    """
    from lab_contracts import validate_artifact
    from lab_runner import default_registry, replay_bundle
    from lab_runner.errors import RunnerError
    from lab_runner.replay import (
        REPLAY_MALFORMED_TRACE,
        REPLAY_MATCH,
        REPLAY_MISMATCH,
        REPLAY_UNSUPPORTED_KERNEL,
    )

    bundle = body.get("bundle")
    if not isinstance(bundle, dict):
        raise ReplayRefused(400, "`bundle` must be a bundle object")
    traces = _traces_by_id(body.get("traces"))

    errors = validate_artifact(bundle, "bundle")
    for trace in traces.values():
        errors += validate_artifact(trace, "trace")
    if errors:
        raise ReplayRefused(422, "the upload is not a valid bundle: " + "; ".join(errors[:5]))

    conditions = bundle.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ReplayRefused(422, "the bundle declares no conditions to replay under")
    versions = tuple(str(c["kernel"]) for c in conditions)
    try:
        kernels = {k.version: k for k in default_registry(versions).kernels}
        report = replay_bundle(bundle, traces, kernels)
    except RunnerError as exc:
        raise ReplayRefused(422, f"cannot replay: {exc}") from exc

    verdicts = report.verdicts()
    statuses = report.status_of()
    denies = sum(1 for vs in verdicts.values() for v in vs if v == "DENY")
    allows = sum(1 for vs in verdicts.values() for v in vs if v == "ALLOW")

    # A bare `bit_identical: False` conflates two different answers. A bundle
    # pinned to a kernel this server does not have was never replayed at all, and
    # reporting that as "the verdicts differ" would be a false claim about
    # someone's evidence. So the outcome is named, and the boolean is kept
    # alongside it for callers that already read it.
    kinds = set(statuses.values())
    if kinds <= {REPLAY_MATCH}:
        outcome = "reproduced"
    elif kinds & {REPLAY_MISMATCH, REPLAY_MALFORMED_TRACE}:
        outcome = "diverged"
    elif REPLAY_UNSUPPORTED_KERNEL in kinds:
        outcome = "not_attempted"
    else:
        outcome = "diverged"

    return {
        "outcome": outcome,
        "bit_identical": report.bit_identical,
        "traces": len(traces),
        "decisions": sum(len(v) for v in verdicts.values()),
        "deny": denies,
        "allow": allows,
        # per-trace, so a single malformed trace is visible as itself rather than
        # dragging the whole upload into an unexplained "mismatch"
        "statuses": [
            {"trace_id": trace_id, "status": status, "verdicts": list(verdicts.get(trace_id, ()))}
            for trace_id, status in sorted(statuses.items())
        ],
        "claim": (
            "governance verdicts recomputed from the recorded traces under the "
            "pinned kernel — the verdict core (verdict, gate, driving value) is "
            "compared, decision prose is not. Behaviour is not reproduced: the "
            "model's choices are frozen in the traces."
        ),
    }
