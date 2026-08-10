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

import json
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

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

    def __init__(self, persist_dir: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._by_kind: dict[str, dict[str, dict[str, Any]]] = {
            kind: {} for kind in SCREEN_KINDS
        }
        # DURABLE storage (open-core plan §4.8 / RFC §16 "hosted workspace"): a
        # workspace that vanishes on restart is a demo, not a product. With a
        # directory, every document is written to disk and reloaded on startup —
        # the swap this class's own docstring anticipated. Without one, the store
        # is in-memory exactly as before (the tests' default, and the offline
        # single-shot CLI's).
        self._root = Path(persist_dir) if persist_dir is not None else None
        if self._root is not None:
            self._load_from_disk()

    # -- persistence ------------------------------------------------------
    def _dir_for(self, kind: str) -> Path:
        assert self._root is not None
        path = self._root / kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _path_for(self, kind: str, identifier: str) -> Path:
        # the id can carry colons and slashes (sha256:..., paths); quote it so it
        # is one safe filename, reversible on load
        return self._dir_for(kind) / f"{quote(identifier, safe='')}.json"

    def _load_from_disk(self) -> None:
        for kind in SCREEN_KINDS:
            directory = self._dir_for(kind)
            for file in directory.glob("*.json"):
                try:
                    document = json.loads(file.read_text())
                except (OSError, ValueError):
                    continue  # a corrupt file is skipped, not fatal on startup
                identifier = unquote(file.stem)
                self._by_kind[kind][identifier] = document

    def _write(self, kind: str, identifier: str, document: dict[str, Any]) -> None:
        if self._root is None:
            return
        target = self._path_for(kind, identifier)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False))
        os.replace(tmp, target)  # atomic: a reader never sees a half-written file

    def _erase(self, kind: str, identifier: str) -> None:
        if self._root is None:
            return
        self._path_for(kind, identifier).unlink(missing_ok=True)

    # -- API --------------------------------------------------------------
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
            self._write(kind, identifier, self._by_kind[kind][identifier])
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
            existed = self._by_kind.get(kind, {}).pop(identifier, None) is not None
            if existed:
                self._erase(kind, identifier)
            return existed

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


def artifact_list(store: ScreenStore, suite_id: str | None = None) -> dict[str, Any]:
    """The artifact registry: newest first, optionally the version history of ONE
    suite (`suite_id`). Ordering by `created` makes a suite's row list its
    successive artifact versions, latest at the top."""
    rows = []
    for artifact in store.list("artifact"):
        suite = artifact.get("suite")
        this_suite = suite.get("id") if isinstance(suite, dict) else None
        if suite_id is not None and this_suite != suite_id:
            continue
        row = _summary(artifact, ARTIFACT_ROW)
        row["suite_id"] = this_suite
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("created", "")), reverse=True)
    return {"artifacts": rows}


def enforce_artifact_retention(store: ScreenStore, keep: int | None) -> list[str]:
    """A registry retains the newest `keep` artifacts; older ones are evicted.

    `keep` is the workspace plan's `max_artifacts` (None = unlimited). Returns the
    ids evicted. This is a REGISTRY's retention, not a hard refusal: a new
    artifact is always stored, and the oldest beyond the plan's window fall off —
    the honest behaviour for a store the customer keeps writing to."""
    if keep is None:
        return []
    artifacts = sorted(
        store.list("artifact"),
        key=lambda a: str(a.get("created", "")), reverse=True,
    )
    evicted: list[str] = []
    for stale in artifacts[keep:]:
        identifier = str(stale.get("artifact_id", ""))
        if identifier and store.delete("artifact", identifier):
            evicted.append(identifier)
    return evicted


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
