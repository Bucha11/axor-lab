"""The governance capability's `Gate` implementation.

This is the code that used to sit inside the general loop as `_gate`. Moving it
here is the substantive part of making governance a plugin: the loop now knows
only that a gate is something which may return a decision, so a run without one
has nothing to disable, no dead branch, and no import of kernel machinery.

Dispatch mirrors the slice runner's exactly — the real axor-core governor and
the reference kernel share one `decide`, so a verdict replays identically no
matter which execution path produced it.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_runner.axor_backend import AxorKernel, gate_with_governor, resolve_kernel
from lab_runner.kernel import Kernel, KernelRegistry, default_registry
from lab_runner.ledger import ValueLedger


@dataclass(frozen=True)
class KernelGate:
    """Gates a call with a resolved kernel under one condition."""

    kernel: Kernel
    condition: dict[str, object]

    def decide(
        self,
        tool: str,
        manifest: dict[str, object],
        args: dict[str, object],
        arg_bindings: dict[str, str],
        ledger: ValueLedger,
        inputs: dict[str, object],
    ) -> dict[str, object] | None:
        if isinstance(self.kernel, AxorKernel):
            registrations = [
                (str(vid), ledger.runtime_value(vid))
                for vid in ledger.untrusted_ids()
                if ledger.has_runtime_value(vid)
            ]
            # The driving value is the one the MANIFEST declares drives this
            # tool's effect — the same rule the reference kernel applies. An
            # earlier version guessed "first untrusted binding", which invented a
            # subject the manifest never nominated and, for a no-argument tool,
            # produced an empty driving id that failed trace validation outright.
            driving_args: list[str] = list(manifest["effect"].get("driving_args", []))  # type: ignore[union-attr]
            unresolved: dict[str, object] | None = None
            if driving_args and driving_args[0] in arg_bindings:
                driving: str | None = arg_bindings[driving_args[0]]
            elif not driving_args:
                # nothing about this call can be driven by tainted data
                driving, unresolved = None, {"kind": "no_driving_args"}
            else:
                driving = None
                unresolved = {"kind": "unresolved_argument", "arg": driving_args[0]}

            decision = gate_with_governor(
                self.kernel.config, str(self.condition["enforcement"]), registrations,
                str(manifest["id"]), args, driving or "",
            )
            if driving is None:
                # never a fabricated `v_none` ledger id: a fail-closed decision
                # that invents a value makes the most interesting incidents
                # unpublishable (review r14). The typed reason goes instead.
                decision["driving_value_id"] = None
                if unresolved is not None:
                    decision["driving_unresolved"] = unresolved
            return decision
        return self.kernel.decide(
            enforcement=str(self.condition["enforcement"]),
            manifest=manifest,
            args=args,
            arg_labels={name: ledger.labels_of(vid) for name, vid in arg_bindings.items()},
            arg_bindings=arg_bindings,
            inputs=inputs,
            policy=self.condition.get("policy"),  # type: ignore[arg-type]
        )


def gate_for_condition(
    condition: dict[str, object],
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
    registry: KernelRegistry | None = None,
) -> KernelGate | None:
    """A gate for this condition, or None when it names no kernel.

    None is the whole point: an observe-only arm resolves NO kernel, so a
    governance-free run never depends on a registry being able to resolve
    anything.
    """
    if not condition.get("kernel"):
        return None
    kernel = resolve_kernel(
        str(condition["kernel"]), manifests, condition.get("policy"),
        registry or default_registry((str(condition["kernel"]),)), inputs,
    )
    return KernelGate(kernel=kernel, condition=condition)
