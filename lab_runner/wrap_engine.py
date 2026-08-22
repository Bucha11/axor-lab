"""The single wrapping/trace engine Lab runs on: axor-wrap.

Both the slice runner (`lab_capabilities.governance.runner`) and the general
loop (`lab_runner.loop`) build their `trace/v1` here — by gating real tool calls
through the real axor-core governor (via `axor_wrap.WrappedToolset`) and taking
`WrappedToolset.trace()`. There is no Lab-owned trace builder any more:
`axor_wrap.trace.build_trace` (reached through `.trace()`) is the ONE builder,
and the ONE decision path is the real kernel.

Governance is a capability, not a stage (Suite Platform RFC §10). A run with the
capability ABSENT (`gate=None` / `kernel=None`) still executes through the
governor — it is a required dependency and the only source of content-derivation
provenance, so the value ledger a trace is read from cannot be built without it —
but nothing it decides is enforced and the trace it yields is stripped of its
verdicts and kernel identity: a run nothing governed carries no `gate_decision`
events and names no `kernel_version`, which is exactly the three-way absence
`verify_bundle` checks for.
"""

from __future__ import annotations

from typing import Callable

from axor_wrap import WrappedToolset
from axor_wrap.trace import verdict_events

from lab_contracts.canonical import world_digest

from .simulator import SimulatedToolHost


def tool_callables(
    host: SimulatedToolHost, manifests: dict[str, dict[str, object]]
) -> dict[str, Callable[..., object]]:
    """Callables the toolset gates and executes — each runs the simulated host.

    The host still validates every call against its `args_schema` (a mistyped
    argument is a protocol error, never a silently-accepted call), so that check
    survives the move onto the governor: it runs when the callable fires, after
    the governor has evaluated the intent."""

    def _make(tool_id: str) -> Callable[..., object]:
        def _call(**args: object) -> object:
            return host.execute(tool_id, args)

        return _call

    return {tool_id: _make(tool_id) for tool_id in manifests}


def make_toolset(
    tools: dict[str, Callable[..., object]],
    manifests: dict[str, dict[str, object]],
    condition: dict[str, object],
    inputs: dict[str, object],
    *,
    governed: bool,
) -> WrappedToolset:
    """A WrappedToolset for ONE trial (the governor carries per-session taint).

    `governed` is whether the governance capability is present. Absent →
    enforcement is forced off and the trace is stripped afterwards; present →
    the condition's own enforcement (`on` blocks, `off` observes) is honored.
    The `$inputs.x` allowlist expands against the scenario inputs either way.
    """
    enforcement = str(condition.get("enforcement", "off")) if governed else "off"
    return WrappedToolset(
        tools,
        list(manifests.values()),
        policy=condition.get("policy"),  # type: ignore[arg-type]
        enforcement=enforcement,
        record=True,
        inputs=inputs,
    )


def last_verdict(toolset: WrappedToolset) -> str:
    """The verdict of the call just made — `ALLOW` or `DENY`.

    Read off the governor's own tool-call verdicts (not the raw event list,
    whose tail after a tainting read is a TAINT_PROPAGATED source event, not a
    verdict). Used to fill the agent-visible observation, so an observe-only arm
    can show the agent a recorded DENY it was allowed to run through."""
    events = verdict_events(toolset.trace_events)
    if not events:
        return "ALLOW"
    kind = str(getattr(events[-1].kind, "value", events[-1].kind))
    return "DENY" if kind == "intent_denied" else "ALLOW"


def finalize_trace(
    toolset: WrappedToolset,
    trial: dict[str, object],
    scenario: dict[str, object],
    *,
    governed: bool,
) -> dict[str, object]:
    """Take the session's `trace/v1`; strip governance when the capability is absent.

    The `inputs_digest` that binds the trace to the world it ran in is computed
    with LAB's canonicalizer — the one `verify_bundle` recomputes it with — not
    the kernel's. The two agree byte-for-byte on the float-free subset a trace's
    values occupy, but a scenario's fixtures may carry a float (a transaction
    amount), which Lab's canonicalizer hashes and the kernel's rejects; taking
    the digest here keeps a float-bearing world hashable and keeps the digest the
    one the bundle verifier will accept. When governance is absent the recorded
    verdicts and the kernel identity are removed and the event sequence
    re-densified — a run that observed the agent's actual behavior but claims to
    have governed nothing."""
    inputs_digest = world_digest(
        scenario.get("inputs", {}), scenario.get("fixtures", {}),  # type: ignore[arg-type]
    )
    trace = toolset.trace(trial, scenario=scenario, inputs_digest=inputs_digest)
    if not governed:
        _strip_governance(trace)
    return trace


def _strip_governance(trace: dict[str, object]) -> None:
    events = [
        e for e in trace["events"]  # type: ignore[union-attr]
        if e.get("type") != "gate_decision"
    ]
    for seq, event in enumerate(events):
        event["seq"] = seq
    trace["events"] = events
    trace["producer"].pop("kernel_version", None)  # type: ignore[union-attr]
