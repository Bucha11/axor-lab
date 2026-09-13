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
from lab_contracts.canonical import canonical_json
from lab_contracts.errors import ScenarioValidationError

from .errors import SuiteValidationError

GOVERNANCE_CAPABILITY = "governance"

# aggregations that compare arms; each needs >= 2 conditions to be meaningful
_COMPARISON_TESTS = frozenset({"mcnemar", "two_proportion"})

# the only topology the runner EXECUTES. A manifest may declare any of the
# others (planner_workers, reviewer_pipeline, …) — the schema accepts them and
# the platform is agent-count agnostic by design (RFC §11) — but executing them
# is a later phase. Absence defaults to single.
EXECUTABLE_TOPOLOGIES = frozenset({"single"})


def topology_execution_error(manifest: dict[str, object]) -> str | None:
    """Why a manifest cannot be EXECUTED yet, or None if it can.

    A topology is validated and stored (so a Builder can author one and it round-
    trips), but running a multi-agent topology needs a scheduler that does not
    exist yet. Refusing to run it is the honest behaviour: the alternative —
    silently executing a single scripted agent and labelling the artifact a
    successful planner_workers run — claims a run that never happened. This is an
    EXECUTION gate, not a validation error, so authoring stays unblocked.
    """
    topology: dict[str, object] = manifest.get("topology") or {}  # type: ignore[assignment]
    kind = str(topology.get("kind", "single"))
    if kind in EXECUTABLE_TOPOLOGIES:
        return None
    return (
        f"topology {kind!r} is accepted and validated but not yet executable — "
        "multi-agent execution (planner/workers, reviewer pipelines, "
        "attacker/defender) is a later phase. Only 'single' runs today."
    )


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
    errors.extend(_scripted_agent_errors(agents))
    errors.extend(_suite_config_errors(manifest))
    return errors


def _suite_config_errors(manifest: dict[str, object]) -> list[str]:
    """The Suite SDK's own knobs: `config` against `config_schema` (RFC §12).

    Both keys existed in the schema and nothing read either — `config_schema`
    is described there as "the Builder renders it; a value that fails it is
    rejected at author time, not at run time", and neither half was true. A
    third-party suite could declare knobs the Builder never showed and the
    validator never checked, which makes the Suite protocol a protocol with no
    author-time surface.

    `ui_schema` is presentation only, and the schema says it "can never
    introduce a field config_schema does not define" — enforced here, because a
    layout naming a field nothing defines renders an input that writes a value
    nothing validates.
    """
    from lab_contracts.subset_validator import validate_against

    errors: list[str] = []
    config_schema: dict[str, object] = manifest.get("config_schema") or {}  # type: ignore[assignment]
    config: dict[str, object] = manifest.get("config") or {}  # type: ignore[assignment]
    declared = set(_config_properties(config_schema))

    if config and not config_schema:
        errors.append(
            "[suite] `config` is set but the suite declares no `config_schema` — "
            "values nothing describes cannot be validated or rendered"
        )
    elif config_schema:
        # the platform's own subset validator, on a one-entry schema map: a
        # suite's knobs get exactly the checks every other contract gets, and
        # `jsonschema` stays out of the dependency list
        for problem in validate_against(config, "config", {"config": config_schema}):
            errors.append(f"[suite] {problem}")

    ui_schema: dict[str, object] = manifest.get("ui_schema") or {}  # type: ignore[assignment]
    for section in ui_schema.get("sections") or []:  # type: ignore[union-attr]
        for name in section.get("fields") or []:  # type: ignore[union-attr]
            if str(name) not in declared:
                errors.append(
                    f"[suite] ui_schema section {section.get('id')!r} lays out field "
                    f"{str(name)!r}, which config_schema does not define — a layout "
                    "cannot introduce a field"
                )
    return errors


def _config_properties(config_schema: dict[str, object]) -> list[str]:
    """The knob names a `config_schema` defines, in declaration order."""
    properties = config_schema.get("properties")
    return [str(k) for k in properties] if isinstance(properties, dict) else []


# The agent identity fields `suite.schema.json` allows. They describe the model
# a runtime is expected to run; NOTHING resolves them locally, where the only
# adapter that exists is the deterministic stand-in (`lab_runner.agents`).
_MODEL_IDENTITY = ("provider", "model", "system_prompt", "params")


def _scripted_agent_errors(agents: list[dict[str, object]]) -> list[str]:
    """A stand-in agent may not advertise a model it will never call.

    `scripted@<rate>` is a fixture: behaviour is a hash of (scenario, seed), so
    a `model:` beside it is read by nothing and honoured by nothing — but it is
    rendered in the Builder and carried into the artifact, where it reads as the
    model that produced the numbers. That is a reproducibility claim the run
    cannot support, so it is refused rather than ignored.
    """
    errors: list[str] = []
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get("ref", ""))
        if ref.partition("@")[0] != "scripted":
            continue  # a real agent's identity belongs to the runtime that runs it
        declared = [key for key in _MODEL_IDENTITY if entry.get(key) is not None]
        if declared:
            errors.append(
                f"[suite] agent {ref!r} is the deterministic stand-in but declares "
                f"{', '.join(declared)} — nothing runs that model, and the artifact "
                "would name it as the one that produced the results. Drop the field, "
                "or bind a real agent through a connected runtime"
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
    bundle: dict[str, dict[str, object]] = dict(tool_manifests or {})
    for tool in environment.get("tools") or []:  # type: ignore[union-attr]
        bundle[str(tool["id"])] = tool  # type: ignore[index]

    # A scenario may carry a tool's FULL manifest inline instead of `$ref`-ing
    # a shared one — scenario.schema.json says so ("each is a full manifest (or
    # a $ref to a shared one)") and the Builder's own scenario list writes
    # either. The resolver used to build the bundle from `environment.tools`
    # alone, so an inline manifest was schema-valid and then resolved to "tool
    # 'x' has no manifest in the bundle": the contract said one thing and the
    # code another. It matters most for a REGISTRY scenario, which travels
    # between suites and cannot assume the borrowing suite declares its tools.
    inline: dict[str, dict[str, object]] = {}
    conflicts: list[str] = []
    for scenario in scenarios:
        for tool in scenario.get("tools") or []:  # type: ignore[union-attr]
            if not isinstance(tool, dict) or "$ref" in tool:
                continue
            tool_id = str(tool.get("id", ""))
            # Two different contracts for one tool id inside one run is not a
            # precedence question, it is an ambiguity: the run governs against
            # one of them and the trace's runtime_config_hash names that one,
            # while the manifest reads as if both applied.
            prior = bundle.get(tool_id) or inline.get(tool_id)
            if prior is not None and canonical_json(prior) != canonical_json(tool):
                conflicts.append(
                    f"[{scenario.get('name')}] tool {tool_id!r} is declared inline with a "
                    "different manifest than the one already in the bundle — one tool id "
                    "cannot mean two contracts in one run"
                )
                continue
            inline[tool_id] = tool
    if conflicts:
        raise SuiteValidationError(tuple(conflicts))

    # scenario-level semantics need the tool manifests, so they run here rather
    # than in validate_manifest. Each scenario is checked against the bundle
    # plus ITS OWN inline manifests, not the union: a scenario that `$ref`s a
    # tool only some sibling declared inline would break the moment that
    # sibling is edited out, and it should not validate today.
    scenario_errors: list[str] = []
    for scenario in scenarios:
        own = {
            str(t.get("id", "")): t
            for t in scenario.get("tools") or []  # type: ignore[union-attr]
            if isinstance(t, dict) and "$ref" not in t
        }
        try:
            validate_scenario(scenario, {**bundle, **own})
        except ScenarioValidationError as exc:
            scenario_errors.extend(f"[{scenario.get('name')}] {e}" for e in exc.errors)
    if scenario_errors:
        raise SuiteValidationError(tuple(scenario_errors))

    # ...but the RESOLVED bundle carries every manifest the run will use. It is
    # what a dispatch ships to a runtime, which has no other way to learn what
    # an inline tool's contract is.
    manifests = {**bundle, **inline}

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


def declared_evaluators(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    """The suite's evaluators, keyed by id — what an `evaluator_outcome`
    invariant resolves its `evaluator` name against.

    A suite that declares none gets an empty table, and an invariant naming an
    evaluator then errors with the list it could have named. That beats a
    silent pass and beats a crash.
    """
    evaluation: dict[str, object] = manifest.get("evaluation") or {}  # type: ignore[assignment]
    return {
        str(e["id"]): dict(e)
        for e in evaluation.get("evaluators") or []  # type: ignore[union-attr]
        if isinstance(e, dict) and e.get("id")
    }
