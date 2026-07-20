"""Shared gating rules for BOTH endpoint paths — the HTTP gateway and the
in-process assemble_and_gate SDK (review r8/r9).

The gate must decide on the value the ledger binding names, never a concrete
arg the caller supplies alongside it. Keeping this in one place means the two
producers cannot drift apart again: the r8 fix that closed the HTTP gateway
left the in-process path with the identical bypass because each had its own
copy of the logic.
"""

from __future__ import annotations

from lab_contracts import content_hash
from lab_runner.replay import resolve_args


class GatingError(Exception):
    """A tool-call intent that cannot be safely gated: a decision-relevant arg
    is unbound, or a caller-asserted concrete arg conflicts with its bound
    provenance value. Fails closed (never an ALLOW)."""


def decision_relevant_args(manifest: dict[str, object]) -> set[str]:
    """Arg names that can change the verdict: driving args plus any arg named
    in an effect-resolve rule's `when`. All must be bound so the gate decides
    over the same concrete values the tool will run."""
    effect: dict[str, object] = manifest.get("effect", {})  # type: ignore[assignment]
    names: set[str] = set(effect.get("driving_args", []))  # type: ignore[arg-type]
    for rule in effect.get("resolve", []):  # type: ignore[union-attr]
        names |= set(rule.get("when", {}).keys())
    return names


def gated_args(
    manifest: dict[str, object],
    arg_bindings: dict[str, str],
    values_by_id: dict[str, dict[str, object]],
    asserted: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return the authoritative args the gate must decide on — assembled SOLELY
    from the bound ledger values (`arg_bindings → decision_value`), the same way
    exact replay reconstructs them.

    Every decision-relevant arg must be bound. If the caller also supplies a
    concrete `asserted` args map, each bound arg it names must match by JCS
    content hash. Any violation raises GatingError (fail closed) — so a clean
    binding paired with a malicious concrete value is refused, never laundered.
    """
    unbound = sorted(decision_relevant_args(manifest) - set(arg_bindings))
    if unbound:
        raise GatingError(f"decision-relevant args must be bound to values: {unbound}")
    authoritative = resolve_args(arg_bindings, values_by_id)
    if asserted is not None:
        for name, value in authoritative.items():
            if name in asserted and content_hash(asserted[name]) != content_hash(value):
                raise GatingError(
                    f"arg {name!r} concrete value does not match its bound provenance value"
                )
    return authoritative


def provenance_fidelity(trusted_runtime: bool, labels_carried: bool) -> str:
    """explicit_flow_tracked asserts a TRUSTED runtime built the lineage with
    closed constructors — it is granted only when the operator vouches for the
    runtime (`trusted_runtime`) AND labels were carried. Caller-supplied labels
    alone are heuristic_attribution; they are never dressed up as tracked flow
    by a bare boolean, on either endpoint path (review r9)."""
    return "explicit_flow_tracked" if (trusted_runtime and labels_carried) else "heuristic_attribution"
