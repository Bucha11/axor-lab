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
    # the integrity spine covers EVERY field, not just the artifacts — so
    # model metadata, trial statuses, failure reasons, timestamps, and
    # packaging cannot be edited after the fact (review P0.3)
    hashes["environment"] = content_hash(environment)
    hashes["trials"] = content_hash(trials)
    hashes["meta"] = content_hash({"bundle_id": bundle_id, "created": created})
    if packaging is not None:
        hashes["packaging"] = content_hash(packaging)
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


# the trial coordinate a trace's own `trial` block must agree with — this is
# the binding that stops one trace from "proving" a hundred fabricated trials
_TRIAL_COORDS = ("scenario_id", "condition_id", "seed", "repeat_index")


def verify_bundle(bundle: dict[str, object], traces: dict[str, dict[str, object]]) -> None:
    """Recompute every content hash AND verify the evidence graph.

    Hash checks prove each JSON object is intact; the graph checks prove the
    objects actually fit together — every ID is unique, and every completed
    trial is bound to exactly one trace whose own trial block agrees on
    scenario/condition/seed/repeat (review r3). Raises BundleIntegrityError on
    any mismatch."""
    errors: list[str] = []
    hashes: dict[str, str] = bundle["content_hashes"]  # type: ignore[assignment]
    for scenario in bundle["scenarios"]:  # type: ignore[union-attr]
        _check(hashes, f"scenario:{scenario['name']}", scenario, errors)
    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        _check(hashes, f"condition:{condition['id']}", condition, errors)
    for manifest in bundle["tool_manifests"]:  # type: ignore[union-attr]
        _check(hashes, f"tool_manifest:{manifest['id']}", manifest, errors)
    _check(hashes, "aggregates", bundle["aggregates"], errors)
    _check(hashes, "environment", bundle["environment"], errors)
    _check(hashes, "trials", bundle["trials"], errors)
    _check(hashes, "meta", {"bundle_id": bundle["bundle_id"], "created": bundle["created"]}, errors)
    if "packaging" in bundle:
        _check(hashes, "packaging", bundle["packaging"], errors)
    for trace in traces.values():
        _check(hashes, f"trace:{trace['trace_id']}", trace, errors)

    _verify_uniqueness(bundle, traces, errors)
    _verify_trial_trace_graph(bundle, traces, errors)
    if errors:
        raise BundleIntegrityError("; ".join(errors))


def _verify_uniqueness(
    bundle: dict[str, object], traces: dict[str, dict[str, object]], errors: list[str]
) -> None:
    """No duplicate display IDs anywhere — a duplicate silently overwrote the
    prior content-hash entry at build time, so the bundle would carry evidence
    for one object while claiming another."""
    for label, items, key in (
        ("scenario name", bundle["scenarios"], "name"),
        ("condition id", bundle["conditions"], "id"),
        ("tool_manifest id", bundle["tool_manifests"], "id"),
        ("trial_id", bundle["trials"], "trial_id"),
    ):
        seen: set[str] = set()
        for item in items:  # type: ignore[union-attr]
            ident = str(item.get(key))
            if ident in seen:
                errors.append(f"duplicate {label} {ident!r}")
            seen.add(ident)
    seen_traces: set[str] = set()
    for trace in traces.values():
        tid = str(trace["trace_id"])
        if tid in seen_traces:
            errors.append(f"duplicate trace_id {tid!r}")
        seen_traces.add(tid)


def _verify_trial_trace_graph(
    bundle: dict[str, object], traces: dict[str, dict[str, object]], errors: list[str]
) -> None:
    """Bidirectional Trial <-> Trace binding for completed trials.

    A completed trial's trace_ref must be the content hash of a trace whose own
    `trial` block agrees on every coordinate — so one trace cannot back several
    trials (their coordinates would differ from the trace's), and a trial cannot
    cite a trace from another scenario/condition. Every trace must be cited by a
    completed trial (no orphans), and the completed-trial ↔ trace mapping is
    one-to-one."""
    by_ref = {content_hash(t): t for t in traces.values()}
    cited: dict[str, str] = {}  # trace_ref -> trial_id that cites it
    for trial in bundle["trials"]:  # type: ignore[union-attr]
        if trial.get("status") != "completed":
            continue
        ref = str(trial.get("trace_ref"))
        trace = by_ref.get(ref)
        if trace is None:
            errors.append(f"trial {trial.get('trial_id')}: trace_ref {ref} has no matching trace")
            continue
        trace_trial: dict[str, object] = trace.get("trial", {})  # type: ignore[assignment]
        for coord in _TRIAL_COORDS:
            if str(trial.get(coord)) != str(trace_trial.get(coord)):
                errors.append(
                    f"trial {trial.get('trial_id')}: {coord} {trial.get(coord)!r} does not match "
                    f"its trace's {trace_trial.get(coord)!r} (trace_ref {ref})"
                )
        if ref in cited:
            errors.append(
                f"trace {ref} backs multiple trials ({cited[ref]} and {trial.get('trial_id')})"
            )
        cited[ref] = str(trial.get("trial_id"))
    for ref, trace in by_ref.items():
        if ref not in cited:
            errors.append(f"orphan trace {trace.get('trace_id')} is not cited by any completed trial")


def _check(hashes: dict[str, str], key: str, artifact: object, errors: list[str]) -> None:
    expected = hashes.get(key)
    if expected is None:
        errors.append(f"missing content hash for {key}")
    elif content_hash(artifact) != expected:
        errors.append(f"content hash mismatch for {key}")
