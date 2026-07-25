"""Run an experiment in the server process — the UI's path to a first result.

Lab's run surface was built around a CONNECTED runtime: the browser plans a run,
a runtime claims it, streams trials back. That is the right shape for someone
else's agent, but it made the first result unreachable from the UI — you had to
install the CLI, run a bundle, start a second server and publish, before the
catalog showed anything at all.

The bundled example needs none of that. `scripted@0.6` is a deterministic
stand-in for the model layer, the reference kernel is stdlib, and the tools are
simulated, so the whole experiment is executable right here with no agent, no
provider, no API key and no network.

Two boundaries this module holds, because a browser is on the other end:

* **Offline only.** A non-deterministic (model-backed) agent is refused. A web
  request must never be able to make the server spend money on provider calls —
  that path stays with the operator's own CLI, where the cost ceilings live.
* **Bounded.** The planned trial count is capped, so a hand-edited request
  cannot ask the server for an unbounded run.

What comes out is the runner's OWN bundle. That matters for evidence, not just
convenience: assembling a bundle from collected traces has to RECONSTRUCT each
trial's `runtime_config_hash` and marks it `reconstructed_legacy`, which the
evidence export refuses. A run executed here records the hash at execution, so
the bundle is publishable evidence on the same footing as a CLI run.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The ceiling on a UI-triggered run. The bundled example plans 2 conditions × 1
# scenario × its own repeats; a suite of a few hundred trials still finishes in
# seconds under the scripted agent. This bounds a hand-edited request, not real
# research — that runs from the CLI.
MAX_LOCAL_TRIALS = 400


class LocalRunRefused(ValueError):
    """The experiment cannot be run in-process. Carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def example_path() -> Path:
    """The bundled example experiment, resolved from the installed package."""
    return Path(__file__).resolve().parent.parent / "examples" / "banking-exfil-01.axl"


def load_example() -> dict[str, Any]:
    from lab_runner.experiment_file import load_axl

    path = example_path()
    if not path.exists():
        raise LocalRunRefused(500, f"the bundled example is missing at {path}")
    return load_axl(path)


def run_local(document: dict[str, Any], created: str | None = None) -> dict[str, Any]:
    """Execute `document` in-process and return the runner's own bundle.

    `created` pins the bundle's creation timestamp, mirroring the CLI's
    `--created`. It is the only wall-clock field in a bundle, and it feeds the
    content hashes — so pinning it is what lets two runs of a deterministic
    experiment be compared byte for byte. Left unset it is now().

    Returns `{bundle, traces, aggregates, missingness, by_status, stopped_reason}`.
    Raises LocalRunRefused for anything this surface will not run.
    """
    from lab_analysis import missingness
    from lab_analysis.errors import AnalysisError
    from lab_contracts import build_bundle
    from lab_runner.bundle_io import PACKAGING
    from lab_runner.cli import _aggregates, _derive_run_id, _environment
    from lab_runner.errors import ExperimentFileError, RunnerError
    from lab_runner.experiment_file import resolve
    from lab_runner.runner import run_experiment_suite

    try:
        resolved = resolve(document)
    except ExperimentFileError as exc:
        raise LocalRunRefused(
            422, "not a runnable experiment: " + "; ".join(exc.errors)
        ) from exc

    agent = resolved.agent
    if not bool(getattr(agent, "is_deterministic", False)):
        # The refusal is the point, not a limitation to work around: a model-backed
        # run costs money, and nothing in an HTTP request can be trusted to bound
        # that. The CLI owns it, with --max-usd and the estimate-confirm gate.
        raise LocalRunRefused(
            409,
            "this run needs a live model — run it from the CLI (axor-lab run), "
            "where the cost ceiling and the estimate-confirm gate apply, or "
            "connect a runtime and let it execute the trials",
        )

    planned = len(resolved.scenarios) * len(resolved.conditions) * max(resolved.repeats, 1)
    if planned > MAX_LOCAL_TRIALS:
        raise LocalRunRefused(
            413,
            f"{planned} planned trials exceeds the in-server ceiling of "
            f"{MAX_LOCAL_TRIALS}; run this from the CLI or split the suite",
        )

    model = str(resolved.experiment.get("agent_ref", "") or "scripted")
    run_id = _derive_run_id(None, resolved.experiment, model, deterministic=True)

    try:
        result = run_experiment_suite(
            list(resolved.scenarios),
            resolved.manifests,
            list(resolved.conditions),
            resolved.kernel_registry,
            repeats=resolved.repeats,
            run_id=run_id,
            agent=agent,
        )
    except RunnerError as exc:
        raise LocalRunRefused(422, f"the run failed: {exc}") from exc

    by_status: dict[str, int] = {}
    for trial in result.trials:
        key = str(trial["status"])
        by_status[key] = by_status.get(key, 0) + 1

    # Missingness first, denominator honesty before any aggregate — the same order
    # the CLI reports in, for the same reason.
    summary = missingness(result.trials)
    try:
        aggregates = _aggregates(resolved, result, agent)
        # e.g. a matched_pairs design declared over a non-deterministic agent —
        # the runner's own error, surfaced as a clean 422 rather than a 500.
        environment = _environment(resolved, model, agent=agent)
    except (RunnerError, AnalysisError) as exc:
        raise LocalRunRefused(422, f"cannot summarise the run: {exc}") from exc

    bundle = build_bundle(
        bundle_id=f"b_{run_id}",
        created=created or datetime.now(UTC).isoformat(timespec="seconds"),
        scenarios=list(resolved.scenarios),
        conditions=list(resolved.conditions),
        tool_manifests=list(resolved.manifests.values()),
        environment=environment,
        trials=result.trials,
        aggregates=aggregates,
        traces=result.traces,
        packaging=dict(PACKAGING),
    )
    return {
        "run_id": run_id,
        "bundle": bundle,
        # keyed by trace ref, the shape build_bundle and publish both expect
        "traces": dict(result.traces),
        "aggregates": aggregates,
        "missingness": summary.display(),
        "by_status": by_status,
        "stopped_reason": result.stopped_reason,
    }
