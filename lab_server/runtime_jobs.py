"""Runtime-jobs API — the connected-runtime execution contract (spec v0.3).

architecture-boundary.md: **Lab assigns, the runtime executes.** Lab never
connects to, executes, or proxies an agent. A user connects an Axor runtime
adapter ONCE (the same one that serves Control Plane); it pulls experiment
assignments, runs them locally, and pushes back kernel events + finished traces.

This is the deliberately SIMPLE first implementation of that contract: a single
process, in-memory job store, stdlib `http.server`. It establishes the surface and
the state machine so a connected runtime can drive a run end-to-end; durability,
per-tenant scoping, SSE streaming and bundle assembly are left as extension points
(the store returns the collected trials/traces; assembly stays the runner's job).

Control surface (Lab operator / UI):

  POST /runtimes/connect     register a runtime  -> { runtime_ref, ingest_key }
  GET  /runtimes             list connected runtimes
  POST /scenarios/validate   validate a scenario -> { ok, errors[] }
  POST /experiments/plan     expand an experiment -> { trials, estimate }
  POST /runs                 assign an experiment to a runtime -> { run_id, state }
  POST /runs/{id}/confirm    confirm an awaiting_confirmation run -> { state }
  POST /runs/{id}/aggregates attach bundle.aggregates + finalize -> { state }
  GET  /runs/{id}            -> { state }  (a lifecycle state)
  GET  /runs/{id}/events     -> text/event-stream (state + trial progress)
  GET  /runs/{id}/results    -> { trials, traces, aggregates }  (collected so far)
  GET  /runs/{id}/trials/{trial_id}/trace  -> the completed trial's trace

Runtime-facing (Bearer <ingest_key>; the runtime pulls and pushes):

  GET  /runtime/jobs                                    poll for assignments
  POST /runtime/jobs/{id}/claim                          claim one -> the assignment
  POST /runtime/jobs/{id}/trials/{trial_id}/events       stream kernel events
  POST /runtime/jobs/{id}/trials/{trial_id}/complete     finalize the trial (uploads its trace)

The connected_runtime lifecycle (ui-backend-contract.md):
  validating -> waiting_for_runtime -> running -> receiving_traces -> analyzing -> completed
"""

from __future__ import annotations

import json
import pathlib
import queue
import re
import secrets
import threading
import time
from urllib.parse import unquote
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lab_server.static import (
    content_type as static_content_type,
    default_root as static_default_root,
    is_app_route,
    resolve as static_resolve,
)
from lab_server.screens import (
    ScreenStore,
    ScreenStoreError,
    artifact_list,
    evidence_list,
    home_payload,
    playground_trial,
    regression_list,
    run_regression,
    run_report,
    trial_detail,
)

_MAX_BODY = 8 * 1024 * 1024

_RUNTIME_JOBS_RE = re.compile(r"^/runtime/jobs$")
_CLAIM_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/claim$")
_EVENTS_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/events$")
_TRIAL_DONE_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/complete$")
_RUN_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)$")
_RUN_RESULTS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/results$")
_RUN_EVENTS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/events$")
_RUN_CONFIRM_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/confirm$")
_RUN_CANCEL_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/cancel$")
_RUN_AGG_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/aggregates$")
_RUN_TRACE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/trace$")
# `validate` is a POST sibling, not a suite id — without the lookahead a GET to
# /suites/validate answers `no suite 'validate'`, which reads like the endpoint
# is missing rather than like the method is wrong.
_SUITE_RE = re.compile(r"^/suites/(?!validate$)([A-Za-z0-9_-]+)$")
_SUITE_YAML_RE = re.compile(r"^/suites/([A-Za-z0-9_-]+)/yaml$")
_SUITE_DISPATCH_RE = re.compile(r"^/suites/([A-Za-z0-9_-]+)/dispatch$")
_RUN_REPORT_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/report$")
_RUN_TRIAL_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)$")
_EVIDENCE_RE = re.compile(r"^/evidence/([A-Za-z0-9_.:-]+)$")
_REGRESSION_RE = re.compile(r"^/regressions/([A-Za-z0-9_.:-]+)$")
_REGRESSION_RUN_RE = re.compile(r"^/regressions/([A-Za-z0-9_.:-]+)/run$")
_ARTIFACT_RE = re.compile(r"^/artifacts/([A-Za-z0-9_.:-]+)$")


class RuntimeJobsError(Exception):
    """A bad runtime-jobs request; carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# Metrics that are Lab's VERDICT about a trial, not the runtime's measurement.
# The runtime may report duration/tokens/cost — things only it can observe — but
# task_success/ASR are recomputed by Lab from the trace (collect_suite_run), and
# a runtime that could set them on the raw store would grade its own homework on
# every UI surface that reads the store before a bundle is built.
RESERVED_METRICS = frozenset({"task_success", "ASR"})


def _trace_errors(trace: object) -> list[str]:
    """Schema errors for a runtime-pushed trace, or ['not a trace'] if absent.

    The runtime is untrusted; a trace is validated at the moment it arrives, the
    same check collect_suite_run runs, so poison never reaches `completed`."""
    if not isinstance(trace, dict):
        return ["no trace body"]
    from lab_contracts import validate_artifact

    return validate_artifact(trace, "trace")


def _sanitize_metrics(metrics: dict[str, object]) -> dict[str, object]:
    """Keep scalar runtime measurements; drop Lab-owned verdict names."""
    return {
        str(k): v for k, v in metrics.items()
        if isinstance(v, (int, float, str, bool)) and str(k) not in RESERVED_METRICS
    }


def finalize_suite_run(jobs: "RuntimeJobStore", shelf: ScreenStore, run_id: str) -> None:
    """Collect a completed DISPATCHED run and persist what collection produces.

    A connected-runtime run finished but produced nothing durable: aggregates
    were the operator's manual step, the recomputed per-trial metrics were
    thrown away, the suite's EvidenceCases were never stored, and no Artifact
    was ever built — the Artifacts and Evidence screens stayed empty after a run
    that had both. This is that missing step, run server-side the moment the run
    completes. Best-effort: a collection failure must never fail the runtime's
    completing request, so every step is guarded.
    """
    try:
        _collect_and_persist(jobs, shelf, run_id)
    except Exception:  # noqa: BLE001 — collection is best-effort; never fail the request
        pass
    # ALWAYS transition to the terminal state and publish `done` — even for a
    # raw (non-suite) run or a collection that failed. The run reached its plan;
    # it must not be stranded in `analyzing`.
    jobs.mark_completed(run_id)


def _collect_and_persist(jobs: "RuntimeJobStore", shelf: ScreenStore, run_id: str) -> None:
    assignment_dict = jobs.assignment_of(run_id)
    manifest = (assignment_dict or {}).get("suite")
    runtime_ref = (assignment_dict or {}).get("_runtime_ref")
    if not isinstance(manifest, dict):
        return  # not a suite dispatch (a raw experiment run) — nothing to collect
    import dataclasses

    from lab_contracts import validate_artifact
    from lab_suite.dispatch import build_assignment, collect_suite_run

    rebuilt = dataclasses.replace(
        build_assignment(manifest, str(runtime_ref or "runtime")), run_id=run_id)
    collected = collect_suite_run(rebuilt, jobs)

    # persist EVERYTHING first, then transition to completed last (below), so a
    # browser that reloads on the SSE `done` sees the finished report — not a
    # race against collection that reads empty aggregates.
    by_unit = {
        f"{t['scenario_id']}:{t['condition_id']}:{t['repeat_index']}": t.get("metrics", {})
        for t in collected.trials if t.get("status") == "completed"
    }
    jobs.overwrite_trial_metrics(run_id, by_unit)  # type: ignore[arg-type]
    for case in collected.evidence_cases:
        try:
            shelf.put("evidence-case", case)
        except ScreenStoreError:
            pass
    try:
        created = _now_iso()
        artifact = collected.artifact(f"a_{run_id}", created, _run_environment(manifest))
        if not validate_artifact(artifact, "artifact"):
            shelf.put("artifact", artifact, id_field="artifact_id")
    except Exception:  # noqa: BLE001
        pass
    if collected.aggregates:
        with jobs._lock:  # noqa: SLF001 — store the aggregates without finalizing yet
            job = jobs._jobs.get(run_id)  # noqa: SLF001
            if job is not None:
                job.aggregates = list(collected.aggregates)


def _verify_artifact_integrity(artifact: dict[str, object]) -> None:
    """The artifact story is hash-spined so a reader can check the numbers. An
    artifact whose embedded body contradicts its own hashes is refused rather
    than stored and served as if the figures were genuine — schema validation
    alone let a forged aggregate estimate through.
    """
    from lab_contracts import content_hash

    bundle = artifact.get("bundle")
    hashes: dict[str, object] = artifact.get("content_hashes") or {}  # type: ignore[assignment]
    if not isinstance(bundle, dict) or not isinstance(hashes, dict):
        return  # schema validation (run by ScreenStore.put) owns shape errors
    if hashes.get("bundle") != content_hash(bundle):
        raise RuntimeJobsError(
            422, "artifact bundle does not match its content hash — the embedded "
                 "body was edited after the artifact was sealed")
    # the bundle's own spine covers environment/trials/meta; a mismatch there is
    # a body edited under a stale hash
    inner: dict[str, object] = bundle.get("content_hashes") or {}  # type: ignore[assignment]
    for field_name in ("environment", "trials"):
        if field_name in inner and field_name in bundle:
            if inner[field_name] != content_hash(bundle[field_name]):
                raise RuntimeJobsError(
                    422, f"artifact bundle.{field_name} does not match its content hash")


def _run_environment(manifest: dict[str, object]) -> dict[str, object]:
    return {"model": {"provider": "connected_runtime",
                      "id": str(manifest.get("id", "suite"))}}


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class _Trial:
    trial_id: str
    events: list[dict[str, object]] = field(default_factory=list)
    trace: dict[str, object] | None = None
    # per-trial metrics the RUNTIME measured (duration, tokens, cost). Carried
    # BESIDE the trace, never inside it: trace/v1 is axor-core-owned and
    # describes what happened, while cost and latency are Lab's experiment
    # metadata. Widening a shared schema for one consumer's bookkeeping is how
    # a "shared" schema stops being shared.
    metrics: dict[str, object] = field(default_factory=dict)
    # the fingerprint of the config the RUNTIME actually governed under. Lab
    # recomputes it from the assignment it issued and refuses a mismatch — it is
    # reported, never trusted.
    runtime_config_hash: str | None = None
    status: str = "pending"  # pending | completed | failed
    attempt: int = 1     # the current TrialAttempt ordinal (retries supersede)
    superseded: int = 0  # how many prior attempts this trial superseded


@dataclass
class _Job:
    job_id: str
    runtime_ref: str
    assignment: dict[str, object]
    planned: tuple[str, ...]
    state: str = "waiting_for_runtime"
    trials: dict[str, _Trial] = field(default_factory=dict)
    estimate: dict[str, object] = field(default_factory=dict)
    aggregates: list[dict[str, object]] = field(default_factory=list)
    # epoch seconds — so the Runs list can show a run's age and a dead run
    # (running, but not touched in a long time) is distinguishable from an
    # active one. Updated on every published transition.
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Live listeners on this run's event stream. A queue per subscriber, so a
    # slow reader cannot stall the runtime thread that is publishing.
    listeners: "list[queue.SimpleQueue]" = field(default_factory=list)


# A run is over when it reaches one of these; the event stream closes with it
# rather than holding a connection open for something that will never move.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


class RuntimeJobStore:
    """Thread-safe, in-memory assignment store. Lab hands out jobs; a connected
    runtime claims one, streams its trials' events, and completes each trial by
    uploading the finished trace. A job reaches `completed` once every planned
    trial has completed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runtimes: dict[str, dict[str, object]] = {}  # runtime_ref -> {..., ingest_key}
        self._by_key: dict[str, str] = {}                  # ingest_key -> runtime_ref
        self._jobs: dict[str, _Job] = {}
        self._n = 0

    def _next(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:04d}_{secrets.token_hex(6)}"

    # -- control surface --------------------------------------------------
    def connect_runtime(self, model: str = "", agent_ref: str | None = None) -> dict[str, object]:
        with self._lock:
            runtime_ref = self._next("rt")
            ingest_key = secrets.token_hex(24)
            self._runtimes[runtime_ref] = {
                "runtime_ref": runtime_ref, "agent_ref": agent_ref,
                "model": model, "status": "connected", "ingest_key": ingest_key,
            }
            self._by_key[ingest_key] = runtime_ref
            return {"runtime_ref": runtime_ref, "ingest_key": ingest_key}

    def list_runtimes(self) -> list[dict[str, object]]:
        with self._lock:
            return [{k: v for k, v in r.items() if k != "ingest_key"}
                    for r in self._runtimes.values()]

    def runtime_for_key(self, ingest_key: str) -> str | None:
        with self._lock:
            return self._by_key.get(ingest_key)

    def create_run(self, runtime_ref: str, experiment: dict[str, object],
                   planned: list[str] | None = None, *,
                   require_confirmation: bool = False,
                   estimate: dict[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            if runtime_ref not in self._runtimes:
                raise RuntimeJobsError(404, f"unknown runtime_ref {runtime_ref!r}")
            job_id = self._next("run")
            plan = tuple(str(t) for t in (planned or experiment.get("planned_trials", []) or []))
            # `awaiting_confirmation` sits before run start (ui-backend-contract §4):
            # the run holds the plan + estimate the operator confirms before it is
            # ever offered to a runtime. Default stays waiting_for_runtime so the
            # unconfirmed simple flow is unchanged.
            state = "awaiting_confirmation" if require_confirmation else "waiting_for_runtime"
            self._jobs[job_id] = _Job(
                job_id=job_id, runtime_ref=runtime_ref, assignment=dict(experiment),
                planned=plan, state=state, estimate=dict(estimate or {}),
            )
            return {"run_id": job_id, "state": state, "estimate": dict(estimate or {})}

    def confirm_run(self, job_id: str) -> dict[str, object]:
        """Confirm an `awaiting_confirmation` run (the operator accepted the
        estimate) → it becomes claimable (`waiting_for_runtime`)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            if job.state != "awaiting_confirmation":
                raise RuntimeJobsError(409, f"run {job_id!r} is not awaiting confirmation "
                                            f"(state {job.state})")
            job.state = "waiting_for_runtime"
            self._publish_locked(job)
            return {"run_id": job_id, "state": job.state}

    def attach_aggregates(self, job_id: str,
                          aggregates: list[dict[str, object]]) -> dict[str, object]:
        """Attach the runner-computed `bundle.aggregates` and finalize the run.
        Lab RENDERS aggregates (ui-backend-contract §3), it does not compute them —
        the runner/analysis assembles them and posts them here."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            if any(not isinstance(a, dict) for a in aggregates):
                raise RuntimeJobsError(400, "each aggregate must be an object")
            # Finalization requires plan coverage. Attaching aggregates (even an
            # empty list) used to flip a 1/2 run to `completed`, painting a green
            # terminal badge over a run that never finished. A run with trials
            # still outstanding is closed by cancel_run, not by pretending its
            # aggregates are final.
            if job.planned:
                done = {tid for tid, t in job.trials.items()
                        if t.status in ("completed", "failed")}
                if not set(job.planned) <= done:
                    outstanding = len(set(job.planned) - done)
                    raise RuntimeJobsError(
                        409, f"cannot finalize run {job_id!r}: {outstanding} planned "
                             "trial(s) have not completed (cancel it to close a partial run)")
            job.aggregates = list(aggregates)
            if job.state in ("running", "receiving_traces", "analyzing"):
                job.state = "completed"
            self._publish_locked(job)
            return {"run_id": job_id, "state": job.state, "aggregates": len(job.aggregates)}

    # -- live progress ----------------------------------------------------
    def subscribe(self, job_id: str) -> "queue.SimpleQueue":
        """A queue of this run's progress events, plus the current snapshot.

        The snapshot is delivered THROUGH the queue, inside the lock, so a
        subscriber cannot miss a transition that lands between "read the current
        state" and "start listening". Reading state first and subscribing second
        is the classic lost-update: a run that finishes in that gap streams
        nothing and the screen waits forever on a run that is already done.
        """
        listener: queue.SimpleQueue = queue.SimpleQueue()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            job.listeners.append(listener)
            for frame in self._frames_locked(job):
                listener.put(frame)
        return listener

    def unsubscribe(self, job_id: str, listener: "queue.SimpleQueue") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.listeners = [x for x in job.listeners if x is not listener]

    def _frames_locked(self, job: _Job) -> list[tuple[str, dict[str, object]]]:
        """The two frames that describe a run's progress right now.

        `trials` comes FIRST and `state` second, because a terminal `state`
        closes the stream: emitting it first meant a client subscribing to an
        already-finished run got the state, then `done`, and never the trial
        data queued behind it. Progress before the frame that ends the stream.
        """
        done = sum(1 for t in job.trials.values() if t.status in ("completed", "failed"))
        return [
            ("trials", {"run_id": job.job_id, "completed": done,
                        "planned": len(job.planned),
                        "trials": [
                            {"trial_id": t.trial_id, "status": t.status,
                             "attempt": t.attempt, "metrics": dict(t.metrics)}
                            for t in job.trials.values()
                        ]}),
            ("state", {"run_id": job.job_id, "state": job.state,
                       "terminal": job.state in TERMINAL_STATES}),
        ]

    def _publish_locked(self, job: _Job) -> None:
        """Push the current snapshot to every listener. Called with the lock
        held, from the mutation that changed something — never from a poller,
        so a screen sees a transition when it happens rather than up to an
        interval later."""
        job.updated_at = time.time()
        if not job.listeners:
            return
        frames = self._frames_locked(job)
        for listener in job.listeners:
            for frame in frames:
                listener.put(frame)

    def run_ids(self) -> list[str]:
        """Every run this store knows, oldest first — the Launchpad's recent
        activity reads the tail."""
        with self._lock:
            return list(self._jobs)

    def run_state(self, job_id: str) -> str:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            return job.state

    def trial_trace(self, job_id: str, trial_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            trial = job.trials.get(trial_id)
            if trial is None or trial.trace is None:
                raise RuntimeJobsError(404, f"no trace for trial {trial_id!r}")
            return trial.trace

    def _results_locked(self, job: _Job) -> dict[str, object]:
        # the runtime REPORTS runtime_config_hash; it is verified only at collect,
        # which recomputes it from the assignment and refuses a mismatch. Served
        # here it is unverified metadata — flagged as such so a consumer does not
        # read a runtime's self-reported fingerprint as one Lab checked.
        trials = [
            {"trial_id": t.trial_id, "status": t.status, "attempt": t.attempt,
             "superseded": t.superseded, "events": len(t.events),
             "has_trace": t.trace is not None, "metrics": dict(t.metrics),
             "runtime_config_hash": t.runtime_config_hash,
             "runtime_config_hash_verified": False}
            for t in job.trials.values()
        ]
        # planned units the runtime never started are a real state — `pending` —
        # not an absence. Omitting them made the run screen list fewer trials
        # than the plan and turned a deep link to an unstarted trial into a hard
        # 404; a run stuck at 3/5 could not show what the other two were.
        started = set(job.trials)
        for unit in job.planned:
            if unit not in started:
                trials.append({
                    "trial_id": unit, "status": "pending", "attempt": 0,
                    "superseded": 0, "events": 0, "has_trace": False,
                    "metrics": {}, "runtime_config_hash": None,
                })
        return {
            "run_id": job.job_id, "state": job.state,
            "planned_trials": list(job.planned),
            "estimate": dict(job.estimate),
            "trials": trials,
            "traces": [t.trace for t in job.trials.values() if t.trace is not None],
            # `bundle.aggregates` — RENDERED by the UI, never recomputed there
            "aggregates": list(job.aggregates),
        }

    def results(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            return self._results_locked(job)

    def overwrite_trial_metrics(self, job_id: str,
                                by_unit: dict[str, dict[str, object]]) -> None:
        """Replace stored per-trial metrics with Lab's recomputed ones.

        The runtime pushes the measurements it alone can take (duration, tokens);
        Lab recomputes the verdict-bearing ones (task_success, suite metrics)
        from the trace at collect. Persisting the collected set here is what lets
        the trial screen and a regression read Lab's authoritative numbers
        instead of whatever the runtime reported — the runtime no longer grades
        its own homework on the UI."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for unit, metrics in by_unit.items():
                trial = job.trials.get(unit)
                if trial is not None:
                    # Lab's recomputation IS the authority — unlike a runtime
                    # push, it keeps task_success/ASR (the verdict it recomputed)
                    trial.metrics = {
                        str(k): v for k, v in metrics.items()
                        if isinstance(v, (int, float, str, bool))
                    }

    def assignment_of(self, job_id: str) -> dict[str, object] | None:
        """The assignment a run was created with, plus its runtime_ref — enough
        to rebuild a SuiteAssignment and collect the run."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {**job.assignment, "_runtime_ref": job.runtime_ref}

    def run_suite_context(self, job_id: str) -> dict[str, object]:
        """The scenarios and evaluators the run's own suite declared, for a
        regression check. An `evaluator_outcome` rule needs the evaluator table;
        the client sends only {run_id}, so it is defaulted from here — the run
        already carries the suite it executed."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            suite: dict[str, object] = job.assignment.get("suite") or {}  # type: ignore[assignment]
        scenarios = {str(s.get("name")): s
                     for s in (suite.get("scenarios") or [])  # type: ignore[union-attr]
                     if isinstance(s, dict)}
        evaluation: dict[str, object] = suite.get("evaluation") or {}  # type: ignore[assignment]
        evaluators = {str(e.get("id")): e
                      for e in (evaluation.get("evaluators") or [])  # type: ignore[union-attr]
                      if isinstance(e, dict)}
        return {"scenarios": scenarios, "evaluators": evaluators}

    def list_runs(self) -> list[dict[str, object]]:
        """Every run, newest first — the Runs screen's source.

        The screen used to read `home.recent_runs`, capped at 5, so a sixth run
        (or a stuck partial one pushed past the cap) silently vanished from the
        only screen named Runs."""
        with self._lock:
            rows = [
                {"run_id": j.job_id, "state": j.state,
                 "planned": len(j.planned),
                 "completed": sum(1 for t in j.trials.values()
                                  if t.status == "completed"),
                 "created_at": j.created_at, "updated_at": j.updated_at}
                for j in self._jobs.values()
            ]
        return list(reversed(rows))

    # -- runtime-facing surface ------------------------------------------
    def list_jobs(self, runtime_ref: str) -> list[dict[str, object]]:
        with self._lock:
            return [{"job_id": j.job_id, "state": j.state,
                     "planned_trials": list(j.planned)}
                    for j in self._jobs.values()
                    if j.runtime_ref == runtime_ref and j.state == "waiting_for_runtime"]

    def claim(self, job_id: str, runtime_ref: str) -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            if job.state != "waiting_for_runtime":
                raise RuntimeJobsError(409, f"run {job_id!r} is not claimable (state {job.state})")
            job.state = "running"
            self._publish_locked(job)
            return {"run_id": job_id, "assignment": job.assignment,
                    "planned_trials": list(job.planned)}

    def append_events(self, job_id: str, trial_id: str, runtime_ref: str,
                      events: list[dict[str, object]]) -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            self._require_active(job)
            self._require_planned(job, trial_id)
            trial = job.trials.setdefault(trial_id, _Trial(trial_id=trial_id))
            if trial.status != "pending":
                # streaming events into an already-finished trial starts a fresh
                # TrialAttempt: a runtime re-ran the unit (a retry). The prior
                # attempt is superseded — not a 409 conflict (ui-backend-contract
                # TrialAttempt supersede-idempotency).
                trial.attempt += 1
                trial.superseded += 1
                trial.status = "pending"
                trial.events = []
                trial.trace = None
            trial.events.extend(events)
            # a trial back in flight means the run is not done — reopen a run
            # that had reached `completed`, so it is never `completed` with a
            # pending trial inside it (the self-contradiction P5-6 flagged)
            if job.state in ("running", "completed", "analyzing"):
                job.state = "receiving_traces"
            self._publish_locked(job)
            return {"trial_id": trial_id, "events": len(trial.events), "attempt": trial.attempt}

    def complete_trial(self, job_id: str, trial_id: str, runtime_ref: str,
                       trace: dict[str, object] | None, status: str = "completed",
                       metrics: dict[str, object] | None = None,
                       runtime_config_hash: str | None = None) -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            self._require_active(job)
            self._require_planned(job, trial_id)
            # the runtime is untrusted input. A status outside the contract is a
            # bug on its side, not a silent success: coercing "error" to
            # "completed" counted a broken trial as a passing one.
            if status not in ("completed", "failed"):
                raise RuntimeJobsError(
                    400, f"trial status must be 'completed' or 'failed', got {status!r}")
            # a completed trial MUST carry a schema-valid trace; a failed one may
            # carry a partial trace or none. Accepting an invalid trace here let
            # poison reach `completed` and be served to the UI raw, while collect
            # could never assemble the run into a bundle — a durable DoS.
            if status == "completed":
                errors = _trace_errors(trace)
                if errors:
                    raise RuntimeJobsError(
                        422, f"completed trial needs a valid trace: {errors[:3]}")
            elif trace is not None and _trace_errors(trace):
                trace = None  # keep the failure, drop the unusable trace body
            trial = job.trials.setdefault(trial_id, _Trial(trial_id=trial_id))
            new_status = status
            if trial.status in ("completed", "failed"):
                # re-completing an already-finished trial. Identical (status,trace)
                # is IDEMPOTENT — a duplicate delivery, not a change. A DIFFERENT
                # trace SUPERSEDES the prior attempt (the runtime re-ran the unit).
                if trial.status == new_status and trial.trace == trace:
                    return {"trial_id": trial_id, "status": trial.status,
                            "run_state": job.state, "attempt": trial.attempt,
                            "superseded": trial.superseded, "idempotent": True}
                trial.attempt += 1
                trial.superseded += 1
            trial.trace = trace
            trial.status = new_status
            if runtime_config_hash:
                trial.runtime_config_hash = str(runtime_config_hash)
            if metrics:
                trial.metrics = _sanitize_metrics(metrics)
            self._maybe_finish(job)
            self._publish_locked(job)
            return {"trial_id": trial_id, "status": trial.status, "run_state": job.state,
                    "attempt": trial.attempt, "superseded": trial.superseded}

    def cancel_run(self, job_id: str) -> dict[str, object]:
        """Terminate a run that will not finish on its own.

        A run whose runtime died mid-plan otherwise sits `running` forever —
        `failed`/`cancelled` were declared terminal states nothing ever
        assigned. Cancelling is the operator's way to close it; already-terminal
        runs are left as they are (idempotent)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            if job.state not in TERMINAL_STATES:
                job.state = "cancelled"
                self._publish_locked(job)
            return {"run_id": job_id, "state": job.state}

    # -- internals --------------------------------------------------------
    def _require_owned(self, job_id: str, runtime_ref: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise RuntimeJobsError(404, f"unknown run {job_id!r}")
        if job.runtime_ref != runtime_ref:
            raise RuntimeJobsError(403, "this runtime does not own that run")
        return job

    # states in which a run does not accept trials: it was never claimed
    # (waiting_for_runtime), never confirmed (awaiting_confirmation), or is dead
    # (cancelled). Completing into any of these bypassed the claim/confirm gate.
    # A run that already reached `completed` DOES still accept a superseding
    # retry (TrialAttempt idempotency) — which reopens it, see below.
    _CLOSED_TO_TRIALS = frozenset({"waiting_for_runtime", "awaiting_confirmation", "cancelled"})

    def _require_active(self, job: _Job) -> None:
        if job.state in self._CLOSED_TO_TRIALS:
            raise RuntimeJobsError(
                409, f"run {job.job_id!r} is not accepting trials (state {job.state})")

    def _require_planned(self, job: _Job, trial_id: str) -> None:
        """The runtime may only push trials Lab planned. An unplanned unit was
        stored and rendered as a trial the experiment never described — the same
        rule collect_suite_run enforces, moved to the moment of arrival."""
        if job.planned and trial_id not in job.planned:
            raise RuntimeJobsError(
                409, f"trial {trial_id!r} is not in the plan for run {job.job_id!r}")

    def _maybe_finish(self, job: _Job) -> None:
        # every planned trial done → `analyzing`, NOT `completed`. The terminal
        # `completed` (and the SSE `done` that closes the stream) is published
        # only after finalize has persisted aggregates, metrics, evidence and the
        # artifact — otherwise a browser reloads on `done` and races the
        # collection, reading an empty report. A run with no plan also parks at
        # `analyzing` until the runtime signals overall completion.
        if not job.planned:
            job.state = "analyzing"
            return
        done = {tid for tid, t in job.trials.items() if t.status in ("completed", "failed")}
        if set(job.planned) <= done and job.state not in TERMINAL_STATES:
            job.state = "analyzing"

    def mark_completed(self, job_id: str) -> dict[str, object]:
        """Transition an analyzing run to the terminal `completed` and publish
        `done`. Called by finalize AFTER persistence, so a subscriber that
        reloads on `done` sees the finished report. Idempotent."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return {"run_id": job_id, "state": "unknown"}
            if job.state not in TERMINAL_STATES:
                job.state = "completed"
                self._publish_locked(job)
            return {"run_id": job_id, "state": job.state}


def plan_experiment(experiment: dict[str, object]) -> dict[str, object]:
    """Expand an `experiment/v1` into its planned trial units + a rough estimate
    (ui-backend-contract `/experiments/plan` → `{trials, estimate}`). A trial unit
    is one (scenario × condition × repeat); the plan is deterministic so the same
    experiment always yields the same trial ids. This is a PLAN, not execution —
    the runtime later runs each unit and pushes its trace."""
    scenarios = [str(s) for s in (experiment.get("scenario_ids") or []) if s]
    conditions = experiment.get("condition_ids") or experiment.get("conditions") or []
    # condition/v1 names the arm `id`; `condition_id` is accepted as the
    # already-flattened form. Reading only `condition_id` collapsed every real
    # condition to "None", so a 2-arm plan produced two PAIRS of identical trial
    # ids and the runtime's second arm overwrote the first.
    condition_ids = [
        str(c.get("id") or c.get("condition_id")) if isinstance(c, dict) else str(c)
        for c in conditions if c
    ]
    try:
        repeats = int(experiment.get("repeats", 1) or 1)
    except (TypeError, ValueError):
        repeats = 1
    repeats = max(repeats, 1)
    if not scenarios:
        scenarios = ["scenario"]
    if not condition_ids:
        condition_ids = ["condition"]
    trials = [
        f"{scenario}:{condition}:{i}"
        for scenario in scenarios
        for condition in condition_ids
        for i in range(repeats)
    ]
    return {
        "trials": trials,
        "estimate": {
            "trials": len(trials),
            "scenarios": len(scenarios),
            "conditions": len(condition_ids),
            "repeats": repeats,
        },
    }


def make_runtime_server(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    control_token: str | None = None,
    store: RuntimeJobStore | None = None,
    screens: "ScreenStore | None" = None,
    web_root: "pathlib.Path | None" = None,
) -> ThreadingHTTPServer:
    """A threaded runtime-jobs + screen-API server. `control_token`, if set,
    gates the control surface (runtime registration, run assignment, every
    screen); the runtime-facing endpoints are gated by the per-runtime
    ingest_key issued at connect."""
    jobs = store or RuntimeJobStore()
    shelf = screens if screens is not None else ScreenStore()
    # The built app, when there is one. Absent, every API route still answers
    # and only the browser surface is missing — the server never pretends to
    # serve a frontend that was not built.
    site = web_root if web_root is not None else static_default_root()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:  # quiet
            return

        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: int, text: str, content_type: str) -> None:
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_sse(self, job_id: str, max_seconds: float = 900.0) -> None:
            """A LIVE event stream for one run.

            This used to send two frames and close — a snapshot wearing
            `text/event-stream`, so a screen that subscribed got one picture of a
            run in progress and nothing after it. The Run screen could show
            progress only by being reloaded.

            Three things it has to get right:

              - **No lost update.** `subscribe` delivers the current snapshot
                THROUGH the queue while holding the store's lock, so a run that
                finishes between "read state" and "start listening" cannot slip
                past. Reading first and subscribing second is exactly how a
                screen ends up waiting forever on a run that is already done.
              - **Closing.** The stream ends when the run reaches a terminal
                state. A browser reconnects an EventSource that closes, so it
                also sends a `done` event first: the client can stop rather than
                reconnect to a run that will never move again.
              - **Not leaking a thread.** A client that vanishes is only noticed
                on a write, so a heartbeat comment goes out on every idle tick;
                `max_seconds` is the backstop for a run that hangs without ever
                reaching a terminal state.
            """
            listener = jobs.subscribe(job_id)   # raises 404 before any header
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # `Connection: close`, not keep-alive. This stream ENDS — when the
            # run reaches a terminal state — and it carries no Content-Length,
            # so on a keep-alive connection the client has no way to know the
            # body is over and blocks reading a response that will never grow.
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            deadline = time.monotonic() + max_seconds
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._sse_frame("timeout", {"run_id": job_id})
                        return
                    try:
                        name, payload = listener.get(timeout=min(15.0, remaining))
                    except queue.Empty:
                        # a comment line: invisible to EventSource, and the write
                        # is what reveals a client that has gone away
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    self._sse_frame(name, payload)
                    if name == "state" and payload.get("terminal"):
                        self._sse_frame("done", {"run_id": job_id,
                                                 "state": payload.get("state")})
                        return
            except (BrokenPipeError, ConnectionResetError):
                return          # the client closed the tab; nothing to report
            finally:
                jobs.unsubscribe(job_id, listener)

        def _sse_frame(self, name: str, payload: dict[str, object]) -> None:
            self.wfile.write(
                f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()
            )
            self.wfile.flush()

        def _bearer(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            return auth[7:] if auth.startswith("Bearer ") else None

        def _require_control(self) -> None:
            if control_token is None:
                return
            if self._bearer() == control_token:
                return
            # `EventSource` cannot set a header, so the SSE stream — and only it —
            # also accepts the token as a query parameter. A token in a URL can
            # land in a proxy log, which is a real cost; the alternative is an
            # unauthenticated stream of run contents to anyone who can reach the
            # port. Restricted to this one route so the trade is not quietly
            # extended to endpoints that have a header available.
            if _RUN_EVENTS_RE.match(self.path.split("?", 1)[0]):
                from urllib.parse import parse_qs, urlparse

                supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
                if supplied == control_token:
                    return
            raise RuntimeJobsError(401, "control token required")

        def _runtime_ref(self) -> str:
            key = self._bearer()
            ref = jobs.runtime_for_key(key) if key else None
            if ref is None:
                raise RuntimeJobsError(401, "a valid runtime ingest_key is required")
            return ref

        def _resolve_suite(self, suite_id: str) -> dict[str, object]:
            """A SAVED suite wins over the built-in of the same id.

            The Builder loaded from the registry and saved to the store, so a
            save reported success and the next load served the original
            document — the edit vanished with a green tick beside it. Reading
            and writing have to name the same place.
            """
            from lab_suite import builtin_registry
            from lab_suite.errors import SuiteNotFound

            try:
                return shelf.get("suite", suite_id)
            except ScreenStoreError:
                pass
            try:
                return builtin_registry().get(suite_id).manifest()
            except SuiteNotFound:
                raise

        def _catalog(self, builtin: list[dict[str, object]]) -> list[dict[str, object]]:
            """Built-ins and announced placeholders, with saved suites layered
            over them — an edited built-in shows the EDITED name, and a suite the
            workspace authored appears at all."""
            saved = {
                str(m.get("id", "")): {
                    "id": str(m.get("id", "")),
                    "name": m.get("name", m.get("id", "")),
                    "description": m.get("description", ""),
                    "origin": m.get("origin", "mine"),
                    "tags": m.get("tags", []),
                    "capabilities": m.get("capabilities", []),
                    "available": True,
                }
                for m in shelf.list("suite") if m.get("id")
            }
            # ORDER is preserved: registered suites, then the announced
            # placeholders, then anything this workspace authored. An empty
            # store therefore returns exactly what `axor-lab suites` prints,
            # which is the property `test_the_catalog_endpoint_serves_the_same_
            # list_as_the_cli` pins.
            cards = [dict(saved.pop(str(c["id"]), c)) for c in builtin]
            cards.extend(saved[key] for key in sorted(saved))
            return cards

        def _announced_reason(self, suite_id: str) -> str | None:
            """The catalog reason for an announced-but-unavailable suite, if any."""
            from lab_suite import suite_catalog

            for card in suite_catalog():
                if str(card.get("id")) == suite_id and not card.get("available", True):
                    return str(card.get("reason", "not yet available"))
            return None

        def _save_suite(self, document: dict[str, object]) -> str:
            """Store a suite after the SAME semantic check the Builder runs.

            The store validated only the JSON Schema, so a direct API client
            could persist a manifest that is structurally valid and semantically
            broken (no scenarios, governance without arms) that the Builder would
            then refuse to run. And a suite this workspace authored is stamped
            `workspace`: a fork of a built-in kept `origin: built_in`, so the
            catalog could not tell a user's suite from the shipped one."""
            from lab_suite import builtin_registry, validate_manifest
            from lab_suite.errors import SuiteNotFound

            errors = validate_manifest(document)
            if errors:
                raise RuntimeJobsError(422, "; ".join(errors[:5]))
            suite_id = str(document.get("id", ""))
            try:
                builtin_registry().get(suite_id)
            except SuiteNotFound:
                document = {**document, "origin": "workspace"}
            return shelf.put("suite", document)

        def _document(self, key: str) -> dict[str, object]:
            """The document a Builder/screen POSTs, under its own key.

            Named rather than "the whole body" so a client cannot smuggle a
            second document past validation by nesting it, and so a missing body
            says which key it was missing.
            """
            body = self._read_json()
            document = body.get(key)
            if not isinstance(document, dict):
                raise RuntimeJobsError(400, f"this endpoint requires {{{key}}}")
            return document

        def _playground_suite(
            self, body: dict[str, object],
        ) -> tuple[dict[str, object], object]:
            """The suite a Playground preview runs: a posted manifest, or a
            registered id."""
            from lab_suite import builtin_registry
            from lab_suite.errors import SuiteNotFound
            from lab_suite.sdk import BaseSuite

            registry = builtin_registry()
            manifest = body.get("suite")
            if not isinstance(manifest, dict):
                suite_id = body.get("suite_id")
                if not isinstance(suite_id, str):
                    raise RuntimeJobsError(
                        400, "a playground trial requires {suite} or {suite_id}",
                    )
                try:
                    suite = registry.get(suite_id)
                except SuiteNotFound as exc:
                    raise RuntimeJobsError(404, str(exc)) from None
                return suite.manifest(), suite
            try:
                return manifest, registry.get(str(manifest.get("id", "")))
            except SuiteNotFound:
                # an unregistered manifest still previews, on BaseSuite defaults
                unimplemented = BaseSuite()
                unimplemented.id = str(manifest.get("id", ""))
                unimplemented.manifest = lambda: manifest  # type: ignore[method-assign]
                return manifest, unimplemented

        def _read_json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > _MAX_BODY:
                raise RuntimeJobsError(413, "request body too large")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                obj = json.loads(raw)
            except ValueError as exc:
                raise RuntimeJobsError(400, f"invalid JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise RuntimeJobsError(400, "body must be a JSON object")
            return obj

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            try:
                # the routing path WITHOUT the query string. Comparing routes
                # against `self.path` meant `GET /suites?x=1` matched nothing and
                # fell through to a 404 — a request that is merely decorated is
                # not a different route.
                path = unquote(self.path.split("?", 1)[0])
                # ...and DECODED: the client percent-encodes every path segment
                # (a trial unit carries colons), so without unquote a trial link
                # that works when typed raw 404s when the app follows it
                if path == "/home":
                    # the Launchpad: the NEXT action, not the past. A catalog of
                    # what has already been published is a different screen.
                    from lab_suite import suite_catalog

                    self._require_control()
                    self._send(200, home_payload(
                        jobs.list_runtimes(),
                        [{"run_id": j, "state": jobs.run_state(j)} for j in jobs.run_ids()],
                        suite_catalog(), shelf,
                    ))
                    return
                if path == "/evidence":
                    self._require_control()
                    self._send(200, evidence_list(shelf))
                    return
                if path == "/regressions":
                    self._require_control()
                    self._send(200, regression_list(shelf))
                    return
                if path == "/artifacts":
                    self._require_control()
                    self._send(200, artifact_list(shelf))
                    return
                m = _EVIDENCE_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, shelf.get("evidence-case", m.group(1)))
                    return
                m = _REGRESSION_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, shelf.get("regression", m.group(1)))
                    return
                m = _ARTIFACT_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, shelf.get("artifact", m.group(1)))
                    return
                m = _RUN_REPORT_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, run_report(jobs.results(m.group(1))))
                    return
                m = _RUN_TRIAL_RE.match(path)
                if m:
                    self._require_control()
                    run_id, trial_id = m.group(1), m.group(2)
                    try:
                        trace = jobs.trial_trace(run_id, trial_id)
                    except RuntimeJobsError:
                        # a planned trial with no trace yet is a real state, not
                        # a 404: the screen shows the record and says the trace
                        # has not arrived
                        trace = None
                    self._send(200, trial_detail(jobs.results(run_id), trial_id, trace))
                    return
                if path == "/suites":
                    # The Suite Catalog screen (ui-backend-contract.md §2). The
                    # payload comes from `lab_suite.suite_catalog`, the same
                    # function `axor-lab suites` prints — two independent lists
                    # is how a catalog offers a suite the runner cannot resolve —
                    # merged with the suites this workspace has SAVED.
                    from lab_suite import suite_catalog

                    self._require_control()
                    self._send(200, {"suites": self._catalog(suite_catalog())})
                    return
                m = _SUITE_YAML_RE.match(path)
                if m:
                    # the Builder's YAML mode reads the SAME manifest the Basic
                    # and Advanced modes edit — one document, three editors
                    # (RFC §13). A second serializer here is how the modes start
                    # disagreeing about what the experiment is.
                    from lab_suite import to_yaml
                    from lab_suite.errors import SuiteNotFound
                    from lab_suite.yaml_mode import YamlUnavailable

                    self._require_control()
                    try:
                        text = to_yaml(self._resolve_suite(m.group(1)))
                    except SuiteNotFound as exc:
                        raise RuntimeJobsError(404, str(exc)) from None
                    except YamlUnavailable as exc:
                        raise RuntimeJobsError(501, str(exc)) from None
                    self._send_text(200, text, "application/yaml")
                    return
                m = _SUITE_RE.match(path)
                if m:
                    from lab_suite.errors import SuiteNotFound

                    self._require_control()
                    try:
                        manifest = self._resolve_suite(m.group(1))
                    except SuiteNotFound as exc:
                        # an ANNOUNCED-but-unavailable suite has a catalog card
                        # and a reason but no manifest — return the announced
                        # reason, not a raw "registered: [...]" registry dump the
                        # Builder would show as an internals error
                        announced = self._announced_reason(m.group(1))
                        if announced is not None:
                            raise RuntimeJobsError(
                                404, f"'{m.group(1)}' is announced but not yet "
                                     f"available: {announced}") from None
                        raise RuntimeJobsError(404, str(exc)) from None
                    self._send(200, manifest)
                    return
                if path == "/runtimes":
                    self._require_control()
                    self._send(200, {"runtimes": jobs.list_runtimes()})
                    return
                if path == "/runs":
                    # the Runs screen's source — every run, not the home
                    # payload's 5-most-recent slice.
                    self._require_control()
                    self._send(200, {"runs": jobs.list_runs()})
                    return
                if _RUNTIME_JOBS_RE.match(path):
                    ref = self._runtime_ref()
                    self._send(200, {"jobs": jobs.list_jobs(ref)})
                    return
                m = _RUN_RESULTS_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, jobs.results(m.group(1)))
                    return
                m = _RUN_TRACE_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, jobs.trial_trace(m.group(1), m.group(2)))
                    return
                m = _RUN_EVENTS_RE.match(path)
                if m:
                    self._require_control()
                    self._stream_sse(m.group(1))
                    return
                m = _RUN_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, {"run_id": m.group(1), "state": jobs.run_state(m.group(1))})
                    return
                if self._serve_static():
                    return
                self._send(404, {"error": "not found"})
            except (RuntimeJobsError, ScreenStoreError) as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001 — never leak a traceback
                self._send(500, {"error": f"{type(exc).__name__}"})

        def _serve_static(self) -> bool:
            """The built app. Static files are NOT gated by the control token —
            gating them would mean the login surface needs a login."""
            if site is None:
                return False
            file = static_resolve(site, self.path)
            if file is None and is_app_route(self.path):
                file = site / "index.html"   # deep links survive a reload
            if file is None or not file.is_file():
                return False
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", static_content_type(file))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def do_PUT(self) -> None:  # noqa: N802
            try:
                # the routing path WITHOUT the query string. Comparing routes
                # against `self.path` meant `GET /suites?x=1` matched nothing and
                # fell through to a 404 — a request that is merely decorated is
                # not a different route.
                path = unquote(self.path.split("?", 1)[0])
                # ...and DECODED: the client percent-encodes every path segment
                # (a trial unit carries colons), so without unquote a trial link
                # that works when typed raw 404s when the app follows it
                m = _SUITE_RE.match(path)
                if m:
                    self._require_control()
                    document = self._document("suite")
                    if str(document.get("id")) != m.group(1):
                        raise RuntimeJobsError(
                            400,
                            f"the manifest's id {document.get('id')!r} does not match "
                            f"the path {m.group(1)!r} — saving it would silently "
                            "create a second suite",
                        )
                    self._send(200, {"id": self._save_suite(document)})
                    return
                self._send(404, {"error": "not found"})
            except (RuntimeJobsError, ScreenStoreError) as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001 — never leak a traceback
                self._send(500, {"error": f"{type(exc).__name__}"})

        def do_DELETE(self) -> None:  # noqa: N802
            try:
                path = unquote(self.path.split("?", 1)[0])
                m = _SUITE_RE.match(path)
                if m:
                    self._require_control()
                    # a built-in cannot be deleted — only the workspace copy
                    # layered over it. Deleting an unknown id is a no-op (idempotent).
                    shelf.delete("suite", m.group(1))
                    self._send(200, {"id": m.group(1), "deleted": True})
                    return
                self._send(404, {"error": "not found"})
            except (RuntimeJobsError, ScreenStoreError) as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": f"{type(exc).__name__}"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                # the routing path WITHOUT the query string. Comparing routes
                # against `self.path` meant `GET /suites?x=1` matched nothing and
                # fell through to a 404 — a request that is merely decorated is
                # not a different route.
                path = unquote(self.path.split("?", 1)[0])
                # ...and DECODED: the client percent-encodes every path segment
                # (a trial unit carries colons), so without unquote a trial link
                # that works when typed raw 404s when the app follows it
                if path == "/runtimes/connect":
                    self._require_control()
                    body = self._read_json()
                    self._send(201, jobs.connect_runtime(
                        model=str(body.get("model", "")),
                        agent_ref=body.get("agent_ref"),  # type: ignore[arg-type]
                    ))
                    return
                if path == "/playground/trial":
                    # ONE trial for inspection (RFC §13). Not a Run: nothing is
                    # stored, nothing is aggregated, and the payload says so.
                    from lab_suite.errors import SuiteError

                    self._require_control()
                    body = self._read_json()
                    manifest, suite = self._playground_suite(body)
                    try:
                        result = playground_trial(
                            manifest, suite,
                            scenario_name=body.get("scenario"),  # type: ignore[arg-type]
                            seed=body.get("seed"),  # type: ignore[arg-type]
                        )
                    except SuiteError as exc:
                        # e.g. a multi-agent topology: authorable, not runnable —
                        # a clean 422, not a 500
                        raise RuntimeJobsError(422, str(exc)) from None
                    self._send(200, result)
                    return
                if path == "/suites":
                    self._require_control()
                    self._send(201, {"id": self._save_suite(self._document("suite"))})
                    return
                if path == "/evidence":
                    self._require_control()
                    self._send(201, {
                        "id": shelf.put("evidence-case", self._document("evidence_case")),
                    })
                    return
                if path == "/regressions":
                    self._require_control()
                    self._send(201, {
                        "id": shelf.put("regression", self._document("regression")),
                    })
                    return
                if path == "/artifacts":
                    self._require_control()
                    document = self._document("artifact")
                    _verify_artifact_integrity(document)
                    self._send(201, {"id": shelf.put(
                        "artifact", document, id_field="artifact_id",
                    )})
                    return
                m = _REGRESSION_RUN_RE.match(path)
                if m:
                    # a CHECK, not a Run (lifecycle.md): it consumes trials that
                    # already exist, and `error` stays distinct from `failed`
                    self._require_control()
                    body = self._read_json()
                    run_id = body.get("run_id")
                    if not isinstance(run_id, str):
                        raise RuntimeJobsError(400, "running a regression requires {run_id}")
                    # default the scenarios + evaluator table from the run's OWN
                    # suite. Without this an evaluator_outcome rule always errored
                    # ("evaluator not declared") because the client sends only
                    # {run_id} — the evaluator table it names lives on the run.
                    context = jobs.run_suite_context(run_id)
                    self._send(200, run_regression(
                        shelf.get("regression", m.group(1)), jobs.results(run_id),
                        scenarios=body.get("scenarios") or context["scenarios"],  # type: ignore[arg-type]
                        evaluators=body.get("evaluators") or context["evaluators"],  # type: ignore[arg-type]
                    ))
                    return
                m = _SUITE_DISPATCH_RE.match(path)
                if m:
                    # The Builder's Run button: bind a CONNECTED agent to this
                    # suite and start a run. The suite resolves stored-first —
                    # the document being dispatched is the one Save wrote, not
                    # the built-in it started from. The agent is a runtime_ref
                    # from /runtimes/connect; the manifest's `agents[]` names
                    # what the suite is ABOUT, this names who executes it.
                    from lab_suite.dispatch import build_assignment
                    from lab_suite.errors import SuiteError, SuiteNotFound

                    self._require_control()
                    body = self._read_json()
                    runtime_ref = body.get("runtime_ref")
                    if not isinstance(runtime_ref, str) or not runtime_ref:
                        raise RuntimeJobsError(
                            400,
                            "dispatch requires {runtime_ref} — connect an "
                            "agent on Integrations first",
                        )
                    try:
                        manifest = self._resolve_suite(m.group(1))
                    except SuiteNotFound as exc:
                        raise RuntimeJobsError(404, str(exc)) from None
                    try:
                        planned = build_assignment(manifest, runtime_ref)
                    except SuiteError as exc:
                        # a manifest that resolves but cannot be executed on a
                        # remote runtime is the user's to fix, not a crash
                        raise RuntimeJobsError(422, str(exc)) from None
                    created = jobs.create_run(
                        runtime_ref, planned.assignment,
                        planned=list(planned.planned),
                    )
                    created["planned_trials"] = list(planned.planned)
                    self._send(201, created)
                    return
                if path == "/suites/validate":
                    # The Builder's validate button, in both Basic and YAML
                    # mode. It validates a POSTED manifest, not a registered id:
                    # the whole point is to check a document the user is editing
                    # and has not saved.
                    from lab_suite import validate_manifest

                    self._require_control()
                    body = self._read_json()
                    manifest = body.get("suite")
                    if not isinstance(manifest, dict):
                        raise RuntimeJobsError(400, "validate requires {suite}")
                    errors = validate_manifest(manifest)
                    self._send(200, {"ok": not errors, "errors": errors})
                    return
                if path == "/suites/to-yaml":
                    # Serialize the manifest the Builder is HOLDING, not the one
                    # on disk. Switching Basic -> YAML must show the edits the
                    # user just made; serializing the stored suite would silently
                    # discard them, which is the one thing the "three modes, one
                    # document" rule exists to prevent.
                    from lab_suite import to_yaml
                    from lab_suite.yaml_mode import YamlUnavailable

                    self._require_control()
                    document = self._document("suite")
                    try:
                        self._send_text(200, to_yaml(document), "application/yaml")
                    except YamlUnavailable as exc:
                        raise RuntimeJobsError(501, str(exc)) from None
                    return
                if path == "/suites/validate-yaml":
                    # Same validator, one parse earlier. The YAML mode must not
                    # get a weaker check than the JSON one, or a manifest can be
                    # valid in one editor and not the other.
                    from lab_suite import from_yaml, validate_manifest
                    from lab_suite.errors import SuiteError
                    from lab_suite.yaml_mode import YamlUnavailable

                    self._require_control()
                    body = self._read_json()
                    text = body.get("yaml")
                    if not isinstance(text, str):
                        raise RuntimeJobsError(400, "validate-yaml requires {yaml}")
                    try:
                        parsed = from_yaml(text)
                    except YamlUnavailable as exc:
                        raise RuntimeJobsError(501, str(exc)) from None
                    except SuiteError as exc:
                        self._send(200, {"ok": False, "errors": [str(exc)]})
                        return
                    except Exception as exc:  # noqa: BLE001 — a parse error is the user's
                        self._send(200, {"ok": False, "errors": [f"invalid YAML: {exc}"]})
                        return
                    errors = validate_manifest(parsed)
                    # the PARSED manifest comes back whenever the text PARSES —
                    # including when validation then fails. The Builder's modes
                    # edit one document, and a semantically invalid document is
                    # still THE document: withholding it here trapped the user
                    # in YAML mode, unable to switch to the form that would
                    # help them fix the error. Only an unparseable text has no
                    # document to hand back. The client still never parses YAML
                    # itself — one implementation, the server's.
                    self._send(200, {
                        "ok": not errors, "errors": errors, "suite": parsed,
                    })
                    return
                if path == "/scenarios/validate":
                    self._require_control()
                    body = self._read_json()
                    scenario = body.get("scenario")
                    manifests = body.get("manifests") or {}
                    if not isinstance(scenario, dict) or not isinstance(manifests, dict):
                        raise RuntimeJobsError(400, "validate requires {scenario, manifests}")
                    from lab_contracts import ScenarioValidationError, validate_scenario
                    try:
                        validate_scenario(scenario, manifests)  # type: ignore[arg-type]
                    except ScenarioValidationError as exc:
                        self._send(200, {"ok": False, "errors": list(exc.errors)})
                    except (KeyError, TypeError, ValueError) as exc:
                        self._send(200, {"ok": False, "errors": [f"malformed scenario: {exc}"]})
                    else:
                        self._send(200, {"ok": True, "errors": []})
                    return
                if path == "/experiments/plan":
                    self._require_control()
                    body = self._read_json()
                    experiment = body.get("experiment")
                    if not isinstance(experiment, dict):
                        raise RuntimeJobsError(400, "plan requires {experiment}")
                    self._send(200, plan_experiment(experiment))
                    return
                if path == "/runs":
                    self._require_control()
                    body = self._read_json()
                    experiment = body.get("experiment")
                    runtime_ref = body.get("runtime_ref")
                    if not isinstance(experiment, dict) or not isinstance(runtime_ref, str):
                        raise RuntimeJobsError(400, "runs require {runtime_ref, experiment}")
                    estimate = body.get("estimate")
                    self._send(201, jobs.create_run(
                        runtime_ref, experiment,
                        planned=body.get("planned_trials"),  # type: ignore[arg-type]
                        require_confirmation=bool(body.get("require_confirmation", False)),
                        estimate=estimate if isinstance(estimate, dict) else None,
                    ))
                    return
                m = _RUN_CONFIRM_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, jobs.confirm_run(m.group(1)))
                    return
                m = _RUN_CANCEL_RE.match(path)
                if m:
                    self._require_control()
                    self._send(200, jobs.cancel_run(m.group(1)))
                    return
                m = _RUN_AGG_RE.match(path)
                if m:
                    self._require_control()
                    body = self._read_json()
                    aggregates = body.get("aggregates", [])
                    if not isinstance(aggregates, list):
                        raise RuntimeJobsError(400, "aggregates must be a list")
                    self._send(200, jobs.attach_aggregates(m.group(1), aggregates))
                    return
                m = _CLAIM_RE.match(path)
                if m:
                    ref = self._runtime_ref()
                    self._send(200, jobs.claim(m.group(1), ref))
                    return
                m = _EVENTS_RE.match(path)
                if m:
                    ref = self._runtime_ref()
                    body = self._read_json()
                    events = body.get("events", [])
                    if not isinstance(events, list):
                        raise RuntimeJobsError(400, "events must be a list")
                    self._send(200, jobs.append_events(m.group(1), m.group(2), ref, events))
                    return
                m = _TRIAL_DONE_RE.match(path)
                if m:
                    ref = self._runtime_ref()
                    body = self._read_json()
                    trace = body.get("trace")
                    metrics = body.get("metrics")
                    rch = body.get("runtime_config_hash")
                    result = jobs.complete_trial(
                        m.group(1), m.group(2), ref,
                        trace if isinstance(trace, dict) else None,
                        status=str(body.get("status", "completed")),
                        metrics=metrics if isinstance(metrics, dict) else None,
                        runtime_config_hash=str(rch) if isinstance(rch, str) else None,
                    )
                    # the plan just completed (state `analyzing`) — collect,
                    # persist, and only then transition to `completed`+`done`, so
                    # a browser reloading on `done` sees the finished report
                    if result.get("run_state") == "analyzing":
                        finalize_suite_run(jobs, shelf, m.group(1))
                        result["run_state"] = jobs.run_state(m.group(1))
                    self._send(200, result)
                    return
                self._send(404, {"error": "not found"})
            except (RuntimeJobsError, ScreenStoreError) as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": f"{type(exc).__name__}"})

    server = ThreadingHTTPServer((host, port), Handler)
    server.job_store = jobs  # type: ignore[attr-defined]
    return server
