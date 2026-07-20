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
from .kernel import KernelRegistry, default_registry, unsupported_reference_policy_fields


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


def _pins_real_kernel(condition: dict[str, object]) -> bool:
    """True when the condition pins an installed axor-core build that executes
    its own policy (so the reference-kernel parity check does not apply)."""
    from .axor_backend import HAS_AXOR_CORE, real_kernel_version

    if not HAS_AXOR_CORE:
        return False
    return str(condition.get("kernel", "")) == real_kernel_version()


def _apply_run_mode(
    conditions: list[dict[str, object]], run_mode: str, errors: list[str]
) -> list[dict[str, object]]:
    """run_mode selects which conditions actually run (it is EXECUTED, not
    decorative): governed → enforcing only, ungoverned → baseline only,
    compare → all (review r4)."""
    if run_mode == "compare":
        return conditions
    if run_mode == "governed":
        selected = [c for c in conditions if str(c.get("enforcement")) == "on"]
    elif run_mode == "ungoverned":
        selected = [c for c in conditions if str(c.get("enforcement")) == "off"]
    else:
        errors.append(f"[validating] unknown run_mode {run_mode!r}")
        return conditions
    if not selected:
        errors.append(f"[validating] run_mode {run_mode!r} selects no condition")
    return selected


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
    # duplicate ids must be an error, not a silent last-wins overwrite
    manifests: dict[str, dict[str, object]] = {}
    for manifest in manifests_list:
        errors += [
            f"[validating] tool_manifest {manifest.get('id')}: {e}"
            for e in validate_artifact(manifest, "tool-manifest")
        ]
        mid = str(manifest.get("id"))
        if mid in manifests:
            errors.append(f"[validating] duplicate tool_manifest id '{mid}'")
        else:
            manifests[mid] = manifest

    from lab_contracts import validate_scenario

    by_name: dict[str, dict[str, object]] = {}
    for scenario in scenarios:
        name = str(scenario.get("name"))
        if name in by_name:
            errors.append(f"[validating] duplicate scenario name '{name}'")
        scenario_errors = validate_artifact(scenario, "scenario")
        errors += [f"[validating] scenario {name}: {e}" for e in scenario_errors]
        by_name.setdefault(name, scenario)
        # inline tool manifests (a full manifest in scenario.tools, not just a
        # $ref) are registered alongside top-level tool_manifests (review §4.5)
        for tool in scenario.get("tools", []):  # type: ignore[union-attr]
            if isinstance(tool, dict) and "$ref" not in tool and "id" in tool:
                errors += [
                    f"[validating] inline manifest {tool['id']}: {e}"
                    for e in validate_artifact(tool, "tool-manifest")
                ]
                manifests.setdefault(str(tool["id"]), tool)
        # two-stage: only run the semantic validator on a SCHEMA-VALID scenario.
        # validate_scenario dereferences scenario['violation'] / ['task_success']
        # unconditionally, so on a schema-invalid scenario it would raise a raw
        # KeyError instead of a clean [validating] error (review r2 §validation).
        if not scenario_errors:
            try:
                validate_scenario(scenario, manifests)
            except ScenarioValidationError as exc:
                errors += [f"scenario {name}: {e}" for e in exc.errors]
            # fixtures must satisfy each tool's result_schema (review r6)
            from .simulator import validate_fixture_results
            errors += [f"[validating] scenario {name}: {e}"
                       for e in validate_fixture_results(scenario, manifests)]

    wanted = [str(s) for s in experiment.get("scenario_ids", [])]  # type: ignore[union-attr]
    for scenario_id in wanted:
        if scenario_id not in by_name:
            errors.append(
                f"[validating] experiment.scenario_ids: '{scenario_id}' not among scenarios"
            )

    # the benchmark runner executes type=benchmark only; games run through
    # lab_games, so a type it does not execute is rejected, not silently ignored
    exp_type = str(experiment.get("type", "benchmark"))
    if exp_type != "benchmark":
        errors.append(
            f"[validating] experiment.type {exp_type!r} is not executed by the benchmark "
            "runner (games run through lab_games)"
        )

    conditions: list[dict[str, object]] = list(experiment.get("conditions", []))  # type: ignore[arg-type]
    pinned: list[dict[str, object]] = []
    for condition in conditions:
        entry = dict(condition)
        computed = condition_config_hash(
            str(entry.get("kernel", "")), entry.get("policy")  # type: ignore[arg-type]
        )
        # verify on EVERY resolve, not only when absent (review §4.6): a stale
        # or wrong config_hash must not silently flow into runs/replay/publish
        if "config_hash" in entry and entry["config_hash"] != computed:
            errors.append(
                f"[validating] condition '{entry.get('id')}': config_hash {entry['config_hash']} "
                f"does not match its kernel+policy ({computed})"
            )
        # policy/runtime parity: reject a policy field the reference kernel does
        # not execute (would be hashed but ignored) unless the condition pins a
        # real axor-core build that executes its own policy (review r4)
        if str(entry.get("enforcement")) == "on" and not _pins_real_kernel(entry):
            errors += [
                f"[validating] condition '{entry.get('id')}': {e}"
                for e in unsupported_reference_policy_fields(entry.get("policy"))  # type: ignore[arg-type]
            ]
        entry["config_hash"] = computed
        pinned.append(entry)

    # run_mode is EXECUTED: it selects which conditions actually run
    run_mode = str(experiment.get("run_mode", "compare"))
    pinned = _apply_run_mode(pinned, run_mode, errors)

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
