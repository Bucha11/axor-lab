"""The reference `decide` — one pure implementation used by BOTH the live
runner and replay (axor-core architecture rule 0: two implementations of the
pipeline would diverge and counterfactual replay would silently lie).

Reference gate set: `taint_floor` only — DENY when an egress-class call's
driving argument carries `untrusted_derived`. Enforcement `off` records the
decision but always ALLOWs (ungoverned = observation on, enforcement off).
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts.inputs import expand_list

from .effects import EGRESS_CLASSES, resolve_effect_class
from .errors import UnknownKernelError
from .ledger import LABEL_UNTRUSTED

GATE_TAINT_FLOOR = "taint_floor"
PROJECTION_UNTRUSTED = "untrusted-derived"


def _resolve_allowlist(
    policy: dict[str, object] | None, inputs: dict[str, object]
) -> frozenset[object]:
    """The operator-declared trusted egress set (paper §6.3, condition.policy.

    allowlist). Entries may be literals or `$inputs.x` references (a referenced
    list splices). Static and attacker-inaccessible by construction — it is
    the condition, not the trace, that carries it.
    """
    if not policy:
        return frozenset()
    entries = policy.get("allowlist")
    if not entries:
        return frozenset()
    return frozenset(expand_list(list(entries), inputs))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Kernel:
    """A pinned kernel version's pure decision behavior."""

    version: str
    taint_floor_enabled: bool = True

    def decide(
        self,
        enforcement: str,
        manifest: dict[str, object],
        args: dict[str, object],
        arg_labels: dict[str, tuple[str, ...]],
        arg_bindings: dict[str, str],
        inputs: dict[str, object],
        policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Pure function of (recorded call state, condition) → decision dict."""
        driving_args: list[str] = list(manifest["effect"]["driving_args"])  # type: ignore[index]
        driving_value_id = arg_bindings.get(driving_args[0], "v_none") if driving_args else "v_none"
        if enforcement == "off":
            return {
                "verdict": "ALLOW",
                "gate": GATE_TAINT_FLOOR,
                "driving_value_id": driving_value_id,
                "reason": "enforcement off (observe-only); observation stays on",
            }
        effect_class = resolve_effect_class(manifest, args, inputs)
        allowlist = _resolve_allowlist(policy, inputs)
        if self.taint_floor_enabled and effect_class in EGRESS_CLASSES:
            for arg_name in driving_args:
                labels = arg_labels.get(arg_name, ())
                # FAIL-CLOSED: an egress driving arg with no resolvable
                # provenance (missing binding / unknown or unlabeled value) is
                # DENIED, never allowed. A client cannot launder a value past
                # the gate by omitting its lineage (review P0.6).
                if not labels:
                    return {
                        "verdict": "DENY",
                        "gate": GATE_TAINT_FLOOR,
                        "driving_value_id": arg_bindings.get(arg_name, "v_unresolved"),
                        "projection": PROJECTION_UNTRUSTED,
                        "reason": (
                            f"egress sink {manifest['id']}: driving arg '{arg_name}' has no "
                            "resolvable provenance (fail-closed)"
                        ),
                    }
                if LABEL_UNTRUSTED in labels:
                    # enum-supersession: an operator-declared allowlisted target
                    # supersedes the taint floor (paper §6.3) — this is the
                    # only sanctioned way to recover the over-taint utility cost.
                    if args.get(arg_name) in allowlist:
                        return {
                            "verdict": "ALLOW",
                            "gate": GATE_TAINT_FLOOR,
                            "driving_value_id": arg_bindings[arg_name],
                            "reason": (
                                f"egress sink {manifest['id']}: driving arg is "
                                "untrusted_derived but allowlisted (enum-supersession)"
                            ),
                        }
                    return {
                        "verdict": "DENY",
                        "gate": GATE_TAINT_FLOOR,
                        "driving_value_id": arg_bindings[arg_name],
                        "projection": PROJECTION_UNTRUSTED,
                        "reason": (
                            f"egress sink {manifest['id']} with {LABEL_UNTRUSTED} driving arg"
                        ),
                    }
        return {
            "verdict": "ALLOW",
            "gate": GATE_TAINT_FLOOR,
            "driving_value_id": driving_value_id,
            "reason": f"effect {effect_class}: no untrusted driving arg on an egress sink",
        }


@dataclass(frozen=True)
class KernelRegistry:
    """Maps pinned kernel version strings to decision behaviors."""

    kernels: tuple[Kernel, ...]

    def get(self, version: str) -> Kernel:
        for kernel in self.kernels:
            if kernel.version == version:
                return kernel
        raise UnknownKernelError(version)


def default_registry(versions: tuple[str, ...]) -> KernelRegistry:
    """A registry with the default gate set for every pinned version named.

    The reference kernel behavior is identical across versions; a variant
    (e.g. taint_floor disabled) must be constructed explicitly — regression
    checks do exactly that.
    """
    return KernelRegistry(kernels=tuple(Kernel(version=v) for v in dict.fromkeys(versions)))
