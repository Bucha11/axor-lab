"""The governance capability's condition→kernel wiring.

`gate_for_condition` resolves the kernel a condition pins into a `KernelGate`
marker (or None when the condition names no kernel, i.e. an observe-only arm).
The runners read that marker to know whether the governance capability is
present; the actual decision path is the real axor-core governor, driven through
`axor_wrap.WrappedToolset` in `lab_runner.wrap_engine`. There is no Lab-side
`decide` any more — one builder, one decision path, and it is axor-core's.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_capabilities.governance.axor_backend import (
    AxorKernel,
    KernelRegistry,
    default_registry,
    resolve_kernel,
)


@dataclass(frozen=True)
class KernelGate:
    """The resolved kernel + condition for a governed arm.

    A presence marker: its existence means the governance capability is wired
    for this condition. It carries no `decide` — the verdict is produced by the
    real governor inside the wrap engine, never re-implemented here."""

    kernel: AxorKernel
    condition: dict[str, object]


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
