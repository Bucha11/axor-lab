"""Instrumented endpoint → conformant trace/v1 + gate decision.

The agent emits value-carrying events (tool results mint values with labels;
tool-call intents bind args to value ids). Lab assembles these into a
`trace/v1` with `producer.mode = instrumented_endpoint` and gates it with the
SAME pure kernel the local runner and replay use — so an instrumented endpoint
gets real governance, EvidenceCase, and replay, exactly like wrapped code.

Fidelity honesty: `explicit_flow_tracked` only when the SDK carries labels;
if only events arrive without labels, the trace is `heuristic_attribution`
and flagged (never presented as sound).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_contracts import world_digest
from lab_runner.axor_backend import AxorKernel, gate_with_governor, is_real_kernel_version
from lab_runner.kernel import Kernel

from .gating import (
    gated_args,
    normalize_value_hash,
    provenance_fidelity,
    provenance_unavailable_decision,
    redacted_untrusted_bindings,
)

PRODUCER_MODE = "instrumented_endpoint"


@dataclass
class EmittedEvent:
    """One event the instrumented agent emits over the gateway."""

    type: str  # "tool_result" | "tool_call_intent"
    tool: str
    # tool_result: values minted from the result, each {value_id, labels, sources, ...}
    values: list[dict[str, object]] = field(default_factory=list)
    # tool_call_intent: arg name -> value_id
    arg_bindings: dict[str, str] = field(default_factory=dict)
    # tool_call_intent: arg name -> concrete value (for effect resolution)
    args: dict[str, object] = field(default_factory=dict)


def assemble_and_gate(
    emitted: list[EmittedEvent],
    condition: dict[str, object],
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
    kernel: Kernel | AxorKernel,
    run_id: str,
    scenario_id: str,
    seed: str = "s000",
    labels_carried: bool = True,
    fixtures: dict[str, object] | None = None,
    trusted_runtime: bool = False,
) -> dict[str, object]:
    """Build a trace/v1 from emitted events and gate each sink call intent.

    Like the HTTP gateway, the gate decides on the args assembled from the
    bindings (`gated_args`), NOT the caller's concrete `item.args`; a mismatched
    concrete arg raises GatingError (fail closed), so a clean binding paired with
    a malicious value cannot launder an ALLOW between live decision and replay
    (review r9). Provenance fidelity comes from the trusted-runtime context, not
    a bare boolean.

    The passed `kernel` is NOT trusted blindly (review r17): it must match the
    condition it claims to run — its version equals `condition.kernel`, and a real
    `axor-core@X` condition MUST be an AxorKernel (a reference Kernel under a
    real-kernel label is refused). The real governor is driven through the shared
    `gate_with_governor`, exactly as the runner/replay do, so the in-process
    endpoint gives real governance instead of silently staying reference-only."""
    _assert_kernel_matches_condition(kernel, condition)
    values: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    registrations: list[tuple[str, object]] = []
    seq = 0
    seen: set[str] = set()
    for item in emitted:
        if item.type == "tool_result":
            for value in item.values:
                if value["value_id"] not in seen:
                    seen.add(str(value["value_id"]))
                    # every trace value must carry an authoritative
                    # canonical_value_hash (contracts trace_semantics, r13)
                    values.append(normalize_value_hash(value))
                    # accumulate the real governor's taint registrations: an
                    # untrusted-derived value carrying its bytes is taint-registered
                    if "untrusted_derived" in value.get("labels", []) and "decision_value" in value:
                        registrations.append((item.tool, value["decision_value"]))
            events.append({
                "seq": seq, "node": "root", "type": "tool_result", "tool": item.tool,
                "produces_value_ids": [str(v["value_id"]) for v in item.values],
            })
            seq += 1
        elif item.type == "tool_call_intent":
            events.append({
                "seq": seq, "node": "root", "type": "tool_call_intent", "tool": item.tool,
                "arg_bindings": dict(item.arg_bindings),
            })
            seq += 1
            values_by_id = {str(v["value_id"]): v for v in values}
            # authoritative args from the bindings; a conflicting concrete
            # assertion fails closed (shared with the HTTP gateway)
            authoritative = gated_args(
                manifests[item.tool], dict(item.arg_bindings), values_by_id,
                asserted=item.args or None,
            )
            # the SAME live/replay dispatch as the runner: the real governor for
            # an AxorKernel, the reference kernel otherwise (review r17)
            if isinstance(kernel, AxorKernel):
                effect: dict[str, object] = manifests[item.tool].get("effect", {})  # type: ignore[assignment]
                driving_args = list(effect.get("driving_args", []))  # type: ignore[arg-type]
                driving_value_id = (
                    item.arg_bindings.get(str(driving_args[0])) if driving_args else "v_none"
                )
                enforcement = str(condition["enforcement"])
                blind = redacted_untrusted_bindings(values, dict(item.arg_bindings))
                if enforcement != "off" and blind:
                    # SAME fail-closed rule as the HTTP gateway (review r18): a
                    # redacted untrusted-derived value bound to a gated arg leaves
                    # the governor's taint incomplete, so we DENY rather than let it
                    # decide fail-open. The two surfaces share the rule so they
                    # cannot drift.
                    decision = provenance_unavailable_decision(str(driving_value_id), blind)
                else:
                    decision = gate_with_governor(
                        kernel.config, enforcement, registrations,
                        item.tool, authoritative, str(driving_value_id),
                    )
            else:
                arg_labels = {
                    name: tuple(_labels_of(values, vid)) for name, vid in item.arg_bindings.items()
                }
                decision = kernel.decide(
                    enforcement=str(condition["enforcement"]),
                    manifest=manifests[item.tool],
                    args=authoritative,
                    arg_labels=arg_labels,
                    arg_bindings=item.arg_bindings,
                    inputs=inputs,
                    policy=condition.get("policy"),  # type: ignore[arg-type]
                )
            events.append({"seq": seq, "node": "root", "type": "gate_decision", "decision": decision})
            seq += 1
        else:
            # an unknown event type is NOT silently dropped (review r18): a
            # tool_result the assembler skips would leave its taint unregistered,
            # and an unrecognised intent would never be gated — both fail OPEN. The
            # HTTP gateway already 400s on this; the in-process path must reject it
            # too so the two surfaces cannot drift.
            raise ValueError(f"unknown emitted event type {item.type!r}")

    return {
        "schema_version": "trace/v1",
        "trace_id": f"t_{run_id}_ep_{seed}",
        "trial": {
            "run_id": run_id, "scenario_id": scenario_id,
            "condition_id": str(condition["id"]), "seed": seed, "repeat_index": 0,
        },
        "producer": {
            "mode": PRODUCER_MODE,
            "provenance_fidelity": provenance_fidelity(trusted_runtime, labels_carried),
            # the kernel that actually decided — validated to equal condition.kernel
            "kernel_version": str(kernel.version),
            "runtime": "lab-gateway@0.1",
        },
        "inputs_digest": world_digest(inputs, fixtures),
        "events": events,
        "values": values,
    }


def _assert_kernel_matches_condition(
    kernel: Kernel | AxorKernel, condition: dict[str, object]
) -> None:
    """The caller-supplied kernel must BE the condition's kernel — not a reference
    kernel wearing a real-kernel label (review r17). A version mismatch, or a real
    `axor-core@X` condition backed by a non-AxorKernel (or vice versa), is a hard
    error, so the assembled trace's `kernel_version` cannot lie about what ran."""
    version = str(condition["kernel"])
    if str(getattr(kernel, "version", "")) != version:
        raise ValueError(
            f"kernel version {getattr(kernel, 'version', None)!r} does not match "
            f"condition.kernel {version!r}"
        )
    real = is_real_kernel_version(version)
    if real and not isinstance(kernel, AxorKernel):
        raise ValueError(
            f"condition pins a real kernel {version!r} but the supplied kernel is not "
            "an AxorKernel — refusing to run the reference kernel under a real-kernel label"
        )
    if not real and isinstance(kernel, AxorKernel):
        raise ValueError(
            f"condition pins a reference kernel {version!r} but an AxorKernel was supplied"
        )


def _labels_of(values: list[dict[str, object]], value_id: str) -> tuple[str, ...]:
    for value in values:
        if value["value_id"] == value_id:
            return tuple(value["labels"])  # type: ignore[arg-type]
    return ()
