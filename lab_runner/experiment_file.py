"""The `.axl` experiment file: resolution and validation (lifecycle stage
`validating`).

An .axl file is a single self-contained JSON document:

    {
      "experiment":     { ...experiment/v1... },
      "scenarios":      [ ...scenario/v1... ],
      "tool_manifests": [ ...tool-manifest/v1... ]
    }

`resolve` performs everything lifecycle.md requires before a run exists:
schema validation of every part, semantic scenario validation (tool bindings,
injection vector, sink, $inputs), scenario_ids resolution, agent_ref
resolution, and condition config-hash pinning. A file failing any check
never reaches execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lab_contracts import (
    ScenarioValidationError,
    condition_config_hash,
    validate_artifact,
)

from .agents import AgentAdapter, resolve_agent
from .errors import ExperimentFileError, UnknownAgentError
from .kernel import KernelRegistry, default_registry


@dataclass(frozen=True)
class ResolvedExperiment:
    """A validated, executable experiment."""

    experiment: dict[str, object]
    scenarios: tuple[dict[str, object], ...]
    manifests: dict[str, dict[str, object]]
    conditions: tuple[dict[str, object], ...]
    agent: AgentAdapter
    kernel_registry: KernelRegistry

    @property
    def repeats(self) -> int:
        return int(self.experiment["repeats"])  # type: ignore[arg-type]

    @property
    def trial_count(self) -> int:
        return len(self.scenarios) * len(self.conditions) * self.repeats


def load_axl(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentFileError((f"[validating] cannot read {path}: {exc}",)) from exc
    if not isinstance(document, dict):
        raise ExperimentFileError((f"[validating] {path}: top level must be an object",))
    return document


def resolve(document: dict[str, object]) -> ResolvedExperiment:
    """Validate the whole document; raise ExperimentFileError with ALL errors."""
    errors: list[str] = []
    experiment: dict[str, object] = document.get("experiment", {})  # type: ignore[assignment]
    scenarios: list[dict[str, object]] = list(document.get("scenarios", []))  # type: ignore[arg-type]
    manifests_list: list[dict[str, object]] = list(document.get("tool_manifests", []))  # type: ignore[arg-type]

    for key in ("experiment", "scenarios", "tool_manifests"):
        if key not in document:
            errors.append(f"[validating] missing top-level key '{key}'")
    if errors:
        raise ExperimentFileError(tuple(errors))

    errors += [f"[validating] experiment: {e}" for e in validate_artifact(experiment, "experiment")]
    for manifest in manifests_list:
        errors += [
            f"[validating] tool_manifest {manifest.get('id')}: {e}"
            for e in validate_artifact(manifest, "tool-manifest")
        ]
    manifests = {str(m["id"]): m for m in manifests_list}

    by_name: dict[str, dict[str, object]] = {}
    for scenario in scenarios:
        name = str(scenario.get("name"))
        by_name[name] = scenario
        errors += [
            f"[validating] scenario {name}: {e}" for e in validate_artifact(scenario, "scenario")
        ]
        try:
            from lab_contracts import validate_scenario

            validate_scenario(scenario, manifests)
        except ScenarioValidationError as exc:
            errors += [f"scenario {name}: {e}" for e in exc.errors]

    wanted = [str(s) for s in experiment.get("scenario_ids", [])]  # type: ignore[union-attr]
    for scenario_id in wanted:
        if scenario_id not in by_name:
            errors.append(
                f"[validating] experiment.scenario_ids: '{scenario_id}' not among scenarios"
            )

    conditions: list[dict[str, object]] = list(experiment.get("conditions", []))  # type: ignore[arg-type]
    pinned: list[dict[str, object]] = []
    for condition in conditions:
        entry = dict(condition)
        if "config_hash" not in entry:
            entry["config_hash"] = condition_config_hash(
                str(entry.get("kernel", "")), entry.get("policy")  # type: ignore[arg-type]
            )
        pinned.append(entry)

    agent: AgentAdapter | None = None
    try:
        agent = resolve_agent(str(experiment.get("agent_ref", "")))
    except UnknownAgentError as exc:
        errors.append(f"[validating] {exc}")

    if errors:
        raise ExperimentFileError(tuple(errors))
    assert agent is not None

    return ResolvedExperiment(
        experiment=experiment,
        scenarios=tuple(by_name[s] for s in wanted),
        manifests=manifests,
        conditions=tuple(pinned),
        agent=agent,
        kernel_registry=default_registry(tuple(str(c["kernel"]) for c in pinned)),
    )
