"""bundle/v1 assembly and integrity verification.

The bundle is versioned, not vibes: kernel version, config hashes, model
params, seeds, trials, traces, aggregates — all content-hashed over the
canonical serialization. `verify_bundle` is what the server runs on upload
(runner-protocol handshake) before anything is trusted.
"""

from __future__ import annotations

from .canonical import content_hash, world_digest
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


def evidence_lineage_ref(bundle: dict[str, object]) -> str:
    """A STABLE identifier for the EVIDENCE a bundle carries, invariant to how it
    is packaged (review r15).

    `content_hash(bundle)` (the publication's bundle_ref) commits to EVERY field
    — bundle_id, created, packaging, the content-hash map — so re-serialising the
    same experiment with a fresh bundle_id/created yields a different bundle_ref.
    That made an evidence-level takedown escapable: repackage the taken-down
    evidence and it hashes differently. The lineage ref hashes ONLY the
    load-bearing evidence — the scenarios, conditions, tool manifests, the
    coordinates+trace refs of the COMPLETED trials, and the aggregate definitions
    — so cosmetic repackaging maps to the SAME lineage and a takedown is final."""
    completed = sorted(
        [
            str(t.get("scenario_id")), str(t.get("condition_id")), str(t.get("seed")),
            str(t.get("repeat_index")), str(t.get("trace_ref", "")),
        ]
        for t in bundle.get("trials", [])  # type: ignore[union-attr]
        if t.get("status") == "completed"
    )
    aggregates = sorted(
        [str(a.get("metric")), str(a.get("condition_id")), str(a.get("comparison_design", "matched_pairs"))]
        for a in bundle.get("aggregates", [])  # type: ignore[union-attr]
    )
    # ID → content-hash MAPS, not raw arrays: the manifest/scenario/condition
    # order is not part of the executable semantics (the server/replay index by
    # id), so a cosmetic reordering must map to the SAME lineage — an array-order
    # hash let a takedown be dodged by permuting the arrays (review r16). Canonical
    # JSON sorts the map keys, making the hash order-independent.
    lineage = {
        "scenarios": {str(s.get("name")): content_hash(s) for s in bundle.get("scenarios", [])},  # type: ignore[union-attr]
        "conditions": {str(c.get("id")): content_hash(c) for c in bundle.get("conditions", [])},  # type: ignore[union-attr]
        "tool_manifests": {str(m.get("id")): content_hash(m) for m in bundle.get("tool_manifests", [])},  # type: ignore[union-attr]
        "completed_trials": completed,
        "aggregates": aggregates,
    }
    return content_hash(lineage)


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
    _verify_cross_references(bundle, traces, errors)
    _verify_trial_trace_graph(bundle, traces, errors)
    _verify_trace_metadata(bundle, traces, errors)
    if errors:
        raise BundleIntegrityError("; ".join(errors))


def _verify_cross_references(
    bundle: dict[str, object], traces: dict[str, dict[str, object]], errors: list[str]
) -> None:
    """Every trial's coordinates and every trace's tools must RESOLVE in-bundle.

    The hash graph proves a completed trial is bound to the right trace, but it
    never checked that the scenario/condition a trial NAMES actually exist, nor
    that a tool a trace invokes has a manifest. So a failed/excluded trial could
    cite a phantom scenario or condition (padding a denominator or inventing a
    baseline that never ran), and a trace could invoke a sink tool with no
    manifest — an egress the governor config never saw — and the bundle would
    still verify. Both are integrity failures now (review r12)."""
    scenario_names = {str(s["name"]) for s in bundle["scenarios"]}  # type: ignore[union-attr]
    condition_ids = {str(c["id"]) for c in bundle["conditions"]}  # type: ignore[union-attr]
    manifest_ids = {str(m["id"]) for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    for trial in bundle["trials"]:  # type: ignore[union-attr]
        # applies to EVERY trial, completed or failed — a failed trial still
        # contributes to the denominator / missingness accounting, so it must
        # not reference a scenario or condition that isn't in the bundle
        sid = str(trial.get("scenario_id"))
        if sid not in scenario_names:
            errors.append(
                f"trial {trial.get('trial_id')}: scenario_id {sid!r} is not a bundle scenario"
            )
        cid = str(trial.get("condition_id"))
        if cid not in condition_ids:
            errors.append(
                f"trial {trial.get('trial_id')}: condition_id {cid!r} is not a bundle condition"
            )
    for trace in traces.values():
        events: list[dict[str, object]] = trace.get("events", [])  # type: ignore[assignment]
        for event in events:
            tool = event.get("tool")
            if tool is not None and str(tool) not in manifest_ids:
                errors.append(
                    f"trace {trace.get('trace_id')}: event invokes tool {tool!r} "
                    "with no tool_manifest in the bundle"
                )


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


def _verify_trace_metadata(
    bundle: dict[str, object], traces: dict[str, dict[str, object]], errors: list[str]
) -> None:
    """Bind the LOAD-BEARING trace metadata to the bundle it lives in (review r8).

    The trace schema says producer.kernel_version and inputs_digest describe the
    exact world a trace was produced in, but the graph verifier only checked the
    trial coordinates — so a trace could claim one producer kernel or inputs
    digest yet sit in a bundle whose condition/scenario say otherwise, and replay
    (which reads the bundle's kernel + inputs, not the trace's) would happily
    reproduce it. This makes the provenance description non-authoritative. Here:
      - producer.kernel_version must equal the kernel of the condition the trial
        ran under;
      - inputs_digest must bind the scenario's inputs + fixtures, the exact bytes
        the runner hashed;
      - a global environment.kernel_version, if present, must be one of the
        conditions' kernels — never a third, unrelated value.
    """
    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    conditions = {str(c["id"]): c for c in bundle["conditions"]}  # type: ignore[union-attr]
    by_ref = {content_hash(t): t for t in traces.values()}
    for trial in bundle["trials"]:  # type: ignore[union-attr]
        if trial.get("status") != "completed":
            continue
        trace = by_ref.get(str(trial.get("trace_ref")))
        if trace is None:
            continue  # missing-trace already reported by the graph check
        producer: dict[str, object] = trace.get("producer", {})  # type: ignore[assignment]
        cond = conditions.get(str(trial.get("condition_id")))
        if cond is not None and str(producer.get("kernel_version")) != str(cond.get("kernel")):
            errors.append(
                f"trace {trace.get('trace_id')}: producer.kernel_version "
                f"{producer.get('kernel_version')!r} does not match condition "
                f"{cond.get('id')!r} kernel {cond.get('kernel')!r}"
            )
        scen = scenarios.get(str(trial.get("scenario_id")))
        # inputs_digest is REQUIRED for a producer that claims to track the world
        # it ran in (wrapped_code, instrumented_endpoint) — otherwise a caller
        # could drop the field to dodge the binding (review r9). It must equal the
        # ONE world_digest over the scenario's inputs + fixtures.
        mode = str(producer.get("mode", ""))
        if "inputs_digest" not in trace and mode in ("wrapped_code", "instrumented_endpoint"):
            errors.append(
                f"trace {trace.get('trace_id')}: producer.mode {mode!r} requires an inputs_digest"
            )
        if scen is not None and "inputs_digest" in trace:
            expected = world_digest(scen.get("inputs", {}), scen.get("fixtures", {}))  # type: ignore[arg-type]
            if str(trace.get("inputs_digest")) != expected:
                errors.append(
                    f"trace {trace.get('trace_id')}: inputs_digest does not bind "
                    f"scenario {scen.get('name')!r} inputs+fixtures"
                )
    environment: dict[str, object] = bundle.get("environment", {})  # type: ignore[assignment]
    kernels = {str(c.get("kernel")) for c in bundle["conditions"]}  # type: ignore[union-attr]
    env_kernel = environment.get("kernel_version")
    if env_kernel is not None and str(env_kernel) not in kernels:
        errors.append(
            f"environment.kernel_version {env_kernel!r} matches no condition kernel "
            f"{sorted(kernels)}"
        )
    # a mixed-kernel bundle records kernel_versions (plural); it must name exactly
    # the distinct condition kernels — no phantom kernel, none missing (review r15)
    env_kernels = environment.get("kernel_versions")
    if env_kernels is not None and set(map(str, env_kernels)) != kernels:  # type: ignore[arg-type]
        errors.append(
            f"environment.kernel_versions {sorted(map(str, env_kernels))} != the condition "  # type: ignore[arg-type]
            f"kernels {sorted(kernels)}"
        )


def _check(hashes: dict[str, str], key: str, artifact: object, errors: list[str]) -> None:
    expected = hashes.get(key)
    if expected is None:
        errors.append(f"missing content hash for {key}")
    elif content_hash(artifact) != expected:
        errors.append(f"content hash mismatch for {key}")
