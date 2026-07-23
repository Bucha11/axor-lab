"""Incident import — the ONE shared core behind the CLI `import-incident` and
the HTTP `POST /api/incidents` (control-plane-handoff.md §Second funnel).

A production trace + its scenario + tool manifests + the EXACT recorded
condition become a trace-replay bundle. The recorded condition is REQUIRED and
used verbatim — reconstructing it (enforcement=on, kernel from the trace)
silently loses enforcement mode, policy, allowlist, criticality overrides and
the config hash, so replay could then yield a different verdict than the
incident actually produced. Everything is validated (schema + semantics +
cross-references + config hash) and REPLAYED before anything is written.

Repo rule "replay is the same code": both surfaces call `import_incident`, so
an incident accepted over HTTP is byte-for-byte the bundle the CLI would build.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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

from .axor_backend import resolve_kernel
from .bundle_io import PACKAGING, write_bundle_dir
from .errors import IncidentImportError, IncidentReplayMismatch
from .kernel import default_registry
from .replay import REPLAY_MATCH, replay_trace_status


@dataclass(frozen=True)
class ImportResult:
    """A successfully imported incident: the trace-replay bundle plus the
    identifiers a caller needs to store/report it."""

    bundle: dict[str, object]
    trace: dict[str, object]
    trace_id: str
    scenario_id: str
    condition_id: str
    replay_status: str  # always REPLAY_MATCH — a mismatch raises instead


def _verdict_cores(decisions: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    """The replay-comparable core of each decision (verdict + gate + driving
    value) — what a mismatch report shows, never the free-text prose."""
    return [
        {
            "verdict": d.get("verdict"),
            "gate": d.get("gate"),
            "driving_value_id": d.get("driving_value_id"),
        }
        for d in decisions
    ]


def _recorded_decisions(trace: dict[str, object]) -> tuple[dict[str, object], ...]:
    return tuple(
        event["decision"]  # type: ignore[misc]
        for event in trace["events"]  # type: ignore[union-attr]
        if event.get("type") == "gate_decision"
    )


def import_incident(
    trace: dict[str, object],
    scenario: dict[str, object],
    manifests: list[dict[str, object]],
    condition: dict[str, object],
    out_dir: Path | None = None,
    *,
    created: str | None = None,
    overwrite: bool = False,
) -> ImportResult:
    """Validate + replay a production incident, build its trace-replay bundle,
    and (when `out_dir` is given) write it as an `axor-bundle-dir/v1`.

    Raises IncidentImportError on any validation failure and
    IncidentReplayMismatch (with structured detail) when the trace does not
    replay under its recorded condition. Nothing is written unless every check
    passes (replay BEFORE write)."""
    # 1. schema validation of every artifact
    for obj, name in ((trace, "trace"), (scenario, "scenario"), (condition, "condition")):
        errors = validate_artifact(obj, name)
        if errors:
            raise IncidentImportError(f"incident {name} is not conformant: {errors}")
    manifests_by_id: dict[str, dict[str, object]] = {}
    for manifest in manifests:
        errors = validate_artifact(manifest, "tool-manifest")
        if errors:
            raise IncidentImportError(
                f"incident manifest {manifest.get('id')} is not conformant: {errors}"
            )
        manifests_by_id[str(manifest["id"])] = manifest

    # 2. semantic + cross-reference validation
    try:
        validate_scenario(scenario, manifests_by_id)
    except ScenarioValidationError as exc:
        raise IncidentImportError(f"incident scenario failed semantic validation: {exc}") from exc
    trial: dict[str, object] = trace["trial"]  # type: ignore[assignment]
    if str(condition["id"]) != str(trial["condition_id"]):
        raise IncidentImportError(
            f"condition.id {condition['id']!r} != trace condition_id {trial['condition_id']!r}"
        )
    if str(scenario["name"]) != str(trial["scenario_id"]):
        raise IncidentImportError(
            f"scenario.name {scenario['name']!r} != trace scenario_id {trial['scenario_id']!r}"
        )

    # 3. config-hash verification (if the recorded condition carries one)
    if "config_hash" in condition:
        expected = condition_config_hash(str(condition["kernel"]), condition.get("policy"))  # type: ignore[arg-type]
        if str(condition["config_hash"]) != expected:
            raise IncidentImportError(
                f"condition config_hash {condition['config_hash']!r} != recomputed {expected!r}"
            )

    # 4. replay the incident under its OWN recorded condition before writing — a
    # wrong/reconstructed condition would surface here as a mismatch. Pass the
    # scenario inputs so a real-kernel `$inputs` allowlist expands to the concrete
    # values the incident actually ran under, not the symbolic ref (review r17).
    kernel = resolve_kernel(
        str(condition["kernel"]), manifests_by_id, condition.get("policy"),  # type: ignore[arg-type]
        default_registry((str(condition["kernel"]),)), scenario.get("inputs", {}),  # type: ignore[arg-type]
    )
    recomputed, status = replay_trace_status(
        trace, condition, kernel, manifests_by_id, scenario.get("inputs", {}),  # type: ignore[arg-type]
    )
    if status != REPLAY_MATCH:
        raise IncidentReplayMismatch(
            f"incident trace does not replay under its condition (status={status}) — "
            "refusing to import a bundle whose verdicts don't reproduce",
            detail={
                "status": status,
                "recorded_verdicts": _verdict_cores(_recorded_decisions(trace)),
                "recomputed_verdicts": _verdict_cores(recomputed),
            },
        )

    # a completed trial carries the runtime config it ran under, but this hash is
    # RECONSTRUCTED at import from the incident's condition + scenario inputs — the
    # original production trace never carried it, and this process did not observe
    # the runtime compilation. Mark it reconstructed_incident so config_provenance
    # reports the honest status and an evidence-backed CP export refuses it as
    # "the exact runtime config that actually ran in production" (review r21).
    incident_rch = runtime_config_hash(
        str(condition["kernel"]), condition.get("policy"), manifests,
        scenario.get("inputs", {}),  # type: ignore[arg-type]
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
    bundle = build_bundle(
        bundle_id="b_incident_" + content_hash(trace).removeprefix("sha256:")[:32],
        created=created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        scenarios=[scenario], conditions=[condition], tool_manifests=manifests,
        environment={"kernel_version": str(trace["producer"]["kernel_version"]),  # type: ignore[index]
                     "model": {"provider": "imported", "id": "production-incident"}},
        trials=trials, aggregates=[], traces={str(trace["trace_id"]): trace},
        packaging=dict(PACKAGING),
    )
    if out_dir is not None:
        write_bundle_dir(out_dir, bundle, {str(trace["trace_id"]): trace}, overwrite=overwrite)
    return ImportResult(
        bundle=bundle, trace=trace, trace_id=str(trace["trace_id"]),
        scenario_id=str(trial["scenario_id"]), condition_id=str(trial["condition_id"]),
        replay_status=status,
    )
