"""bundle/v1 assembly and integrity verification.

The bundle is versioned, not vibes: kernel version, config hashes, model
params, seeds, trials, traces, aggregates — all content-hashed over the
canonical serialization. `verify_bundle` is what the server runs on upload
(runner-protocol handshake) before anything is trusted.
"""

from __future__ import annotations

from .canonical import content_hash
from .errors import BundleIntegrityError


def build_bundle(
    bundle_id: str,
    created: str,
    scenarios: list[dict[str, object]],
    conditions: list[dict[str, object]],
    tool_manifests: list[dict[str, object]],
    environment: dict[str, object],
    trials: list[dict[str, object]],
    aggregates: list[dict[str, object]],
    traces: dict[str, dict[str, object]],
    packaging: dict[str, object] | None = None,
) -> dict[str, object]:
    """Assemble a bundle/v1 dict; `created` is caller-supplied (determinism)."""
    hashes: dict[str, str] = {}
    for scenario in scenarios:
        hashes[f"scenario:{scenario['name']}"] = content_hash(scenario)
    for condition in conditions:
        hashes[f"condition:{condition['id']}"] = content_hash(condition)
    for manifest in tool_manifests:
        hashes[f"tool_manifest:{manifest['id']}"] = content_hash(manifest)
    for trace in traces.values():
        hashes[f"trace:{trace['trace_id']}"] = content_hash(trace)
    hashes["aggregates"] = content_hash(aggregates)
    bundle: dict[str, object] = {
        "schema_version": "bundle/v1",
        "bundle_id": bundle_id,
        "created": created,
        "scenarios": scenarios,
        "conditions": conditions,
        "tool_manifests": tool_manifests,
        "environment": environment,
        "trials": trials,
        "aggregates": aggregates,
        "content_hashes": hashes,
        "canonicalization": "JCS/RFC8785",
    }
    if packaging is not None:
        bundle["packaging"] = packaging
    return bundle


def verify_bundle(bundle: dict[str, object], traces: dict[str, dict[str, object]]) -> None:
    """Recompute every content hash; raise BundleIntegrityError on any mismatch."""
    errors: list[str] = []
    hashes: dict[str, str] = bundle["content_hashes"]  # type: ignore[assignment]
    for scenario in bundle["scenarios"]:  # type: ignore[union-attr]
        _check(hashes, f"scenario:{scenario['name']}", scenario, errors)
    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        _check(hashes, f"condition:{condition['id']}", condition, errors)
    for manifest in bundle["tool_manifests"]:  # type: ignore[union-attr]
        _check(hashes, f"tool_manifest:{manifest['id']}", manifest, errors)
    _check(hashes, "aggregates", bundle["aggregates"], errors)
    by_ref = {content_hash(t): t for t in traces.values()}
    for trial in bundle["trials"]:  # type: ignore[union-attr]
        ref = trial.get("trace_ref")
        if trial.get("status") == "completed" and ref not in by_ref:
            errors.append(f"trial {trial['trial_id']}: trace_ref {ref} has no matching trace")
    for trace in traces.values():
        _check(hashes, f"trace:{trace['trace_id']}", trace, errors)
    if errors:
        raise BundleIntegrityError("; ".join(errors))


def _check(hashes: dict[str, str], key: str, artifact: object, errors: list[str]) -> None:
    expected = hashes.get(key)
    if expected is None:
        errors.append(f"missing content hash for {key}")
    elif content_hash(artifact) != expected:
        errors.append(f"content hash mismatch for {key}")
