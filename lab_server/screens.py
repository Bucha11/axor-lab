"""The screen API — one named endpoint per screen (ui-backend-contract.md §2).

The contract's rule: every screen renders a schema-conforming payload from a
named endpoint, and **rendered, never computed** — a screen displays aggregates,
metrics and verdicts the backend already stored, and never recomputes a rate or
a threshold outcome in the browser. Two implementations of one statistic is how
a published number and a displayed number diverge.

So the payload builders here are thin. They select, label and shape what the
platform already produced; the only arithmetic is counting, and where a number
would have to be derived it is read from `bundle.aggregates` instead.

Everything is stored in memory, like `RuntimeJobStore` beside it. Durable
storage is a swap of this class, not of the endpoints.
"""

from __future__ import annotations

import threading
from typing import Any

from lab_contracts import validate_artifact

SCREEN_KINDS = ("suite", "evidence-case", "regression", "artifact")


class ScreenStoreError(Exception):
    """A screen request that cannot be served, with the HTTP status it maps to."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ScreenStore:
    """Suites, EvidenceCases, Regressions and Artifacts, by id.

    Every write is SCHEMA-VALIDATED before it lands. A store that accepts a
    malformed document serves it back to a screen that cannot render it, and the
    failure surfaces in the browser with no way to tell whether the producer or
    the renderer was wrong.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_kind: dict[str, dict[str, dict[str, Any]]] = {
            kind: {} for kind in SCREEN_KINDS
        }

    def put(self, kind: str, document: dict[str, Any], id_field: str = "id") -> str:
        if kind not in self._by_kind:
            raise ScreenStoreError(400, f"unknown kind {kind!r}")
        errors = validate_artifact(document, kind)
        if errors:
            raise ScreenStoreError(
                422, f"{kind} is not conformant: {'; '.join(errors[:5])}",
            )
        identifier = str(document.get(id_field, ""))
        if not identifier:
            raise ScreenStoreError(422, f"{kind} has no {id_field}")
        with self._lock:
            self._by_kind[kind][identifier] = dict(document)
        return identifier

    def get(self, kind: str, identifier: str) -> dict[str, Any]:
        with self._lock:
            document = self._by_kind.get(kind, {}).get(identifier)
        if document is None:
            raise ScreenStoreError(404, f"no {kind} {identifier!r}")
        return document

    def delete(self, kind: str, identifier: str) -> bool:
        """Remove a stored document. Missing is a no-op (idempotent delete)."""
        with self._lock:
            return self._by_kind.get(kind, {}).pop(identifier, None) is not None

    def list(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(d) for d in self._by_kind.get(kind, {}).values()]

    def ids(self, kind: str) -> list[str]:
        with self._lock:
            return sorted(self._by_kind.get(kind, {}))


# ── payload builders ─────────────────────────────────────────────────────────


def _summary(document: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """A list-view row: the named fields that are present, and nothing else.

    A list endpoint returning whole documents makes the client decide what a row
    is, and two screens then disagree about it.
    """
    return {f: document[f] for f in fields if f in document}


EVIDENCE_ROW = ("id", "kind", "title", "status", "severity", "created", "trial_ref")
REGRESSION_ROW = ("id", "name", "status", "created", "expectation")
# NOT "suite" — that embedded the entire suite/v1 manifest (scenarios,
# fixtures, tool schemas) in every list row for a field the list screen shows
# only as an id. The detail screen carries the full suite.
ARTIFACT_ROW = ("artifact_id", "created")


def home_payload(
    runtimes: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    store: ScreenStore,
) -> dict[str, Any]:
    """The Launchpad. About the NEXT ACTION, not the past (Web UX RFC).

    The onboarding step is derived from what the workspace actually has, in the
    order the workflow needs it. A catalog of finished work is a different
    screen; putting it here is what turned Home into a publication list last
    time.
    """
    if not runs:
        step = "connect_runtime" if not runtimes else "run_a_suite"
    elif not store.ids("evidence-case") and not store.ids("regression"):
        step = "inspect_a_run"
    else:
        step = "pin_a_regression"

    available = [c for c in catalog if c.get("available")]
    return {
        "onboarding_step": step,
        "quick_actions": [
            {"id": "run_suite", "label": "Run a suite", "endpoint": "POST /runs"},
            {"id": "playground", "label": "Try one trial",
             "endpoint": "POST /playground/trial"},
            {"id": "connect_runtime", "label": "Connect a runtime",
             "endpoint": "POST /runtimes/connect"},
        ],
        "suites": available,
        "counts": {
            "runtimes": len(runtimes),
            "runs": len(runs),
            "evidence_cases": len(store.ids("evidence-case")),
            "regressions": len(store.ids("regression")),
            "artifacts": len(store.ids("artifact")),
        },
        # newest last in the store's insertion order; the screen shows the tail
        "recent_runs": runs[-5:],
    }


def run_report(results: dict[str, Any]) -> dict[str, Any]:
    """The Run Report: stored aggregates plus per-metric coverage.

    The only numbers computed here are COUNTS of trials by status and how many
    recorded each metric. Every rate, interval and test comes from
    `bundle.aggregates`, which the runner produced and the artifact publishes —
    recomputing one here would give a screen a second opinion about a published
    result.
    """
    trials = list(results.get("trials", []))
    by_status: dict[str, int] = {}
    for trial in trials:
        status = str(trial.get("status", "unknown"))
        by_status[status] = by_status.get(status, 0) + 1

    completed = [t for t in trials if str(t.get("status")) == "completed"]
    measured: dict[str, int] = {}
    for trial in completed:
        for name in (trial.get("metrics") or {}):
            measured[str(name)] = measured.get(str(name), 0) + 1

    return {
        "run_id": results.get("run_id"),
        "state": results.get("state"),
        "planned_trials": len(results.get("planned_trials", [])),
        "trials_by_status": by_status,
        # denominator honesty, first (statistics.md §5): a report that shows
        # aggregates without saying what fraction of the plan produced them
        # invites reading a partial run as a complete one
        "coverage": {
            "completed": len(completed),
            "planned": len(results.get("planned_trials", [])),
        },
        # per metric: how many COMPLETED trials recorded it. An unmeasured
        # metric is absent from this map, never zero-with-a-value.
        "metric_coverage": measured,
        "aggregates": list(results.get("aggregates", [])),
        # the plan/cost estimate — what the operator confirms before an
        # awaiting_confirmation run starts. It was carried by /results but
        # dropped here, so the Run screen had nothing to base a confirmation on.
        "estimate": dict(results.get("estimate", {})),
    }


def trial_detail(
    results: dict[str, Any], trial_id: str, trace: dict[str, Any] | None,
) -> dict[str, Any]:
    """The Trial screen: the trial record and its trace, joined."""
    trial = next(
        (t for t in results.get("trials", []) if str(t.get("trial_id")) == trial_id),
        None,
    )
    if trial is None:
        raise ScreenStoreError(404, f"no trial {trial_id!r} in run {results.get('run_id')!r}")
    return {
        "run_id": results.get("run_id"),
        "trial": trial,
        # the trace is the authority for what happened; the trial record is the
        # experiment metadata around it
        "trace": trace,
    }


def playground_trial(
    manifest: dict[str, Any],
    suite: Any,
    scenario_name: str | None = None,
    seed: str | None = None,
) -> dict[str, Any]:
    """One trial, executed for inspection (RFC §13).

    NOT a Run: no repeats, no aggregation, no artifact, and nothing is stored.
    lifecycle.md is explicit that a Playground trial must never be counted into
    a Run's aggregates — a debugging session that silently became evidence is
    worse than no debugger.
    """
    from lab_suite import run_suite

    scenarios = list(manifest.get("scenarios") or [])
    if scenario_name:
        scenarios = [s for s in scenarios if str(s.get("name")) == scenario_name]
        if not scenarios:
            raise ScreenStoreError(404, f"no scenario {scenario_name!r} in this suite")
    if not scenarios:
        raise ScreenStoreError(422, "this suite carries no inline scenario to preview")

    execution = dict(manifest.get("execution") or {})
    execution["repeats"] = 1
    if seed:
        execution["seed"] = seed
    # ONE scenario, ONE repeat, and only the first declared arm: a preview that
    # silently ran a whole matrix would bill a debugging click as an experiment.
    conditions = list(execution.get("conditions") or [])
    if conditions:
        execution["conditions"] = conditions[:1]

    # Aggregations and regressions come OFF, and not merely to avoid work.
    # Neither has anything to operate on: an aggregate over one trial is not a
    # rate, and an invariant checked against a single preview would report a
    # pass or a failure about a run that does not exist. Dropping them is also
    # what keeps the preview manifest VALID — a comparison suite declares a
    # `mcnemar` aggregation over two arms, and trimming to one arm without
    # trimming the aggregation produces a manifest the validator correctly
    # refuses ("a comparison needs 2 conditions").
    evaluation = {k: v for k, v in (manifest.get("evaluation") or {}).items()
                  if k != "aggregations"}
    preview = {**manifest, "scenarios": scenarios[:1], "execution": execution,
               "evaluation": evaluation, "regressions": []}

    run = run_suite(preview, run_id="playground", suite=suite)
    trial = run.trials[0] if run.trials else None
    trace = run.traces.get(str((trial or {}).get("trace_ref"))) if trial else None
    return {
        "mode": "playground",
        "trial": trial,
        "trace": trace,
        # said out loud in the payload, not only in the docs: a client that
        # stored this alongside a Run's trials would corrupt the denominator
        "counted_in_a_run": False,
        "evidence_cases": list(run.evidence_cases),
    }


def evidence_list(store: ScreenStore) -> dict[str, Any]:
    return {"evidence_cases": [_summary(c, EVIDENCE_ROW) for c in store.list("evidence-case")]}


def regression_list(store: ScreenStore) -> dict[str, Any]:
    return {"regressions": [_summary(r, REGRESSION_ROW) for r in store.list("regression")]}


def artifact_list(store: ScreenStore) -> dict[str, Any]:
    rows = []
    for artifact in store.list("artifact"):
        row = _summary(artifact, ARTIFACT_ROW)
        suite = artifact.get("suite")
        if isinstance(suite, dict):
            row["suite_id"] = suite.get("id")  # a scalar label, not the manifest
        rows.append(row)
    return {"artifacts": rows}


def run_regression(
    regression: dict[str, Any],
    results: dict[str, Any],
    scenarios: dict[str, dict[str, Any]] | None = None,
    evaluators: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate one stored regression against one run — `POST /regressions/{id}/run`.

    The result is an `InvariantResult` rendered as the regression's `history`
    entry shape, so the screen shows the same object the artifact stores. Note
    it is a CHECK, not a Run: it consumes trials that already exist
    (lifecycle.md), and `error` stays a distinct outcome from `failed`.
    """
    from lab_contracts import content_hash
    from lab_runner.invariants import check_invariant

    # The job store keys trials by their planned UNIT (scenario:condition:index)
    # and holds traces in a flat list. `check_invariant` joins them the way a
    # bundle does — through `trial.trace_ref`, a content hash — so the pairing is
    # reconstructed here from each trace's OWN trial coordinates rather than by
    # position. Zipping two lists that happen to be the same length is how a
    # verdict gets attributed to the wrong trial.
    traces_by_ref: dict[str, dict[str, Any]] = {}
    ref_by_unit: dict[str, str] = {}
    for trace in results.get("traces", []):
        if not trace:
            continue
        coordinates: dict[str, Any] = trace.get("trial") or {}
        unit = (
            f"{coordinates.get('scenario_id')}:{coordinates.get('condition_id')}:"
            f"{coordinates.get('repeat_index')}"
        )
        ref = content_hash(trace)
        traces_by_ref[ref] = trace
        ref_by_unit[unit] = ref

    trials: list[dict[str, Any]] = []
    for trial in results.get("trials", []):
        record = dict(trial)
        unit = str(record.get("trial_id", ""))
        if unit in ref_by_unit:
            record["trace_ref"] = ref_by_unit[unit]
            scenario_id, condition_id, _, *_ = (*unit.split(":"), "", "")
            record.setdefault("scenario_id", scenario_id)
            record.setdefault("condition_id", condition_id)
        trials.append(record)

    result = check_invariant(
        regression, trials, traces_by_ref, scenarios or {}, evaluators or {},
    )
    return {
        "regression_id": result.regression_id,
        "run_id": results.get("run_id"),
        "status": result.status,
        "detail": result.detail,
        "trials_checked": result.trials_checked,
        "trials_failed": result.trials_failed,
        "failing_trial_ids": list(result.failing_trial_ids),
    }
