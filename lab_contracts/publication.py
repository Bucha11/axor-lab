"""publication/v1: immutable public record + typed claims + provenance axes.

The claims.md boundary is STRUCTURAL here: an `exactly_replayable` claim must
reference a trace; a `statistically_reproducible` claim must reference an
aggregate — a behavioral delta can never be typed exact. Reproductions accrue
in a separate append-only attestation log so the publication stays immutable.
"""

from __future__ import annotations

from .errors import ClaimTypingError

KIND_EXACT = "exactly_replayable"
KIND_STATISTICAL = "statistically_reproducible"
REPRODUCTION_KINDS = frozenset({"exact_replay", "fresh_live", "changed_model", "changed_kernel"})

DEFAULT_LIMITATIONS = (
    "measures Axor's own enforcement; reproduce independently",
    "model layer stochastic; only governance verdicts replay exact",
)


def make_claim(
    kind: str,
    text: str,
    support_ref: str,
    *,
    trace_refs: frozenset[str],
    aggregate_refs: frozenset[str],
) -> dict[str, object]:
    """Build one typed claim; enforce the exact/statistical boundary."""
    if kind == KIND_EXACT:
        if support_ref not in trace_refs:
            raise ClaimTypingError(
                f"exactly_replayable claim must be supported by a trace; got {support_ref!r} "
                "(a behavioral delta / aggregate is never exactly replayable)"
            )
    elif kind == KIND_STATISTICAL:
        if support_ref not in aggregate_refs:
            raise ClaimTypingError(
                f"statistically_reproducible claim must be supported by an aggregate; got {support_ref!r}"
            )
    else:
        raise ClaimTypingError(f"unknown claim kind {kind!r}")
    return {"kind": kind, "text": text, "support_ref": support_ref}


def build_publication(
    publication_id: str,
    bundle_ref: str,
    question: str,
    origin: str,
    integrity: str,
    claims: list[dict[str, object]],
    license_id: str,
    limitations: tuple[str, ...] = DEFAULT_LIMITATIONS,
    visibility: str = "unlisted",
    statistics_integrity: str | None = None,
) -> dict[str, object]:
    publication: dict[str, object] = {
        "schema_version": "publication/v1",
        "publication_id": publication_id,
        "bundle_ref": bundle_ref,
        "question": question,
        "immutable": True,
        "origin": origin,
        "integrity": integrity,
        "claims": claims,
        "limitations": list(limitations),
        "license": license_id,
        "visibility": visibility,
        "reproductions_ref": f"attlog:{publication_id}",
    }
    if statistics_integrity is not None:
        publication["statistics_integrity"] = statistics_integrity
    return publication


def add_reproduction(
    log: tuple[dict[str, object], ...],
    attestation: dict[str, object],
    known_keys: dict[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Append to the attestation log — the publication itself never changes.

    Review §6.4: reproductions are provenance, not a free counter. This
    (1) rejects an unknown kind, (2) de-duplicates by (attester, kind,
    publication) so the count can't be inflated by re-posting, and (3) when the
    attestation carries a `signature` + `by` key that is KNOWN, verifies it and
    marks it `verified` — an unknown-key or bad signature is rejected rather
    than silently counted as verified."""
    if attestation.get("kind") not in REPRODUCTION_KINDS:
        raise ClaimTypingError(f"unknown reproduction kind {attestation.get('kind')!r}")
    key = (attestation.get("by"), attestation.get("kind"), attestation.get("publication_id"))
    if any((a.get("by"), a.get("kind"), a.get("publication_id")) == key for a in log):
        return log  # idempotent: a duplicate attestation does not inflate the count
    entry = dict(attestation)
    signature = entry.pop("signature", None)
    if signature is not None:
        pubkey = (known_keys or {}).get(str(entry.get("by")))
        if pubkey is None:
            raise ClaimTypingError(f"attestation signed by unknown key {entry.get('by')!r}")
        from .signing import SignatureInvalid, verify_bundle_signature

        try:
            # sign over the attestation body (minus signature), same crypto path
            verify_bundle_signature({"content_hashes": entry}, signature, pubkey)
        except SignatureInvalid as exc:
            raise ClaimTypingError(f"attestation signature invalid: {exc}") from exc
        entry["verified"] = True
    return log + (entry,)


def provenance_axes(
    publication: dict[str, object], log: tuple[dict[str, object], ...]
) -> dict[str, object]:
    """The three INDEPENDENT axes a catalog card composes — never one badge."""
    return {
        "origin": publication["origin"],
        "integrity": publication["integrity"],
        "reproductions": {
            "count": len(log),
            "kinds": sorted({str(a["kind"]) for a in log}),
        },
    }
