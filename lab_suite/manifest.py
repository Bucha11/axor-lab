"""suite/v1 — load, validate, resolve.

Resolution turns a manifest into a `ResolvedSuite`: the concrete scenarios,
tool manifests and conditions a run will execute against. It is the point where
"what the author wrote" becomes "what will run", so it is also where the
manifest's semantic rules are enforced — the ones JSON Schema cannot express.

Those rules exist because each of them is a way a manifest can be structurally
valid and still describe something incoherent, and every one of them is cheaper
to catch here than to discover from a finished run's results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from lab_contracts import validate_artifact, validate_scenario
from lab_contracts.errors import ScenarioValidationError

from .errors import SuiteValidationError

GOVERNANCE_CAPABILITY = "governance"

# aggregations that compare arms; each needs >= 2 conditions to be meaningful
_COMPARISON_TESTS = frozenset({"mcnemar", "two_proportion"})


@dataclass(frozen=True)
class ResolvedSuite:
    """A manifest with everything a run needs, already resolved and checked."""

    manifest: dict[str, object]
    scenarios: tuple[dict[str, object], ...]
    manifests: dict[str, dict[str, object]]
    conditions: tuple[dict[str, object], ...]
    repeats: int
    metrics: tuple[dict[str, object], ...] = field(default_factory=tuple)
    aggregations: tuple[dict[str, object], ...] = field(default_factory=tuple)
    evaluators: tuple[dict[str, object], ...] = field(default_factory=tuple)
    regressions: tuple[dict[str, object], ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        return str(self.manifest["id"])

    @property
    def governed(self) -> bool:
        return bool(self.conditions)

    def trial_count(self) -> int:
        arms = len(self.conditions) or 1
        return len(self.scenarios) * arms * self.repeats


def load_manifest(path: str | Path) -> dict[str, object]:
    return json.loads(Path(path).read_text())


def validate_manifest(
    manifest: dict[str, object],
    scenario_registry: dict[str, dict[str, object]] | None = None,
) -> list[str]:
    """Every problem with a manifest, schema and semantic, in one list."""
    errors = [f"[schema] {e}" for e in validate_artifact(manifest, "suite")]
    if errors:
        # semantic checks read fields the schema just rejected; reporting both
        # would bury the real error under cascading noise
        return errors

    scenarios = list(manifest.get("scenarios") or [])
    refs = list(manifest.get("scenario_refs") or [])
    if not scenarios and not refs:
        errors.append(
            "[suite] no scenarios: a suite must carry inline `scenarios` or name "
            "`scenario_refs`, or it describes an experiment with nothing to run"
        )
    registry = scenario_registry or {}
    for ref in refs:
        if str(ref) not in registry:
            errors.append(f"[suite] scenario_ref {ref!r} resolves to nothing")

    execution: dict[str, object] = manifest.get("execution") or {}  # type: ignore[assignment]
    conditions = list(execution.get("conditions") or [])
    capabilities = [str(c) for c in manifest.get("capabilities") or []]

    # governance is opt-in BOTH ways: declaring the capability without arms is a
    # suite that thinks it is measuring governance and is not, and supplying arms
    # without declaring it hides a kernel dependency from anyone reading the
    # capability list.
    if GOVERNANCE_CAPABILITY in capabilities and not conditions:
        errors.append(
            "[suite] capabilities declares 'governance' but execution.conditions is "
            "empty — nothing would be governed"
        )
    if conditions and GOVERNANCE_CAPABILITY not in capabilities:
        errors.append(
            "[suite] execution.conditions is set but 'governance' is not in "
            "capabilities — a kernel dependency must be declared, not implied"
        )

    evaluation: dict[str, object] = manifest.get("evaluation") or {}  # type: ignore[assignment]
    metric_names = {str(m["name"]) for m in evaluation.get("metrics") or []}  # type: ignore[index,union-attr]
    evaluator_products = {
        str(e["produces"]) for e in evaluation.get("evaluators") or []  # type: ignore[index,union-attr]
    }
    for metric in evaluation.get("metrics") or []:  # type: ignore[union-attr]
        if str(metric.get("source")) == "evaluator":
            source = str(metric.get("from", ""))
            if source and source not in {
                str(e["id"]) for e in evaluation.get("evaluators") or []  # type: ignore[index,union-attr]
            } and source not in evaluator_products:
                errors.append(
                    f"[suite] metric {metric.get('name')!r} reads evaluator {source!r} "
                    "which the suite does not declare"
                )

    for agg in evaluation.get("aggregations") or []:  # type: ignore[union-attr]
        metric = str(agg.get("metric", ""))
        if metric not in metric_names:
            errors.append(
                f"[suite] aggregation over metric {metric!r} which the suite does not declare"
            )
        test = str(agg.get("test", "none"))
        if test in _COMPARISON_TESTS and len(conditions) < 2:
            # the single most misleading manifest a author can write: it looks
            # like it will produce a comparison, and it cannot
            errors.append(
                f"[suite] aggregation on {metric!r} asks for a {test!r} comparison but the "
                f"suite declares {len(conditions)} condition(s) — a comparison needs 2"
            )

    seed_policy = str(execution.get("seed_policy", "per_repeat"))
    if seed_policy == "random" and any(
        str(a.get("test", "none")) == "mcnemar" for a in evaluation.get("aggregations") or []  # type: ignore[union-attr]
    ):
        errors.append(
            "[suite] seed_policy 'random' forfeits the paired trials a matched-pairs "
            "McNemar needs — use 'fixed' or 'per_repeat'"
        )

    topology: dict[str, object] = manifest.get("topology") or {}  # type: ignore[assignment]
    agents = list(manifest.get("agents") or [])
    if str(topology.get("kind", "single")) == "single" and len(agents) > 1:
        errors.append(
            f"[suite] topology 'single' but {len(agents)} agents declared — say which "
            "topology relates them"
        )
    return errors


def resolve_suite(
    manifest: dict[str, object],
    scenario_registry: dict[str, dict[str, object]] | None = None,
    tool_manifests: dict[str, dict[str, object]] | None = None,
) -> ResolvedSuite:
    """Validate and resolve, or raise SuiteValidationError with every failure."""
    errors = validate_manifest(manifest, scenario_registry)
    if errors:
        raise SuiteValidationError(tuple(errors))

    registry = scenario_registry or {}
    scenarios: list[dict[str, object]] = [
        dict(s) for s in manifest.get("scenarios") or []  # type: ignore[arg-type]
    ]
    # a ref is resolved to its BODY and frozen into the run, so a later registry
    # change cannot retroactively alter what a finished artifact says was run
    scenarios.extend(dict(registry[str(ref)]) for ref in manifest.get("scenario_refs") or [])

    environment: dict[str, object] = manifest.get("environment") or {}  # type: ignore[assignment]
    manifests: dict[str, dict[str, object]] = dict(tool_manifests or {})
    for tool in environment.get("tools") or []:  # type: ignore[union-attr]
        manifests[str(tool["id"])] = tool  # type: ignore[index]

    # scenario-level semantics need the tool manifests, so they run here rather
    # than in validate_manifest
    scenario_errors: list[str] = []
    for scenario in scenarios:
        try:
            validate_scenario(scenario, manifests)
        except ScenarioValidationError as exc:
            scenario_errors.extend(f"[{scenario.get('name')}] {e}" for e in exc.errors)
    if scenario_errors:
        raise SuiteValidationError(tuple(scenario_errors))

    execution: dict[str, object] = manifest.get("execution") or {}  # type: ignore[assignment]
    evaluation: dict[str, object] = manifest.get("evaluation") or {}  # type: ignore[assignment]
    return ResolvedSuite(
        manifest=manifest,
        scenarios=tuple(scenarios),
        manifests=manifests,
        conditions=tuple(execution.get("conditions") or []),  # type: ignore[arg-type]
        repeats=int(execution.get("repeats", 1)),  # type: ignore[arg-type]
        metrics=tuple(evaluation.get("metrics") or []),  # type: ignore[arg-type]
        aggregations=tuple(evaluation.get("aggregations") or []),  # type: ignore[arg-type]
        evaluators=tuple(evaluation.get("evaluators") or []),  # type: ignore[arg-type]
        regressions=tuple(manifest.get("regressions") or []),  # type: ignore[arg-type]
    )
