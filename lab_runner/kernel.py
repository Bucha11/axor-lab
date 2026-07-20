"""A REFERENCE `decide`, not the production axor-core kernel (review P0.2).

Honest scope: this is `reference_taint_floor_kernel` — a single pure `decide`
used by BOTH the live runner and replay (so the two never diverge and
counterfactual replay cannot silently lie), implementing ONE gate: `taint_floor`
(DENY an egress-class call whose driving argument carries `untrusted_derived`,
with allowlist enum-supersession). It is NOT the paper's full 9-gate kernel,
and `default_registry` returns the SAME behavior for every pinned version
string — the version is recorded metadata, not a loaded historical kernel.

Therefore Lab currently verifies *this reference kernel*, not a specific
production axor-core build. Real cross-version fidelity (loading
axor-core@X.Y.Z and replaying under it) is the integration tracked in
POST_MVP_PLAN.md; until then, a bundle's `kernel_version` documents intent, and
a KernelRegistry with genuinely different behaviors per version must be
constructed explicitly (regression checks do exactly that).
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts.inputs import expand_list

from .effects import EGRESS_CLASSES, resolve_effect_class
from .errors import UnknownKernelError
from .ledger import LABEL_UNTRUSTED

GATE_TAINT_FLOOR = "taint_floor"
PROJECTION_UNTRUSTED = "untrusted-derived"

# what the reference kernel actually EXECUTES. A policy value outside these sets
# would enter the config hash but never change a verdict, so a condition
# declaring it is rejected rather than silently ignored (review r4: a
# schema-valid condition must be fully executable by the kernel that runs it).
REFERENCE_SUPPORTED_PROFILES = frozenset({"strict", "default"})
REFERENCE_SUPPORTED_TRUST_MODELS = frozenset({"content-ledger"})


def unsupported_reference_policy_fields(policy: dict[str, object] | None) -> list[str]:
    """Policy values the reference kernel does not execute (would be hashed but
    ignored). Empty ⇒ the policy is fully executable by the reference kernel."""
    if not policy:
        return []
    errors: list[str] = []
    profile = policy.get("profile")
    if profile is not None and str(profile) not in REFERENCE_SUPPORTED_PROFILES:
        errors.append(
            f"policy.profile {profile!r} is not executed by the reference kernel "
            f"(supported: {sorted(REFERENCE_SUPPORTED_PROFILES)}) — it would enter the "
            "config_hash but never change a verdict"
        )
    trust_model = policy.get("trust_model")
    if trust_model is not None and str(trust_model) not in REFERENCE_SUPPORTED_TRUST_MODELS:
        errors.append(
            f"policy.trust_model {trust_model!r} is not executed by the reference kernel "
            f"(supported: {sorted(REFERENCE_SUPPORTED_TRUST_MODELS)})"
        )
    if policy.get("criticality_overrides"):
        errors.append(
            "policy.criticality_overrides is not implemented by the reference kernel; "
            "remove it or run a kernel that executes it"
        )
    return errors


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


REFERENCE_KERNEL = "reference_taint_floor_kernel"


@dataclass(frozen=True)
class Kernel:
    """A reference kernel's pure decision behavior (see module docstring —
    this is the taint_floor reference, not the production axor-core build the
    `version` string names)."""

    version: str
    taint_floor_enabled: bool = True

    @property
    def behavior_version(self) -> str:
        """Identity that reflects behavior-changing flags — so two kernels with
        the same version string but different gates cannot share a config
        identity (review r4). A taint_floor-disabled variant is a DIFFERENT
        kernel and says so, instead of masquerading as the pinned version."""
        if not self.taint_floor_enabled:
            return f"{self.version}+taint_floor=off"
        return self.version

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
