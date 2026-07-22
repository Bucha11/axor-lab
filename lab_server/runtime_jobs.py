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
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_MAX_BODY = 8 * 1024 * 1024

_RUNTIME_JOBS_RE = re.compile(r"^/runtime/jobs$")
_CLAIM_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/claim$")
_EVENTS_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/events$")
_TRIAL_DONE_RE = re.compile(r"^/runtime/jobs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/complete$")
_RUN_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)$")
_RUN_RESULTS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/results$")
_RUN_EVENTS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/events$")
_RUN_CONFIRM_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/confirm$")
_RUN_AGG_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/aggregates$")
_RUN_TRACE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trials/([A-Za-z0-9_.:-]+)/trace$")


class RuntimeJobsError(Exception):
    """A bad runtime-jobs request; carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Trial:
    trial_id: str
    events: list[dict[str, object]] = field(default_factory=list)
    trace: dict[str, object] | None = None
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
            job.aggregates = list(aggregates)
            if job.state in ("running", "receiving_traces", "analyzing"):
                job.state = "completed"
            return {"run_id": job_id, "state": job.state, "aggregates": len(job.aggregates)}

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
        return {
            "run_id": job.job_id, "state": job.state,
            "planned_trials": list(job.planned),
            "estimate": dict(job.estimate),
            "trials": [
                {"trial_id": t.trial_id, "status": t.status, "attempt": t.attempt,
                 "superseded": t.superseded, "events": len(t.events),
                 "has_trace": t.trace is not None}
                for t in job.trials.values()
            ],
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
                      events: list[dict[str, object]]) -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
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
            if job.state == "running":
                job.state = "receiving_traces"
            return {"trial_id": trial_id, "events": len(trial.events), "attempt": trial.attempt}

    def complete_trial(self, job_id: str, trial_id: str, runtime_ref: str,
                       trace: dict[str, object] | None, status: str = "completed") -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            trial = job.trials.setdefault(trial_id, _Trial(trial_id=trial_id))
            new_status = status if status in ("completed", "failed") else "completed"
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
            self._maybe_finish(job)
            return {"trial_id": trial_id, "status": trial.status, "run_state": job.state,
                    "attempt": trial.attempt, "superseded": trial.superseded}

    # -- internals --------------------------------------------------------
    def _require_owned(self, job_id: str, runtime_ref: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise RuntimeJobsError(404, f"unknown run {job_id!r}")
        if job.runtime_ref != runtime_ref:
            raise RuntimeJobsError(403, "this runtime does not own that run")
        return job

    def _maybe_finish(self, job: _Job) -> None:
        # a job with no explicit plan finishes when the runtime says so (a later
        # complete-run call); with a plan, it finishes once every planned trial is done
        if not job.planned:
            job.state = "analyzing"
            return
        done = {tid for tid, t in job.trials.items() if t.status in ("completed", "failed")}
        if set(job.planned) <= done:
            job.state = "completed"


def plan_experiment(experiment: dict[str, object]) -> dict[str, object]:
    """Expand an `experiment/v1` into its planned trial units + a rough estimate
    (ui-backend-contract `/experiments/plan` → `{trials, estimate}`). A trial unit
    is one (scenario × condition × repeat); the plan is deterministic so the same
    experiment always yields the same trial ids. This is a PLAN, not execution —
    the runtime later runs each unit and pushes its trace."""
    scenarios = [str(s) for s in (experiment.get("scenario_ids") or []) if s]
    conditions = experiment.get("condition_ids") or experiment.get("conditions") or []
    condition_ids = [
        str(c.get("condition_id") if isinstance(c, dict) else c)
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
                m = _RUN_AGG_RE.match(self.path)
                if m:
                    self._require_control()
                    body = self._read_json()
                    aggregates = body.get("aggregates", [])
                    if not isinstance(aggregates, list):
                        raise RuntimeJobsError(400, "aggregates must be a list")
                    self._send(200, jobs.attach_aggregates(m.group(1), aggregates))
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
                    self._send(200, jobs.append_events(m.group(1), m.group(2), ref, events))
                    return
                m = _TRIAL_DONE_RE.match(self.path)
                if m:
                    ref = self._runtime_ref()
                    body = self._read_json()
                    trace = body.get("trace")
                    self._send(200, jobs.complete_trial(
                        m.group(1), m.group(2), ref,
                        trace if isinstance(trace, dict) else None,
                        status=str(body.get("status", "completed")),
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
