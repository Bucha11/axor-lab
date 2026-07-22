"""Runtime-jobs API — the connected-runtime execution contract (spec v0.3).

architecture-boundary.md: **Lab assigns, the runtime executes.** Lab never
connects to, executes, or proxies an agent. A user connects an Axor runtime
adapter ONCE (the same one that serves Control Plane); it pulls experiment
assignments, runs them locally, and pushes back kernel events + finished traces.

This is the deliberately SIMPLE first implementation of that contract: a single
process, in-memory job store, stdlib `http.server`. It establishes the surface
and the state machine so a connected runtime can drive a run end-to-end.

**Trust boundary (review v0.3-2, restoring the pre-refactor invariant).** Lab does
NOT trust a runtime's summary of its own work: a completed trial MUST carry a
schema- and semantics-conformant `trace/v1`, and Lab computes any aggregate from
the collected traces at bundle/publish time — it never renders an uploaded
aggregate as a result. A finished attempt is IMMUTABLE (frozen with its
content-addressed `trace_ref`); a re-run is an explicit new `TrialAttempt` that
supersedes the prior one without destroying it. Event batches are idempotent so a
network retry cannot duplicate a ledger.

Control surface (Lab operator / UI):

  POST /runtimes/connect     register a runtime  -> { runtime_ref, ingest_key }
  GET  /runtimes             list connected runtimes
  POST /scenarios/validate   validate a scenario -> { ok, errors[] }
  POST /experiments/plan     expand an experiment -> { trials, estimate }   (fail-closed)
  POST /runs                 assign an experiment to a runtime -> { run_id, state }
  POST /runs/{id}/confirm    confirm an awaiting_confirmation run -> { state }
  POST /runs/{id}/trials/{trial_id}/retry   open a fresh TrialAttempt (supersede)
  GET  /runs/{id}            -> { state }  (a lifecycle state)
  GET  /runs/{id}/events     -> text/event-stream (state + trial progress)
  GET  /runs/{id}/results    -> { trials (attempt history), traces }  (NO uploaded aggregates)
  GET  /runs/{id}/trials/{trial_id}/trace  -> the accepted trial's trace

Runtime-facing (Bearer <ingest_key>; the runtime pulls and pushes):

  GET  /runtime/jobs                                    poll for assignments
  POST /runtime/jobs/{id}/claim                          claim one -> the assignment
  POST /runtime/jobs/{id}/trials/{trial_id}/events       stream kernel events (idempotent by batch_id)
  POST /runtime/jobs/{id}/trials/{trial_id}/complete     finalize the trial (uploads a conformant trace)

The connected_runtime lifecycle (ui-backend-contract.md):
  validating -> waiting_for_runtime -> running -> receiving_traces -> analyzing -> completed
Terminal states (completed | failed | cancelled) are terminal — no further ingest.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_MAX_BODY = 8 * 1024 * 1024
_TERMINAL = ("completed", "failed", "cancelled")

_RUNTIME_JOBS_RE = re.compile(r"^/runtime/jobs$")
_CLAIM_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/claim$")
_EVENTS_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/events$")
_TRIAL_DONE_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/complete$")
_RUN_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)$")
_RUN_RESULTS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/results$")
_RUN_EVENTS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/events$")
_RUN_CONFIRM_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/confirm$")
_RUN_RETRY_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/retry$")
_RUN_TRACE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/trace$")


class RuntimeJobsError(Exception):
    """A bad runtime-jobs request; carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _validate_trace(trace: object) -> list[str]:
    """Schema + semantic conformance of an uploaded `trace/v1` (Lab does not take a
    runtime's word for what it ran). Returns the list of errors, empty if valid."""
    if not isinstance(trace, dict):
        return ["trace must be a JSON object"]
    from lab_contracts import trace_semantics, validate_artifact
    errors = list(validate_artifact(trace, "trace"))
    if not errors:  # semantics assume a schema-valid shape
        errors.extend(trace_semantics(trace))
    return errors


@dataclass
class _Attempt:
    """One TrialAttempt — immutable once terminal (review v0.3-2/v0.3-history)."""

    attempt_id: str
    status: str = "running"  # running | completed | failed
    events: list[dict[str, object]] = field(default_factory=list)
    trace: dict[str, object] | None = None
    trace_ref: str | None = None
    failure: dict[str, object] | None = None
    supersedes: str | None = None
    seen_batches: set[str] = field(default_factory=set)

    def record(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id, "status": self.status,
            "events": len(self.events), "trace_ref": self.trace_ref,
            "failure": self.failure, "supersedes": self.supersedes,
            "has_trace": self.trace is not None,
        }


@dataclass
class _Trial:
    trial_id: str
    unit: dict[str, object] | None  # the assigned TrialUnit coordinate, if any
    attempts: list[_Attempt] = field(default_factory=list)

    @property
    def active(self) -> _Attempt:
        return self.attempts[-1]

    @property
    def status(self) -> str:
        return self.attempts[-1].status if self.attempts else "pending"


@dataclass
class _Job:
    job_id: str
    runtime_ref: str
    assignment: dict[str, object]
    planned: tuple[str, ...]
    units: dict[str, dict[str, object]]  # trial_id -> TrialUnit coordinate (if provided)
    state: str = "waiting_for_runtime"
    trials: dict[str, _Trial] = field(default_factory=dict)
    estimate: dict[str, object] = field(default_factory=dict)


class RuntimeJobStore:
    """Thread-safe, in-memory assignment store. Lab hands out jobs; a connected
    runtime claims one, streams its trials' events, and completes each trial by
    uploading a CONFORMANT trace. A job reaches `completed` once every planned
    trial has an accepted terminal attempt."""

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
                   planned: list[object] | None = None, *,
                   require_confirmation: bool = False,
                   estimate: dict[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            if runtime_ref not in self._runtimes:
                raise RuntimeJobsError(404, f"unknown runtime_ref {runtime_ref!r}")
            job_id = self._next("run")
            plan_src = planned if planned is not None else experiment.get("planned_trials", [])
            plan_ids: list[str] = []
            units: dict[str, dict[str, object]] = {}
            for entry in (plan_src or []):
                if isinstance(entry, dict):
                    # a planned TrialUnit: {trial_id, trial: {coordinate}}
                    tid = str(entry.get("trial_id"))
                    unit = entry.get("trial")
                    if isinstance(unit, dict):
                        units[tid] = unit
                else:
                    tid = str(entry)
                plan_ids.append(tid)
            # `awaiting_confirmation` sits before run start (ui-backend-contract §4).
            state = "awaiting_confirmation" if require_confirmation else "waiting_for_runtime"
            self._jobs[job_id] = _Job(
                job_id=job_id, runtime_ref=runtime_ref, assignment=dict(experiment),
                planned=tuple(plan_ids), units=units, state=state,
                estimate=dict(estimate or {}),
            )
            return {"run_id": job_id, "state": state, "estimate": dict(estimate or {})}

    def confirm_run(self, job_id: str) -> dict[str, object]:
        """Confirm an `awaiting_confirmation` run (the operator accepted the
        estimate) → it becomes claimable (`waiting_for_runtime`)."""
        with self._lock:
            job = self._job(job_id)
            if job.state != "awaiting_confirmation":
                raise RuntimeJobsError(409, f"run {job_id!r} is not awaiting confirmation "
                                            f"(state {job.state})")
            job.state = "waiting_for_runtime"
            return {"run_id": job_id, "state": job.state}

    def run_state(self, job_id: str) -> str:
        with self._lock:
            return self._job(job_id).state

    def trial_trace(self, job_id: str, trial_id: str) -> dict[str, object]:
        with self._lock:
            job = self._job(job_id)
            trial = job.trials.get(trial_id)
            trace = trial.active.trace if trial and trial.attempts else None
            if trace is None:
                raise RuntimeJobsError(404, f"no accepted trace for trial {trial_id!r}")
            return trace

    def results(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._job(job_id)
            return self._results_locked(job)

    def _results_locked(self, job: _Job) -> dict[str, object]:
        return {
            "run_id": job.job_id, "state": job.state,
            "planned_trials": list(job.planned),
            "estimate": dict(job.estimate),
            # per-trial attempt HISTORY — the audit trail, not a destructive latest
            "trials": [
                {"trial_id": t.trial_id, "status": t.status,
                 "attempts": [a.record() for a in t.attempts]}
                for t in job.trials.values()
            ],
            # the accepted trace of each trial's active attempt; aggregates are
            # RECOMPUTED by Lab from these at bundle/publish time — never uploaded
            "traces": [t.active.trace for t in job.trials.values()
                       if t.attempts and t.active.trace is not None],
        }

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
            return {"run_id": job_id, "assignment": job.assignment,
                    "planned_trials": list(job.planned)}

    def append_events(self, job_id: str, trial_id: str, runtime_ref: str,
                      events: list[dict[str, object]],
                      batch_id: str | None = None) -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            self._reject_terminal(job)
            trial = self._trial(job, trial_id)
            att = trial.active
            if att.status != "running":
                # a finished attempt is immutable — reopen it with an explicit retry
                raise RuntimeJobsError(409, f"trial {trial_id!r} attempt is {att.status}; "
                                            "POST .../retry to open a new attempt")
            # idempotent by batch_id: a re-delivered batch is a no-op (a network
            # retry must not duplicate the ledger — review v0.3 idempotency)
            if batch_id is not None:
                if batch_id in att.seen_batches:
                    return {"trial_id": trial_id, "events": len(att.events),
                            "attempt": att.attempt_id, "idempotent": True}
                att.seen_batches.add(batch_id)
            att.events.extend(events)
            if job.state == "running":
                job.state = "receiving_traces"
            return {"trial_id": trial_id, "events": len(att.events), "attempt": att.attempt_id}

    def complete_trial(self, job_id: str, trial_id: str, runtime_ref: str,
                       trace: dict[str, object] | None, status: str = "completed",
                       failure: dict[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            self._reject_terminal(job)
            trial = self._trial(job, trial_id)
            att = trial.active
            if att.status in ("completed", "failed"):
                # immutable: re-completing a finished attempt is refused, not a
                # silent destructive replace (review v0.3-2). Retry is explicit.
                raise RuntimeJobsError(409, f"trial {trial_id!r} attempt already {att.status}; "
                                            "POST .../retry to open a new attempt")
            new_status = status if status in ("completed", "failed") else "completed"
            if new_status == "completed":
                # a completed trial MUST carry a conformant trace bound to its unit
                errors = _validate_trace(trace)
                if errors:
                    raise RuntimeJobsError(
                        422, "trace is not a conformant trace/v1: " + "; ".join(errors[:5]))
                assert isinstance(trace, dict)
                self._bind_unit(trial, trace)
                from lab_contracts import content_hash
                att.trace = trace
                att.trace_ref = content_hash(trace)
            else:  # failed
                if not isinstance(failure, dict) or not failure:
                    raise RuntimeJobsError(422, "a failed trial requires typed failure details")
                att.failure = failure
            att.status = new_status
            self._maybe_finish(job)
            return {"trial_id": trial_id, "status": att.status, "run_state": job.state,
                    "attempt": att.attempt_id, "trace_ref": att.trace_ref}

    def retry_trial(self, job_id: str, trial_id: str, runtime_ref: str) -> dict[str, object]:
        """Open a fresh TrialAttempt that SUPERSEDES the trial's prior one, keeping
        the prior attempt in the audit history (review v0.3-history). The run
        returns to `running`; any earlier finalization is invalidated."""
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            if job.state == "cancelled":
                raise RuntimeJobsError(409, "run is cancelled")
            trial = job.trials.get(trial_id)
            if trial is None or not trial.attempts:
                raise RuntimeJobsError(404, f"trial {trial_id!r} has no attempt to retry")
            prior = trial.active
            att = _Attempt(attempt_id=self._next("att"), supersedes=prior.attempt_id)
            trial.attempts.append(att)
            job.state = "running"
            return {"trial_id": trial_id, "attempt": att.attempt_id,
                    "supersedes": prior.attempt_id, "run_state": job.state}

    # -- internals --------------------------------------------------------
    def _job(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise RuntimeJobsError(404, f"unknown run {job_id!r}")
        return job

    def _require_owned(self, job_id: str, runtime_ref: str) -> _Job:
        job = self._job(job_id)
        if job.runtime_ref != runtime_ref:
            raise RuntimeJobsError(403, "this runtime does not own that run")
        return job

    def _reject_terminal(self, job: _Job) -> None:
        if job.state in _TERMINAL:
            raise RuntimeJobsError(409, f"run is {job.state} (terminal); no further ingest")

    def _trial(self, job: _Job, trial_id: str) -> _Trial:
        # fail-closed: only a PLANNED trial may be driven (when the run has a plan)
        if job.planned and trial_id not in job.planned:
            raise RuntimeJobsError(404, f"trial {trial_id!r} is not in the run's plan")
        trial = job.trials.get(trial_id)
        if trial is None:
            trial = _Trial(trial_id=trial_id, unit=job.units.get(trial_id))
            trial.attempts.append(_Attempt(attempt_id=self._next("att")))
            job.trials[trial_id] = trial
        return trial

    def _bind_unit(self, trial: _Trial, trace: dict[str, object]) -> None:
        # when the plan named a TrialUnit coordinate, the uploaded trace's `trial`
        # block MUST equal it — a runtime cannot report a trace for a different unit
        if trial.unit is None:
            return
        if trace.get("trial") != trial.unit:
            raise RuntimeJobsError(
                422, f"trace.trial does not match the assigned unit for {trial.trial_id!r}")

    def _maybe_finish(self, job: _Job) -> None:
        if not job.planned:
            # no explicit plan: a completed trial moves the run to analyzing (the
            # runtime signals overall completion out of band in this simple form)
            job.state = "analyzing"
            return
        statuses = {tid: (job.trials[tid].status if tid in job.trials else "pending")
                    for tid in job.planned}
        if all(s in ("completed", "failed") for s in statuses.values()):
            # every planned trial has a terminal attempt → the run is done
            job.state = "failed" if any(s == "failed" for s in statuses.values()) else "completed"


def plan_experiment(experiment: dict[str, object]) -> dict[str, object]:
    """Expand an `experiment/v1` into its planned trial units + a rough estimate
    (ui-backend-contract `/experiments/plan` → `{trials, estimate}`). A trial unit
    is one (scenario × condition × repeat); the plan is deterministic.

    FAIL-CLOSED (review v0.3-plan): the planner does NOT invent identifiers for a
    malformed experiment — an empty scenario/condition matrix or a non-positive
    `repeats` is rejected, so an unrunnable experiment never yields a plausible
    plan of fictional units. Raises RuntimeJobsError(400) on a bad matrix."""
    scenarios = [str(s) for s in (experiment.get("scenario_ids") or []) if s]
    conditions = experiment.get("condition_ids") or experiment.get("conditions") or []
    condition_ids = [
        str(c.get("condition_id") if isinstance(c, dict) else c)
        for c in conditions if c
    ]
    if not scenarios:
        raise RuntimeJobsError(400, "experiment names no scenario_ids")
    if not condition_ids:
        raise RuntimeJobsError(400, "experiment names no conditions")
    raw_repeats = experiment.get("repeats", 1)
    try:
        repeats = int(raw_repeats)
    except (TypeError, ValueError) as exc:
        raise RuntimeJobsError(400, f"repeats is not an integer: {raw_repeats!r}") from exc
    if repeats < 1:
        raise RuntimeJobsError(400, f"repeats must be >= 1, got {repeats}")
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
) -> ThreadingHTTPServer:
    """A threaded runtime-jobs server. `control_token`, if set, gates the control
    surface (runtime registration + run assignment); the runtime-facing endpoints
    are gated by the per-runtime ingest_key issued at connect."""
    jobs = store or RuntimeJobStore()

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

        def _send_sse(self, frames: list[tuple[str, dict[str, object]]]) -> None:
            # a snapshot event-stream: emit the run's current lifecycle state +
            # trial progress as text/event-stream frames, then close. A long-lived
            # push stream is the extension point; the frame format is already SSE
            # so a browser EventSource reads it unchanged.
            body = "".join(
                f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in frames
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bearer(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            return auth[7:] if auth.startswith("Bearer ") else None

        def _require_control(self) -> None:
            if control_token is not None and self._bearer() != control_token:
                raise RuntimeJobsError(401, "control token required")

        def _runtime_ref(self) -> str:
            key = self._bearer()
            ref = jobs.runtime_for_key(key) if key else None
            if ref is None:
                raise RuntimeJobsError(401, "a valid runtime ingest_key is required")
            return ref

        def _batch_id(self, body: dict[str, object]) -> str | None:
            # an Idempotency-Key header or a body batch_id makes an event POST
            # idempotent (a retried batch is a no-op)
            header = self.headers.get("Idempotency-Key")
            if header:
                return str(header)
            bid = body.get("batch_id")
            return str(bid) if bid is not None else None

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
                if self.path == "/runtimes":
                    self._require_control()
                    self._send(200, {"runtimes": jobs.list_runtimes()})
                    return
                if _RUNTIME_JOBS_RE.match(self.path):
                    ref = self._runtime_ref()
                    self._send(200, {"jobs": jobs.list_jobs(ref)})
                    return
                m = _RUN_RESULTS_RE.match(self.path)
                if m:
                    self._require_control()
                    self._send(200, jobs.results(m.group(1)))
                    return
                m = _RUN_TRACE_RE.match(self.path)
                if m:
                    self._require_control()
                    self._send(200, jobs.trial_trace(m.group(1), m.group(2)))
                    return
                m = _RUN_EVENTS_RE.match(self.path)
                if m:
                    self._require_control()
                    res = jobs.results(m.group(1))  # raises 404 for unknown run
                    self._send_sse([
                        ("state", {"run_id": res["run_id"], "state": res["state"]}),
                        ("trials", {"trials": res["trials"],
                                    "planned_trials": res["planned_trials"]}),
                    ])
                    return
                m = _RUN_RE.match(self.path)
                if m:
                    self._require_control()
                    self._send(200, {"run_id": m.group(1), "state": jobs.run_state(m.group(1))})
                    return
                self._send(404, {"error": "not found"})
            except RuntimeJobsError as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001 — never leak a traceback
                self._send(500, {"error": f"{type(exc).__name__}"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path == "/runtimes/connect":
                    self._require_control()
                    body = self._read_json()
                    self._send(201, jobs.connect_runtime(
                        model=str(body.get("model", "")),
                        agent_ref=body.get("agent_ref"),  # type: ignore[arg-type]
                    ))
                    return
                if self.path == "/scenarios/validate":
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
                if self.path == "/experiments/plan":
                    self._require_control()
                    body = self._read_json()
                    experiment = body.get("experiment")
                    if not isinstance(experiment, dict):
                        raise RuntimeJobsError(400, "plan requires {experiment}")
                    self._send(200, plan_experiment(experiment))
                    return
                if self.path == "/runs":
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
                m = _RUN_CONFIRM_RE.match(self.path)
                if m:
                    self._require_control()
                    self._send(200, jobs.confirm_run(m.group(1)))
                    return
                m = _RUN_RETRY_RE.match(self.path)
                if m:
                    ref = self._runtime_ref()
                    self._send(200, jobs.retry_trial(m.group(1), m.group(2), ref))
                    return
                m = _CLAIM_RE.match(self.path)
                if m:
                    ref = self._runtime_ref()
                    self._send(200, jobs.claim(m.group(1), ref))
                    return
                m = _EVENTS_RE.match(self.path)
                if m:
                    ref = self._runtime_ref()
                    body = self._read_json()
                    events = body.get("events", [])
                    if not isinstance(events, list):
                        raise RuntimeJobsError(400, "events must be a list")
                    self._send(200, jobs.append_events(
                        m.group(1), m.group(2), ref, events, batch_id=self._batch_id(body)))
                    return
                m = _TRIAL_DONE_RE.match(self.path)
                if m:
                    ref = self._runtime_ref()
                    body = self._read_json()
                    trace = body.get("trace")
                    failure = body.get("failure")
                    self._send(200, jobs.complete_trial(
                        m.group(1), m.group(2), ref,
                        trace if isinstance(trace, dict) else None,
                        status=str(body.get("status", "completed")),
                        failure=failure if isinstance(failure, dict) else None,
                    ))
                    return
                self._send(404, {"error": "not found"})
            except RuntimeJobsError as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": f"{type(exc).__name__}"})

    server = ThreadingHTTPServer((host, port), Handler)
    server.job_store = jobs  # type: ignore[attr-defined]
    return server
