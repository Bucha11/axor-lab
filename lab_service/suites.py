"""Running an experiment suite, and materializing one from a benchmark.

A suite run has the same shape as an `.axl` run — plan, a person agrees, execute
— and the same reason for splitting there: the confirmation gate belongs to the
face, not to the work.

What differs is the deliverable. A suite run produces an ARTIFACT (RFC §14), and
the artifact is the thing a reader gets, so an invalid one is a failure at write
time rather than a discovery hours later at publish time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .outcomes import Outcome

DEFAULT_KERNEL = "axor-core@0.4.2"
STRICT_POLICY: dict[str, object] = {"profile": "strict", "trust_model": "content-ledger"}


@dataclass(frozen=True)
class SuitePlan:
    """A validated suite manifest and the work it implies."""

    outcome: Outcome
    manifest: dict[str, object]
    suite: object
    run_id: str
    scenarios: int = 0
    conditions: int = 0
    repeats: int = 1
    trials: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def suite_id(self) -> str:
        return str(self.manifest.get("id", ""))

    @property
    def single_arm(self) -> bool:
        """A suite with NO conditions is single-arm — one trial per (scenario,
        repeat). That is the platform's default path, not a degenerate case."""
        return self.conditions == 0


@dataclass(frozen=True)
class SuiteRunResult:
    """A finished suite run and the artifact it produced.

    Violated and unevaluable invariants stay separate: an invariant that could
    not be evaluated is not a pass (an unmeasured latency is not a fast one,
    lifecycle.md) but it is a different thing to tell CI than "your change
    regressed".
    """

    outcome: Outcome
    directory: Path
    artifact: dict[str, object]
    planned: int
    by_status: dict[str, int]
    missingness: str
    aggregates: tuple[dict[str, object], ...] = field(default_factory=tuple)
    invariants: tuple[object, ...] = field(default_factory=tuple)
    violated: tuple[object, ...] = field(default_factory=tuple)
    unevaluable: tuple[object, ...] = field(default_factory=tuple)
    trace_count: int = 0
    schema_errors: tuple[str, ...] = field(default_factory=tuple)


def resolve_suite_target(target: str) -> tuple[dict[str, object], object]:
    """Resolve `target` — a registered suite id or a manifest path — to
    (manifest, suite implementation).

    A manifest file may name a suite the registry knows; then that suite's hooks
    run over the file's manifest, which is exactly the Builder's edit-and-run
    loop. A manifest whose `id` is unregistered runs on `BaseSuite` defaults — no
    program, no metrics, no extractors — rather than being refused, because a
    manifest is authorable long before an implementation exists.
    """
    from lab_suite import builtin_registry, load_manifest
    from lab_suite.errors import SuiteNotFound
    from lab_suite.sdk import BaseSuite

    registry = builtin_registry()
    path = Path(target)
    if path.exists():
        manifest = load_manifest(path)
        try:
            return manifest, registry.get(str(manifest.get("id", "")))
        except SuiteNotFound:
            unimplemented = BaseSuite()
            unimplemented.id = str(manifest.get("id", ""))
            unimplemented.manifest = lambda: manifest  # type: ignore[method-assign]
            return manifest, unimplemented
    suite = registry.get(target)
    return suite.manifest(), suite


def plan_suite_run(target: str, *, run_id: str | None = None) -> SuitePlan:
    """Resolve and validate a suite; nothing executes. Validation problems come
    back on the plan rather than as an exception — they are the answer."""
    from lab_contracts import content_hash
    from lab_suite import validate_manifest

    manifest, suite = resolve_suite_target(target)
    errors = validate_manifest(manifest) + suite.validate(manifest)  # type: ignore[attr-defined]
    resolved_run_id = run_id or f"r_{content_hash(manifest)[7:39]}"
    if errors:
        return SuitePlan(
            outcome=Outcome.VALIDATION, manifest=manifest, suite=suite,
            run_id=resolved_run_id, errors=tuple(str(e) for e in errors),
        )
    execution: dict[str, object] = manifest.get("execution") or {}  # type: ignore[assignment]
    scenarios = list(manifest.get("scenarios") or []) or list(manifest.get("scenario_refs") or [])
    conditions = list(execution.get("conditions") or [])
    repeats = int(execution.get("repeats", 1))  # type: ignore[arg-type]
    return SuitePlan(
        outcome=Outcome.OK, manifest=manifest, suite=suite, run_id=resolved_run_id,
        scenarios=len(scenarios), conditions=len(conditions), repeats=repeats,
        trials=len(scenarios) * repeats * max(len(conditions), 1),
    )


def execute_suite_run(
    plan: SuitePlan,
    *,
    out: Path,
    created: str | None = None,
    overwrite: bool = False,
    command: str = "",
) -> SuiteRunResult:
    """Execute a planned suite run and write its artifact + bundle."""
    import json

    from lab_analysis import missingness
    from lab_contracts import validate_artifact
    from lab_runner.bundle_io import write_bundle_dir
    from lab_runner.invariants import STATUS_ERROR, STATUS_FAILED
    from lab_suite import run_suite

    run = run_suite(plan.manifest, run_id=plan.run_id, suite=plan.suite)  # type: ignore[arg-type]
    by_status: dict[str, int] = {}
    for trial in run.trials:
        by_status[str(trial["status"])] = by_status.get(str(trial["status"]), 0) + 1
    artifact = run.artifact(
        artifact_id=f"a_{plan.run_id}",
        created=created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        environment={
            "model": {"provider": "scripted", "id": plan.suite_id,
                      "inference_params": {"suite_id": plan.suite_id}},
        },
        command=command,
    )
    directory = Path(out)
    schema_errors = validate_artifact(artifact, "artifact")
    if schema_errors:
        # the artifact IS the deliverable; writing an invalid one and finding out
        # at publish time is how a run becomes unusable hours later
        return SuiteRunResult(
            outcome=Outcome.FAILURE, directory=directory, artifact=artifact,
            planned=len(run.trials), by_status=by_status,
            missingness=missingness(run.trials).display(),
            schema_errors=tuple(str(e) for e in schema_errors),
        )
    write_bundle_dir(
        directory, artifact["bundle"], run.traces,  # type: ignore[arg-type]
        overwrite=overwrite,
    )
    (directory / "artifact.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False)
    )
    # `skipped` IS fine: the rule kind executes elsewhere, and failing on it
    # would fail every governed suite for carrying a verdict_sequence pin.
    violated = tuple(r for r in run.invariants if r.status == STATUS_FAILED)
    unevaluable = tuple(r for r in run.invariants if r.status == STATUS_ERROR)
    if violated:
        outcome = Outcome.REGRESSION_DIFFERS
    elif unevaluable:
        outcome = Outcome.FAILURE
    else:
        outcome = Outcome.OK
    return SuiteRunResult(
        outcome=outcome, directory=directory, artifact=artifact,
        planned=len(run.trials), by_status=by_status,
        missingness=missingness(run.trials).display(),
        aggregates=tuple(run.aggregates), invariants=tuple(run.invariants),
        violated=violated, unevaluable=unevaluable, trace_count=len(run.traces),
    )


@dataclass(frozen=True)
class BenchmarkImportResult:
    """A benchmark materialized as a runnable experiment document."""

    outcome: Outcome
    document: dict[str, object]
    scenarios: int


def build_agentdojo_experiment(
    suite: str, *, repeats: int = 1, agent_ref: str = "scripted"
) -> BenchmarkImportResult:
    """Materialize a curated AgentDojo suite as an ungoverned/governed compare.

    Raises `UnknownSuiteError` for a suite that is not curated, and whatever
    `resolve` raises for a materialized document that cannot resolve — that is a
    bug in the adapter, and it should fail loudly rather than write a file
    nobody can run.
    """
    from lab_adapters import build_experiment_document
    from lab_capabilities.governance import resolve
    from lab_contracts import condition_config_hash

    conditions = [
        {
            "schema_version": "condition/v1",
            "id": "ungoverned",
            "label": "ungoverned",
            "enforcement": "off",
            "kernel": DEFAULT_KERNEL,
            "config_hash": condition_config_hash(DEFAULT_KERNEL, None),
        },
        {
            "schema_version": "condition/v1",
            "id": "governed",
            "label": "governed",
            "enforcement": "on",
            "kernel": DEFAULT_KERNEL,
            "policy": dict(STRICT_POLICY),
            "config_hash": condition_config_hash(DEFAULT_KERNEL, dict(STRICT_POLICY)),
        },
    ]
    document = build_experiment_document(
        suite, conditions, repeats=repeats, agent_ref=agent_ref
    )
    # a materialized suite that cannot resolve is a bug — fail loudly, not silently
    resolve(document)
    return BenchmarkImportResult(
        outcome=Outcome.OK,
        document=document,
        scenarios=len(document["scenarios"]),  # type: ignore[arg-type]
    )
