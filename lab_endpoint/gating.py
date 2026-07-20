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


def normalize_value_hash(value: dict[str, object]) -> dict[str, object]:
    """Return a copy of a ledger value with an AUTHORITATIVE canonical_value_hash.

    Every value in a trace must carry canonical_value_hash so the ledger is
    self-verifying (contracts trace_semantics, review r13). An endpoint value
    that carries its decision_value gets the hash computed from it here — the
    server derives it, never trusts a client-supplied hash. A redacted sensitive
    value (no decision_value) keeps whatever hash the producer supplied."""
    out = dict(value)
    if "decision_value" in out:
        out["canonical_value_hash"] = content_hash(out["decision_value"])
    return out


def decision_relevant_args(manifest: dict[str, object]) -> set[str]:
    """Arg names that can change the verdict: driving args plus any arg named
    in an effect-resolve rule's `when`. All must be bound so the gate decides
    over the same concrete values the tool will run."""
    effect: dict[str, object] = manifest.get("effect", {})  # type: ignore[assignment]
    names: set[str] = set(effect.get("driving_args", []))  # type: ignore[arg-type]
    for rule in effect.get("resolve", []):  # type: ignore[union-attr]
        names |= set(rule.get("when", {}).keys())
    return names


def required_args(manifest: dict[str, object]) -> set[str]:
    """The tool's schema-required args. A side-effecting call is not valid
    without them, so they must ALL be bound for authoritative_args to be a
    complete, executable tool call (review r10)."""
    schema: dict[str, object] = manifest.get("args_schema", {})  # type: ignore[assignment]
    return set(schema.get("required", []))  # type: ignore[arg-type]


def gated_args(
    manifest: dict[str, object],
    arg_bindings: dict[str, str],
    values_by_id: dict[str, dict[str, object]],
    asserted: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return the authoritative args the gate decides on — the COMPLETE tool
    call, assembled SOLELY from the bound ledger values (`arg_bindings →
    decision_value`), the same way exact replay reconstructs them.

    Every arg that must have provenance is bound, or this fails closed
    (GatingError): the decision-relevant args (driving + effect-resolution), the
    schema-required args, AND every arg the caller says it will actually pass
    (`asserted`). So `authoritative_args` is a full executable call — a
    cooperating proxy runs exactly it, never a bound subset topped up with
    unbound, unrecorded values (review r10). If the caller supplies a concrete
    `asserted` map, each bound arg it names must also match by JCS content hash,
    so a clean binding paired with a malicious concrete value is refused.
    """
    must_bind = decision_relevant_args(manifest) | required_args(manifest)
    if asserted is not None:
        must_bind |= set(asserted)  # every arg the caller will execute needs a value id
    unbound = sorted(must_bind - set(arg_bindings))
    if unbound:
        raise GatingError(f"these args must be bound to ledger values: {unbound}")
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
