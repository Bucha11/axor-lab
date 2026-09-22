"""The second funnel: a production trace becomes a bundle you can test against.

`contracts/control-plane-handoff.md` §Second funnel. An incident that already
happened is evidence; importing it turns that evidence into something a policy
can be tested against, pinned, and exported — the same artifacts a designed run
produces, so every downstream verb works on it unchanged.

Nothing is written before everything is checked: schema, semantics,
cross-references, the recorded condition's config hash, and a full replay under
that condition. An import that cannot reproduce its own verdicts is refused
rather than stored as evidence of something it does not show.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .outcomes import Outcome


@dataclass(frozen=True)
class IncidentImportResult:
    """A trace-replay bundle built from one production incident."""

    outcome: Outcome
    directory: Path
    bundle_id: str
    trace_id: str
    replay_status: str


def import_incident(
    trace: dict[str, object],
    scenario: dict[str, object],
    manifests: list[dict[str, object]],
    condition: dict[str, object],
    *,
    out: Path,
    created: str | None = None,
    overwrite: bool = False,
) -> IncidentImportResult:
    """Validate, replay and materialize an incident as a trace-replay bundle.

    The recorded condition is REQUIRED and used verbatim — reconstructing it
    (enforcement=on, kernel from the trace) silently loses enforcement mode,
    policy, allowlist, criticality overrides and the config hash, so replay could
    then yield a different verdict than the incident actually produced.

    Raises `RunnerError` for any artifact that is not conformant, does not
    cross-reference, or does not replay.
    """
    from lab_capabilities.governance import default_registry, resolve_kernel
    from lab_capabilities.governance.replay import REPLAY_MATCH, replay_trace_status
    from lab_contracts import (
        CONFIG_COMPILER_VERSION,
        ScenarioValidationError,
        build_bundle,
        condition_config_hash,
        content_hash,
        runtime_config_hash,
        validate_artifact,
        validate_scenario,
    )
    from lab_runner.bundle_io import PACKAGING, write_bundle_dir
    from lab_runner.errors import RunnerError

    # 1. schema validation of every artifact
    for obj, name in ((trace, "trace"), (scenario, "scenario"), (condition, "condition")):
        errors = validate_artifact(obj, name)
        if errors:
            raise RunnerError(f"incident {name} is not conformant: {errors}")
    manifests_by_id: dict[str, dict[str, object]] = {}
    for manifest in manifests:
        errors = validate_artifact(manifest, "tool-manifest")
        if errors:
            raise RunnerError(f"incident manifest {manifest.get('id')} is not conformant: {errors}")
        manifests_by_id[str(manifest["id"])] = manifest

    # 2. semantic + cross-reference validation
    try:
        validate_scenario(scenario, manifests_by_id)
    except ScenarioValidationError as exc:
        raise RunnerError(f"incident scenario failed semantic validation: {exc}") from exc
    trial: dict[str, object] = trace["trial"]  # type: ignore[assignment]
    if str(condition["id"]) != str(trial["condition_id"]):
        raise RunnerError(
            f"condition.id {condition['id']!r} != trace condition_id {trial['condition_id']!r}"
        )
    if str(scenario["name"]) != str(trial["scenario_id"]):
        raise RunnerError(
            f"scenario.name {scenario['name']!r} != trace scenario_id {trial['scenario_id']!r}"
        )

    # 3. config-hash verification (if the recorded condition carries one)
    if "config_hash" in condition:
        expected = condition_config_hash(str(condition["kernel"]), condition.get("policy"))  # type: ignore[arg-type]
        if str(condition["config_hash"]) != expected:
            raise RunnerError(
                f"condition config_hash {condition['config_hash']!r} != recomputed {expected!r}"
            )

    # 4. replay the incident under its OWN recorded condition before writing — a
    # wrong/reconstructed condition would surface here as a mismatch. Pass the
    # scenario inputs so a real-kernel `$inputs` allowlist expands to the concrete
    # values the incident actually ran under, not the symbolic ref (review r17).
    inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
    kernel = resolve_kernel(
        str(condition["kernel"]), manifests_by_id, condition.get("policy"),  # type: ignore[arg-type]
        default_registry((str(condition["kernel"]),)), inputs,
    )
    _, status = replay_trace_status(trace, condition, kernel, manifests_by_id, inputs)
    if status != REPLAY_MATCH:
        raise RunnerError(
            f"incident trace does not replay under its condition (status={status}) — "
            "refusing to import a bundle whose verdicts don't reproduce"
        )

    # a completed trial carries the runtime config it ran under, but this hash is
    # RECONSTRUCTED at import from the incident's condition + scenario inputs — the
    # original production trace never carried it, and this process did not observe
    # the runtime compilation. Mark it reconstructed_incident so config_provenance
    # reports the honest status and an evidence-backed CP export refuses it as
    # "the exact runtime config that actually ran in production" (review r21).
    incident_rch = runtime_config_hash(
        str(condition["kernel"]), condition.get("policy"), manifests, inputs,  # type: ignore[arg-type]
    )
    trials = [{
        "trial_id": content_hash(trace), "scenario_id": str(trial["scenario_id"]),
        "condition_id": str(trial["condition_id"]), "seed": str(trial["seed"]),
        "repeat_index": int(trial["repeat_index"]), "status": "completed",
        "trace_ref": content_hash(trace),
        "runtime_config_hash": incident_rch,
        "config_compiler_version": CONFIG_COMPILER_VERSION,
        "runtime_provenance": "reconstructed_incident",
    }]
    bundle_id = "b_incident_" + content_hash(trace).removeprefix("sha256:")[:32]
    traces = {str(trace["trace_id"]): trace}
    bundle = build_bundle(
        bundle_id=bundle_id,
        created=created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        scenarios=[scenario], conditions=[condition], tool_manifests=manifests,
        environment={"kernel_version": str(trace["producer"]["kernel_version"]),  # type: ignore[index]
                     "model": {"provider": "imported", "id": "production-incident"}},
        trials=trials, aggregates=[], traces=traces,
        packaging=dict(PACKAGING),
    )
    write_bundle_dir(Path(out), bundle, traces, overwrite=overwrite)
    return IncidentImportResult(
        outcome=Outcome.OK,
        directory=Path(out),
        bundle_id=bundle_id,
        trace_id=str(trace["trace_id"]),
        replay_status=status,
    )
