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
  POST /runs                 assign an experiment to a runtime -> { run_id, state }
  GET  /runs/{id}            -> { state }  (a lifecycle state)
  GET  /runs/{id}/results    -> { trials: [...] }  (collected so far)

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


@dataclass
class _Job:
    job_id: str
    runtime_ref: str
    assignment: dict[str, object]
    planned: tuple[str, ...]
    state: str = "waiting_for_runtime"
    trials: dict[str, _Trial] = field(default_factory=dict)


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
                   planned: list[str] | None = None) -> dict[str, object]:
        with self._lock:
            if runtime_ref not in self._runtimes:
                raise RuntimeJobsError(404, f"unknown runtime_ref {runtime_ref!r}")
            job_id = self._next("run")
            plan = tuple(str(t) for t in (planned or experiment.get("planned_trials", []) or []))
            self._jobs[job_id] = _Job(job_id=job_id, runtime_ref=runtime_ref,
                                      assignment=dict(experiment), planned=plan)
            return {"run_id": job_id, "state": "waiting_for_runtime"}

    def run_state(self, job_id: str) -> str:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            return job.state

    def results(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeJobsError(404, f"unknown run {job_id!r}")
            return {
                "run_id": job_id, "state": job.state,
                "planned_trials": list(job.planned),
                "trials": [
                    {"trial_id": t.trial_id, "status": t.status,
                     "events": len(t.events), "has_trace": t.trace is not None}
                    for t in job.trials.values()
                ],
                "traces": [t.trace for t in job.trials.values() if t.trace is not None],
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
                      events: list[dict[str, object]]) -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            trial = job.trials.setdefault(trial_id, _Trial(trial_id=trial_id))
            if trial.status != "pending":
                raise RuntimeJobsError(409, f"trial {trial_id!r} already {trial.status}")
            trial.events.extend(events)
            if job.state == "running":
                job.state = "receiving_traces"
            return {"trial_id": trial_id, "events": len(trial.events)}

    def complete_trial(self, job_id: str, trial_id: str, runtime_ref: str,
                       trace: dict[str, object] | None, status: str = "completed") -> dict[str, object]:
        with self._lock:
            job = self._require_owned(job_id, runtime_ref)
            trial = job.trials.setdefault(trial_id, _Trial(trial_id=trial_id))
            trial.trace = trace
            trial.status = status if status in ("completed", "failed") else "completed"
            self._maybe_finish(job)
            return {"trial_id": trial_id, "status": trial.status, "run_state": job.state}

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
                if self.path == "/runs":
                    self._require_control()
                    body = self._read_json()
                    experiment = body.get("experiment")
                    runtime_ref = body.get("runtime_ref")
                    if not isinstance(experiment, dict) or not isinstance(runtime_ref, str):
                        raise RuntimeJobsError(400, "runs require {runtime_ref, experiment}")
                    self._send(201, jobs.create_run(
                        runtime_ref, experiment,
                        planned=body.get("planned_trials"),  # type: ignore[arg-type]
                    ))
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
