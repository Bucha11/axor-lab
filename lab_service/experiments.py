"""Validating, planning, running and replaying an experiment.

`run` is not one call and never was: it validates, shows what it is about to do,
WAITS for a person to agree, and only then executes. The CLI expressed that as
prompt-in-the-middle, which is exactly why the verb could not be driven from
anywhere else — the hosted face already models the same gate as
`POST /runs/{id}/confirm`, but against a separate implementation.

So the verb is two: `plan_run` resolves and prices the work, `execute_run`
performs it. The gate belongs to the face — a terminal prompt, an HTTP confirm —
and neither face owns the work on either side of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .outcomes import Outcome

if TYPE_CHECKING:
    from lab_capabilities.governance.experiment_file import ResolvedExperiment

METRIC_ASR = "ASR"
METRIC_UTILITY = "utility"
DESIGN_MATCHED = "matched_pairs"
DESIGN_INDEPENDENT = "independent_samples"
_RUN_ID_HEX = 32  # 128-bit; a 32-bit slice was birthday-collision-searchable (r7)


@dataclass(frozen=True)
class ValidationResult:
    """What a valid experiment says it will do."""

    outcome: Outcome
    experiment_id: str
    scenarios: int
    conditions: int
    repeats: int
    trial_count: int


@dataclass(frozen=True)
class RunPlan:
    """A resolved experiment and the work it implies, before anything executes."""

    resolved: ResolvedExperiment
    run_id: str
    agent: object
    repinned_kernel: str | None = None

    @property
    def agent_ref(self) -> str:
        return str(self.resolved.experiment["agent_ref"])

    @property
    def trial_count(self) -> int:
        return int(self.resolved.trial_count)


@dataclass(frozen=True)
class RunResult:
    """A finished run: what it produced and what it failed to produce.

    Planned / completed / failed / excluded are reported separately because
    "N trials completed" over every trial record was misleading — the record set
    also holds failed and cost-excluded trials (review r14).
    """

    outcome: Outcome
    directory: Path
    bundle_id: str
    run_id: str
    planned: int
    completed: int
    failed: int
    excluded: int
    missingness: str
    aggregates: tuple[dict[str, object], ...] = field(default_factory=tuple)
    trace_count: int = 0
    superseded_log: Path | None = None
    superseded_count: int = 0


def validate_experiment(document: dict[str, object]) -> ValidationResult:
    """Resolve an `.axl` document; raises `ExperimentFileError` when it does not."""
    from lab_capabilities.governance import resolve

    resolved = resolve(document)
    return ValidationResult(
        outcome=Outcome.OK,
        experiment_id=str(resolved.experiment["id"]),
        scenarios=len(resolved.scenarios),
        conditions=len(resolved.conditions),
        repeats=int(resolved.repeats),
        trial_count=int(resolved.trial_count),
    )


def repin_to_real_kernel(document: dict[str, object]) -> str:
    """Repin EVERY condition — baseline included — to the installed axor-core
    version, and return it, so the run governs with the real kernel.

    Repinning only the enforcement-on conditions left the baseline on the
    reference kernel, so the compare no longer isolated enforcement: it mixed an
    enforcement change WITH a kernel change. It also produced a bundle with two
    distinct condition kernels, so the environment carried a comma-joined
    `kernel_version` that `verify_bundle` rejects — meaning the command ran every
    trial (paid model calls included) and only THEN failed at save (review r13).

    Repinning the baseline is load-bearing, not cosmetic. `enforcement: off` is
    observe-only, not gate-free: the kernel evaluates every call and records the
    verdict it would have enforced. So the baseline's verdicts are the REAL
    kernel's verdicts, and a baseline left on the reference kernel would report
    a different kernel's opinion of the same calls.
    """
    from lab_capabilities.governance import axor_available, real_kernel_version
    from lab_contracts import condition_config_hash
    from lab_runner.errors import RunnerError

    if not axor_available():
        raise RunnerError("--real-kernel requested but axor-core is not installed")
    version = real_kernel_version()
    experiment: dict[str, object] = document["experiment"]  # type: ignore[assignment]
    for condition in experiment.get("conditions", []):  # type: ignore[union-attr]
        condition["kernel"] = version
        condition["config_hash"] = condition_config_hash(version, condition.get("policy"))
    return version


def agent_is_deterministic(agent: object) -> bool:
    return bool(getattr(agent, "is_deterministic", False))


def derive_run_id(
    explicit: str | None,
    experiment: dict[str, object],
    fingerprint: str,
    *,
    deterministic: bool,
) -> str:
    """The run id. An explicit id always wins. A DETERMINISTIC agent (scripted /
    replayed cassette) yields a content-derived id, so re-running the same
    experiment reproduces the same identity. A NONDETERMINISTIC agent (a live
    model) draws a fresh sample each execution, so two runs are DIFFERENT
    executions, not retries of one — a random execution nonce is folded in so
    their run/trial/trace ids differ (review r13)."""
    from lab_contracts import content_hash

    if explicit:
        return explicit
    body: dict[str, object] = {"experiment": experiment, "agent": fingerprint}
    if not deterministic:
        import secrets

        body["execution_nonce"] = secrets.token_hex(16)  # 128-bit per-execution
    return "r_" + content_hash(body).removeprefix("sha256:")[:_RUN_ID_HEX]


def effective_design(resolved: ResolvedExperiment, agent: object) -> str:
    """paired (McNemar) only when the agent's behavior is fixed by scenario+seed.

    A live model draws each condition independently — the 'pairs' are nominal, so
    McNemar's paired test is invalid and the comparison is independent samples. A
    declared matched_pairs design is rejected for a non-deterministic agent
    rather than silently producing a spurious paired p-value (review r4)."""
    from lab_runner.errors import RunnerError

    declared = None
    design_obj = resolved.experiment.get("comparison_design")  # type: ignore[union-attr]
    if isinstance(design_obj, dict):
        declared = design_obj.get("kind")
    deterministic = agent_is_deterministic(agent)
    if declared == DESIGN_MATCHED:
        if not deterministic:
            raise RunnerError(
                "comparison_design=matched_pairs requires a deterministic agent; a live "
                "model is sampled independently per condition — use independent_samples"
            )
        return DESIGN_MATCHED
    if declared == DESIGN_INDEPENDENT:
        return DESIGN_INDEPENDENT
    return DESIGN_MATCHED if deterministic else DESIGN_INDEPENDENT


def condition_counts(result: object, condition_id: str) -> tuple[int, int, int]:
    # only COMPLETED trials that actually produced an outcome (a failed trial has
    # none) — accessing result.outcomes[...] for a failed trial used to KeyError
    trials = [
        t for t in result.trials  # type: ignore[attr-defined]
        if t["condition_id"] == condition_id and str(t["trial_id"]) in result.outcomes  # type: ignore[attr-defined]
    ]
    outcomes = [result.outcomes[str(t["trial_id"])] for t in trials]  # type: ignore[attr-defined]
    return (
        len(outcomes),
        sum(1 for o in outcomes if o.violation),
        sum(1 for o in outcomes if o.task_success),
    )


def compute_aggregates(
    resolved: ResolvedExperiment, result: object, agent: object
) -> list[dict[str, object]]:
    """Every aggregate the run supports, with the test its DESIGN allows."""
    from lab_analysis import binary_aggregate, mcnemar_test, two_proportion_test

    design = effective_design(resolved, agent)
    aggregates: list[dict[str, object]] = []
    baseline = next(
        (str(c["id"]) for c in resolved.conditions if c["enforcement"] == "off"), None
    )
    counts = {
        str(c["id"]): condition_counts(result, str(c["id"])) for c in resolved.conditions
    }
    for condition in resolved.conditions:
        condition_id = str(condition["id"])
        n, asr_succ, util_succ = counts[condition_id]
        if n == 0:
            # a condition where every trial failed produces NO aggregate rather
            # than crashing wilson_interval; missingness reports the gap (r7)
            continue
        for metric, successes in ((METRIC_ASR, asr_succ), (METRIC_UTILITY, util_succ)):
            test = None
            is_treated = baseline is not None and condition_id != baseline and metric == METRIC_ASR
            base_n = counts[baseline][0] if baseline is not None else 0  # type: ignore[index]
            if is_treated and base_n > 0 and design == DESIGN_MATCHED:
                pairs = result.pairs(baseline, condition_id, metric="ASR")  # type: ignore[attr-defined]
                test = mcnemar_test(pairs, vs=baseline)
            elif is_treated and base_n > 0 and design == DESIGN_INDEPENDENT:
                base_asr = counts[baseline][1]  # type: ignore[index]
                test = two_proportion_test(base_asr, base_n, successes, n, vs=baseline)
            aggregates.append(
                binary_aggregate(metric, condition_id, successes, n, test=test,
                                 comparison_design=design)
            )
    return aggregates


def build_environment(
    resolved: ResolvedExperiment,
    model: str | None = None,
    usage: dict[str, object] | None = None,
    agent: object | None = None,
) -> dict[str, object]:
    """Record the ACTUAL agent that ran (review §6.1). The bundle stays
    self-describing: kernel, the agent id, and (when imported) the dataset
    version."""
    kernels = sorted({str(c["kernel"]) for c in resolved.conditions})
    model = model or str(resolved.experiment["agent_ref"])
    provider = model.split(":", 1)[0] if ":" in model else (
        "scripted" if model.startswith("scripted") else "unknown"
    )
    inference_params: dict[str, object] = {"experiment_id": str(resolved.experiment["id"])}
    if usage is not None:
        inference_params["usage"] = usage
    env: dict[str, object] = {
        "model": {"provider": provider, "id": model, "inference_params": inference_params},
    }
    # the FIRST-CLASS comparison design, recorded at run time and bound to the
    # ACTUAL agent's determinism — this, not the uploader-controlled aggregate, is
    # what the CP bridge reads to choose matched_pairs vs independent_samples
    # (review r21). effective_design already refuses matched_pairs for a live agent.
    if agent is not None:
        design = effective_design(resolved, agent)
        env["experiment_design"] = {
            "schema_version": "comparison-design/v1",
            "kind": design,
            "unit_key": ["execution_id", "scenario_id", "condition_id", "seed", "repeat_index"],
            "assignment": "shared_deterministic_agent_state" if design == DESIGN_MATCHED
                          else "independent_per_condition",
            "agent_deterministic": agent_is_deterministic(agent),
        }
    # the global kernel_version is a convenience that only makes sense when every
    # condition shares one kernel — verify_bundle requires it to equal a condition
    # kernel. Emitting a comma-joined pseudo-value for a mixed-kernel bundle would
    # fail that check AFTER every (paid) trial ran; omit it instead (each trace's
    # producer.kernel_version, bound to its own condition, stays authoritative).
    if len(kernels) == 1:
        env["kernel_version"] = kernels[0]
    else:
        # a mixed-kernel run omits the single global kernel_version (now optional
        # in the schema) and records the distinct kernels explicitly, so the
        # bundle is schema-VALID and readable rather than a write-now/read-never
        # artifact (review r15).
        env["kernel_versions"] = kernels
    return env


def plan_run(
    document: dict[str, object],
    *,
    real_kernel: bool = False,
    run_id: str | None = None,
) -> RunPlan:
    """Resolve an experiment and settle its identity — nothing executes here."""
    from lab_capabilities.governance import resolve

    repinned = repin_to_real_kernel(document) if real_kernel else None
    resolved = resolve(document)
    agent = resolved.agent
    # run identity folds in the agent, so two runs of the same experiment under
    # different agents are different runs rather than retries of one (review r3).
    fingerprint = str(resolved.experiment["agent_ref"])
    return RunPlan(
        resolved=resolved,
        run_id=derive_run_id(
            run_id, resolved.experiment, fingerprint,
            deterministic=bool(getattr(agent, "is_deterministic", True)),
        ),
        agent=agent,
        repinned_kernel=repinned,
    )


def execute_run(
    plan: RunPlan,
    *,
    out: Path,
    created: str | None = None,
    overwrite: bool = False,
) -> RunResult:
    """Execute a planned run, analyze it, and write the bundle directory."""
    from lab_analysis import missingness
    from lab_capabilities.governance import run_experiment_suite
    from lab_contracts import build_bundle
    from lab_runner.bundle_io import PACKAGING, write_bundle_dir, write_superseded_attempts

    resolved = plan.resolved
    result = run_experiment_suite(
        list(resolved.scenarios),
        resolved.manifests,
        list(resolved.conditions),
        resolved.kernel_registry,
        repeats=resolved.repeats,
        run_id=plan.run_id,
        agent=plan.agent,
    )
    by_status: dict[str, int] = {}
    for trial in result.trials:
        by_status[str(trial["status"])] = by_status.get(str(trial["status"]), 0) + 1
    # missingness FIRST (denominator honesty) — it must be reported even if a
    # whole condition has no completed trials, so it never depends on aggregates
    summary = missingness(result.trials)
    aggregates = compute_aggregates(resolved, result, plan.agent)
    bundle_id = f"b_{plan.run_id}"
    bundle = build_bundle(
        bundle_id=bundle_id,
        created=created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        scenarios=list(resolved.scenarios),
        conditions=list(resolved.conditions),
        tool_manifests=list(resolved.manifests.values()),
        # no paid inference: the agent is scripted, so there is no spend to record
        environment=build_environment(resolved, usage=None, agent=plan.agent),
        trials=result.trials,
        aggregates=aggregates,
        traces=result.traces,
        packaging=dict(PACKAGING),
    )
    directory = Path(out)
    write_bundle_dir(directory, bundle, result.traces, overwrite=overwrite)
    # superseded retry attempts are NOT publishable evidence (they would orphan
    # the bundle graph), but they ARE the audit trail — persist them beside the
    # bundle so "both attempts are preserved" holds on disk, not only in the
    # in-memory result (review r9)
    attempt_log = write_superseded_attempts(directory, result.superseded)
    return RunResult(
        outcome=Outcome.OK,
        directory=directory,
        bundle_id=bundle_id,
        run_id=plan.run_id,
        planned=len(result.trials),
        completed=by_status.get("completed", 0),
        failed=by_status.get("failed", 0),
        excluded=by_status.get("excluded", 0),
        missingness=summary.display(),
        aggregates=tuple(aggregates),
        trace_count=len(result.traces),
        superseded_log=attempt_log,
        superseded_count=len(result.superseded),
    )


@dataclass(frozen=True)
class ReplayResult:
    """Exact decision replay over frozen traces — verdicts only.

    Replay reproduces the VERDICTS, never the counterfactual continuation
    (claims.md). `bit_identical` is a claim about the verdict core, not a
    statistical one: behavioural outcomes reproduce through `run`, not here.
    """

    outcome: Outcome
    traces: int
    decisions: int
    denies: int
    allows: int
    bit_identical: bool


def replay_source(path: Path) -> ReplayResult:
    """Replay a bundle DIRECTORY or a downloaded `.json` reproduction package, so
    a reader can replay exactly what a publication page served (review r13)."""
    from lab_capabilities.governance import default_registry, replay_bundle
    from lab_runner.bundle_io import read_bundle_source

    bundle, traces = read_bundle_source(Path(path))
    conditions: list[dict[str, object]] = bundle["conditions"]  # type: ignore[assignment]
    versions = tuple(str(c["kernel"]) for c in conditions)
    kernels = {k.version: k for k in default_registry(versions).kernels}
    report = replay_bundle(bundle, traces, kernels)
    verdicts = [v for vs in report.verdicts().values() for v in vs]
    return ReplayResult(
        outcome=Outcome.OK if report.bit_identical else Outcome.FAILURE,
        traces=len(traces),
        decisions=len(report.decisions),
        denies=sum(1 for v in verdicts if v == "DENY"),
        allows=sum(1 for v in verdicts if v == "ALLOW"),
        bit_identical=bool(report.bit_identical),
    )
