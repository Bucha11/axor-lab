"""The reference `decide` — a THIN ADAPTER over axor-core's gate, not a copy.

Honest scope: this is `reference_taint_floor_kernel` — a single pure `decide`
used by BOTH the live runner and replay (so the two never diverge and
counterfactual replay cannot silently lie), covering ONE gate: `taint_floor`.
It is NOT the paper's full 9-gate kernel, and `default_registry` returns the SAME
behavior for every pinned version string — the version is recorded metadata, not
a loaded historical kernel. For the real 9-gate sequence a condition pins an
installed axor-core build and `lab_runner.axor_backend` drives
`ToolCallGovernor` (`resolve_kernel` refuses a pin it cannot satisfy exactly,
never substituting this one).

What changed: the taint verdict itself is no longer written here. It used to be —
a second implementation of a predicate axor-core already owns, which nothing held
in agreement with the original and which the Control Plane then mirrored a THIRD
time for its incident export. `decide` now calls
`axor_core.policy.gates.taint_gate`.

That is possible because the gate never needed content. `ToolCallGovernor` needs
content because it is the LEDGER — the part that derives a value's provenance as
a session runs. The gate is a pure predicate over `(NormalizedIntent,
CausalRoot)`, and `CausalRoot` is `{sources, sensitive}` with
`is_tainted = bool(sources)` — which is exactly what Lab's ledger already knows
per argument (`arg_labels`). So the label world and the content world meet at
the same predicate; only the way the labels were derived differs.

What stays here, deliberately, is everything that is NOT the taint predicate:
the effect-class resolution from a manifest, the per-argument allowlist
supersession, the decision dict's shape (`trace/v1` validates it), and the two
fail-closed preconditions this kernel adds — an egress sink that declares no
driving args, and a driving arg with no resolvable provenance. Those are
admission checks Lab makes before the taint question is even meaningful; the
production kernel refuses the same situations earlier, through STRICT role
completeness on an unclassified tool.
"""

from __future__ import annotations

from dataclasses import dataclass

from axor_core.contracts.anomaly import NormalizedIntent
from axor_core.contracts.taint import TaintSource
from axor_core.policy.gates import taint_gate
from axor_core.taint.causal_root import CausalRoot
from lab_contracts.inputs import expand_list

from .effects import EGRESS_CLASSES, resolve_effect_class
from .errors import UnknownKernelError
from .ledger import LABEL_SENSITIVE, LABEL_UNTRUSTED

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


# Lab's ledger labels a value; axor-core's gate decides on a CausalRoot. They are
# the same statement — "this value carries an external source" — in two
# vocabularies, so the translation is total and carries no judgement of its own.
def _root_of(labels: tuple[str, ...]) -> CausalRoot:
    sources = (
        frozenset({TaintSource.UNKNOWN_EXTERNAL})
        if LABEL_UNTRUSTED in labels else frozenset()
    )
    return CausalRoot(sources=sources, sensitive=LABEL_SENSITIVE in labels)


# The structural projection an egress decision needs. Lab carries the egress
# signal in the manifest's effect class, not in a normalizer's output, so it is
# passed to the gate as the operator's `egress_sinks` declaration and the
# structural fields stay neutral.
def _neutral_intent(tool: str) -> NormalizedIntent:
    return NormalizedIntent(
        tool=tool, operation="other", target_kind="workdir",
        destination_kind="none", provenance="unknown",
        reads_secret_like_data=False, writes_outside_workdir=False,
        executes_generated_code=False, after_external_read=False,
        after_secret_access=False, data_flow="none",
    )


def _kernel_denies(tool: str, labels: tuple[str, ...], *, superseded: bool) -> bool:
    """Would axor-core's taint floor deny this egress call on this value?"""
    return taint_gate(
        tool,
        _neutral_intent(tool),
        _root_of(labels),
        floor_active=False,
        egress_sinks=frozenset({tool}),
        integrity_superseded=superseded,
    ) is not None


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
        # the driving value is a REAL ledger id, or None when none exists — a
        # fail-closed decision must not invent a fake `v_none`/`v_unresolved`
        # ledger value (which then fails trace validation and makes the most
        # interesting incidents unpublishable, review r14). `driving_unresolved`
        # carries the typed reason instead.
        if driving_args and driving_args[0] in arg_bindings:
            driving_value_id: str | None = arg_bindings[driving_args[0]]
            unresolved: dict[str, object] | None = None
        elif not driving_args:
            driving_value_id, unresolved = None, {"kind": "no_driving_args"}
        else:
            driving_value_id = None
            unresolved = {"kind": "unresolved_argument", "arg": driving_args[0]}

        def _decision(verdict: str, reason: str, *, dv: str | None = ...,  # type: ignore[assignment]
                      unres: dict[str, object] | None = ..., **extra: object) -> dict[str, object]:
            d: dict[str, object] = {
                "verdict": verdict, "gate": GATE_TAINT_FLOOR,
                "driving_value_id": driving_value_id if dv is ... else dv,
                "reason": reason, **extra,
            }
            u = unresolved if unres is ... else unres
            if d["driving_value_id"] is None and u is not None:
                d["driving_unresolved"] = u
            return d

        if enforcement == "off":
            return _decision("ALLOW", "enforcement off (observe-only); observation stays on")
        manifest_id = str(manifest["id"])
        effect_class = resolve_effect_class(manifest, args, inputs)
        allowlist = _resolve_allowlist(policy, inputs)
        if self.taint_floor_enabled and effect_class in EGRESS_CLASSES:
            # an egress sink with NO declared driving args cannot be
            # provenance-checked — fail closed rather than ALLOW an unverifiable
            # call (review r6: allowlist/empty-driving-args fail-open)
            if not driving_args:
                return _decision(
                    "DENY",
                    f"egress sink {manifest['id']} declares no driving_args; cannot "
                    "verify provenance (fail-closed)",
                    projection=PROJECTION_UNTRUSTED,
                )
            # check EVERY driving arg: an allowlisted arg supersedes the taint
            # floor for ITSELF only — it must not short-circuit ALLOW and leave a
            # later tainted arg (e.g. an exfiltrated body) unexamined (review r6)
            superseded: list[str] = []
            for arg_name in driving_args:
                labels = arg_labels.get(arg_name, ())
                # FAIL-CLOSED: an egress driving arg with no resolvable
                # provenance (missing binding / unknown or unlabeled value) is
                # DENIED, never allowed (review P0.6).
                if not labels:
                    # a real binding may exist (an unlabeled value) → use it; else
                    # the arg is genuinely unresolved → null + typed reason
                    bound = arg_bindings.get(arg_name)
                    return _decision(
                        "DENY",
                        f"egress sink {manifest['id']}: driving arg '{arg_name}' has no "
                        "resolvable provenance (fail-closed)",
                        dv=bound,
                        unres=None if bound is not None
                        else {"kind": "unresolved_argument", "arg": arg_name},
                        projection=PROJECTION_UNTRUSTED,
                    )
                # THE taint question, asked of axor-core's gate rather than
                # answered here. Allowlisting supersedes the integrity axis for
                # THIS arg only — it must not short-circuit ALLOW and leave a
                # later tainted arg (an exfiltrated body) unexamined (review r6),
                # so the gate is asked once per driving arg.
                allowlisted = args.get(arg_name) in allowlist
                if _kernel_denies(manifest_id, labels, superseded=allowlisted):
                    return _decision(
                        "DENY",
                        f"egress sink {manifest['id']}: driving arg '{arg_name}' is "
                        f"{LABEL_UNTRUSTED} and not allowlisted",
                        dv=arg_bindings[arg_name],
                        projection=PROJECTION_UNTRUSTED,
                    )
                # else: this arg is trusted, or untrusted-but-allowlisted
                # (enum-supersession, paper §6.3) — continue checking the rest
                if LABEL_UNTRUSTED in labels:
                    superseded.append(arg_name)
            reason = f"effect {effect_class}: every driving arg is trusted or allowlisted"
            if superseded:
                reason += f"; allowlisted (enum-supersession): {', '.join(superseded)}"
            return _decision("ALLOW", reason)
        # a non-egress ALLOW may still have no driving value (e.g. a read-only
        # tool with no driving args) — go through _decision so a null
        # driving_value_id carries its typed driving_unresolved reason, never a
        # bare null that trace_semantics rejects (review r14)
        return _decision("ALLOW", f"effect {effect_class}: no egress gate applies")


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
