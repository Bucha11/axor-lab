"""artifact/v1 assembly — the portable output of a Run.

Suite Platform RFC §14. The artifact is the platform's user-facing noun: it
carries the suite, configuration, environment, agent identity, observations,
traces, metrics, EvidenceCases, regressions, reproduce instructions and hashes.

It WRAPS a bundle/v1 body rather than restating its fields. bundle/v1 is
content-addressed and its hashes are already published, so flattening it into a
new shape would invalidate every one of them; nesting keeps
``content_hash(artifact["bundle"])`` equal to the hash that bundle always had.
The practical consequence is that adopting artifact/v1 is a wrap, not a
migration — an existing bundle becomes an artifact without a single byte of it
changing.
"""

from __future__ import annotations

from .canonical import content_hash

REPRODUCIBILITY_EXACT = "exact_replay"
REPRODUCIBILITY_STATISTICAL = "statistically_reproducible"
REPRODUCIBILITY_NONE = "not_reproducible"


def build_artifact(
    artifact_id: str,
    created: str,
    bundle: dict[str, object],
    suite: dict[str, object] | None = None,
    agent_identity: dict[str, object] | None = None,
    evidence_cases: list[dict[str, object]] | None = None,
    regressions: list[dict[str, object]] | None = None,
    reproduce: dict[str, object] | None = None,
) -> dict[str, object]:
    """Assemble an artifact/v1 around an already-built bundle.

    `created` is caller-supplied for determinism, as in build_bundle.
    """
    artifact: dict[str, object] = {
        "schema_version": "artifact/v1",
        "artifact_id": artifact_id,
        "created": created,
        "bundle": bundle,
    }
    if suite is not None:
        artifact["suite"] = suite
    if agent_identity is not None:
        artifact["agent_identity"] = agent_identity
    if evidence_cases is not None:
        artifact["evidence_cases"] = evidence_cases
    if regressions is not None:
        artifact["regressions"] = regressions
    if reproduce is not None:
        artifact["reproduce"] = reproduce
    # the bundle is hashed AS A WHOLE, so a reader can check the embedded body
    # against a hash published before artifact/v1 existed
    artifact["content_hashes"] = {"bundle": content_hash(bundle)}
    return artifact


def reproducibility_of(bundle: dict[str, object], traces_included: bool) -> str:
    """The honest reproducibility ceiling for a bundle.

    Exact replay recomputes gate verdicts over frozen traces, so it needs both
    the traces AND a kernel that produced verdicts to recompute. A run with no
    governance has no verdicts to reproduce exactly — a fresh run of a
    stochastic agent can only be expected to agree within CI — so it claims
    `statistically_reproducible`, never `exact_replay`. Claiming otherwise would
    promise a bit-identical result that nothing in the artifact can deliver.
    """
    if not traces_included:
        return REPRODUCIBILITY_NONE
    conditions: list[dict[str, object]] = bundle.get("conditions", [])  # type: ignore[assignment]
    if any(c.get("kernel") for c in conditions):
        return REPRODUCIBILITY_EXACT
    return REPRODUCIBILITY_STATISTICAL
