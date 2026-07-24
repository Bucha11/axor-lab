"""Runtime worker — the connected-runtime driver that actually EXECUTES runs.

architecture-boundary.md: **Lab assigns, the runtime executes.** The Lab
runtime-jobs API (lab_server/runtime_jobs.py) hands out experiment assignments
and collects pushed traces; it never runs an agent itself. This module is the
missing other half — the connected runtime. It:

  1. connects once (`POST /runtimes/connect`) → an `ingest_key`;
  2. polls `GET /runtime/jobs`, claims one (`POST /runtime/jobs/{id}/claim`),
     receiving the whole experiment assignment + its planned trial ids;
  3. runs the assignment LOCALLY through the reference runner
     (`run_experiment_suite`) with a deterministic scripted agent by default;
  4. pushes each trial's kernel events + finished trace back
     (`.../trials/{tid}/events`, `.../trials/{tid}/complete`); and
  5. posts the runner-computed aggregates (`POST /runs/{id}/aggregates`) so Lab
     finalizes the run at `completed`.

Trial-id alignment (critical): the Lab store's planned trial ids follow
`plan_experiment`'s deterministic scheme — one `f"{scenario}:{condition}:{repeat}"`
per (scenario × condition × repeat). The runner names its own trials by an
opaque run-scoped content hash (`trial_id_for`), which is a DIFFERENT scheme.
The worker therefore does NOT push the runner's internal ids; it re-keys every
executed trial onto the `scenario:condition:repeat` coordinate the plan uses, so
the ids the runtime completes match the ids Lab planned and the run can finish.

Transport is a thin stdlib-urllib client — the runtime speaks the HTTP protocol
directly and pulls in no server (or axor-wrap) code.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from lab_contracts.canonical import content_hash

from .agents import AgentAdapter
from .errors import RunnerError
from .experiment_file import resolve
from .runner import ExperimentResult, run_experiment_suite

DEFAULT_POLL_INTERVAL = 1.0
_HTTP_RETRIES = 3
_HTTP_BACKOFF = 0.25


class WorkerError(RunnerError):
    """The runtime worker could not drive a run (transport or protocol error)."""


def _planned_trial_id(scenario_id: str, condition_id: str, repeat_index: int) -> str:
    """The one canonical trial identity Lab plans and the runtime completes.

    Byte-identical to `plan_experiment`'s `f"{scenario}:{condition}:{i}"` — the
    single scheme both sides agree on (see the module docstring). The runner's
    own `trial_id_for` content hash is internal and never leaves the worker."""
    return f"{scenario_id}:{condition_id}:{repeat_index}"


def planned_trials(assignment: dict[str, object]) -> list[str]:
    """The planned trial ids for an assignment, in `plan_experiment` order and
    format. A run creator can pass these as `planned_trials` so the ids Lab
    expects are exactly the ones this worker will complete."""
    resolved = resolve(assignment)
    return [
        _planned_trial_id(str(scenario["name"]), str(condition["id"]), repeat_index)
        for scenario in resolved.scenarios
        for condition in resolved.conditions
        for repeat_index in range(resolved.repeats)
    ]


class _Client:
    """A minimal JSON-over-HTTP client for the runtime-jobs API. Runtime-facing
    calls carry the per-runtime `ingest_key`; the two control-surface calls the
    worker makes (connect, aggregates) carry the operator `control_token`."""

    def __init__(
        self, base_url: str, *, ingest_key: str | None = None,
        control_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.ingest_key = ingest_key
        self.control_token = control_token

    def _do(self, method: str, path: str, *, body: dict[str, object] | None,
            token: str | None) -> tuple[int, dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method,
        )
        last_exc: Exception | None = None
        for attempt in range(_HTTP_RETRIES):
            try:
                with urllib.request.urlopen(request) as response:  # noqa: S310 (operator URL)
                    raw = response.read()
                    return response.status, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as exc:
                # an HTTP status IS the answer (409 double-claim, 401, …) — surface
                # it to the caller rather than retrying a well-formed rejection
                raw = exc.read()
                try:
                    payload = json.loads(raw) if raw else {}
                except ValueError:
                    payload = {"error": raw.decode("utf-8", "replace")}
                return exc.code, payload
            except urllib.error.URLError as exc:  # a transport failure — retry
                last_exc = exc
                if attempt + 1 < _HTTP_RETRIES:
                    time.sleep(_HTTP_BACKOFF * (attempt + 1))
        raise WorkerError(f"cannot reach {self.base_url}{path}: {last_exc}")

    def runtime_get(self, path: str) -> dict[str, Any]:
        status, payload = self._do("GET", path, body=None, token=self.ingest_key)
        if status != 200:
            raise WorkerError(f"GET {path} -> {status}: {payload.get('error', payload)}")
        return payload

    def runtime_post(self, path: str, body: dict[str, object]) -> tuple[int, dict[str, Any]]:
        return self._do("POST", path, body=body, token=self.ingest_key)

    def control_post(self, path: str, body: dict[str, object]) -> tuple[int, dict[str, Any]]:
        return self._do("POST", path, body=body, token=self.control_token)


def connect(
    base_url: str, model: str = "scripted", control_token: str | None = None,
) -> dict[str, object]:
    """Register this runtime with Lab → `{runtime_ref, ingest_key}`. The
    ingest_key authorizes every runtime-facing pull/push afterwards."""
    client = _Client(base_url, control_token=control_token)
    status, payload = client.control_post("/runtimes/connect", {"model": model})
    if status != 201:
        raise WorkerError(f"connect -> {status}: {payload.get('error', payload)}")
    return payload


def _run_id_for(experiment: dict[str, object], agent_ref: str) -> str:
    """A stable run id for a deterministic scripted execution. It only scopes the
    runner's internal trial/trace ids; the trace ids pushed to Lab use the
    plan coordinate, so this value never has to agree with the server."""
    body = {"experiment": experiment, "agent": agent_ref}
    return "r_" + content_hash(body).removeprefix("sha256:")[:32]


def _push_trial(
    client: _Client, job_id: str, trial: dict[str, object],
    result: ExperimentResult,
) -> None:
    """Complete ONE trial on Lab, keyed by its plan coordinate. Streams the
    trial's kernel events first (when it produced a trace), then uploads the
    finished trace (or a status=failed marker for a trial that raised)."""
    plan_id = _planned_trial_id(
        str(trial["scenario_id"]), str(trial["condition_id"]),
        int(trial["repeat_index"]),
    )
    trace_ref = str(trial.get("trace_ref", ""))
    trace = result.traces.get(trace_ref) if trace_ref else None
    if trace is not None:
        events = trace.get("events")
        if isinstance(events, list) and events:
            code, payload = client.runtime_post(
                f"/runtime/jobs/{job_id}/trials/{plan_id}/events", {"events": events},
            )
            if code != 200:
                raise WorkerError(
                    f"events {plan_id} -> {code}: {payload.get('error', payload)}"
                )
    body: dict[str, object] = {"status": "completed" if trace is not None else "failed"}
    if trace is not None:
        body["trace"] = trace
    code, payload = client.runtime_post(
        f"/runtime/jobs/{job_id}/trials/{plan_id}/complete", body,
    )
    if code != 200:
        raise WorkerError(f"complete {plan_id} -> {code}: {payload.get('error', payload)}")


def process_job(
    client: _Client, job_id: str, *, agent: AgentAdapter | None = None,
) -> dict[str, object]:
    """Claim, execute, and finalize one job. Returns a small summary
    (`run_id`, `state`, counts) for the caller/tests."""
    code, claim = client.runtime_post(f"/runtime/jobs/{job_id}/claim", {})
    if code == 409:
        # someone else already claimed it (or it is not claimable) — not our job
        return {"run_id": job_id, "state": "skipped", "reason": claim.get("error")}
    if code != 200:
        raise WorkerError(f"claim {job_id} -> {code}: {claim.get('error', claim)}")

    assignment: dict[str, object] = claim.get("assignment", {})  # type: ignore[assignment]
    try:
        resolved = resolve(assignment)
    except RunnerError as exc:
        raise WorkerError(f"assignment for {job_id} is not a runnable experiment: {exc}") from exc
    run_agent = agent or resolved.agent
    run_id = _run_id_for(resolved.experiment, str(resolved.experiment.get("agent_ref", "")))

    result = run_experiment_suite(
        list(resolved.scenarios),
        resolved.manifests,
        list(resolved.conditions),
        resolved.kernel_registry,
        repeats=resolved.repeats,
        run_id=run_id,
        agent=run_agent,
    )

    for trial in result.trials:
        _push_trial(client, job_id, trial, result)

    # Lab RENDERS aggregates, it never computes them — the runner does, and the
    # worker posts them. Reuse the CLI's aggregate assembly (same statistics the
    # local `axor-lab run` reports) rather than reinventing it.
    from .cli import _aggregates

    aggregates = _aggregates(resolved, result, run_agent)
    code, payload = client.control_post(
        f"/runs/{job_id}/aggregates", {"aggregates": aggregates},
    )
    if code != 200:
        raise WorkerError(f"aggregates {job_id} -> {code}: {payload.get('error', payload)}")

    completed = sum(1 for t in result.trials if str(t.get("status")) == "completed")
    return {
        "run_id": job_id,
        "state": str(payload.get("state", "unknown")),
        "trials": len(result.trials),
        "completed": completed,
        "aggregates": len(aggregates),
    }


def serve(
    base_url: str, *,
    once: bool = False,
    control_token: str | None = None,
    agent: AgentAdapter | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    model: str = "scripted",
    connection: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Connect and drive assigned runs to completion.

    `once=True` processes at most one available job and returns (with an empty
    list when the queue is empty) — used by the CLI's single-shot mode and the
    tests. Otherwise it polls forever, claiming and executing jobs as they
    appear. Returns the summaries of the jobs it processed.

    `connection` reuses an existing `{runtime_ref, ingest_key}` from a prior
    `connect(...)` instead of registering a fresh runtime — the realistic order
    is connect once, then have runs assigned to that runtime_ref, then serve."""
    conn = connection or connect(base_url, model=model, control_token=control_token)
    client = _Client(
        base_url, ingest_key=str(conn["ingest_key"]), control_token=control_token,
    )
    processed: list[dict[str, object]] = []
    while True:
        listing = client.runtime_get("/runtime/jobs")
        jobs = [j for j in listing.get("jobs", []) if isinstance(j, dict)]
        claimed_one = False
        for job in jobs:
            job_id = str(job.get("job_id"))
            if not job_id:
                continue
            summary = process_job(client, job_id, agent=agent)
            if summary.get("state") == "skipped":
                continue  # lost the race — try the next visible job
            processed.append(summary)
            claimed_one = True
            if once:
                return processed
        if once:
            return processed
        if not claimed_one:
            time.sleep(poll_interval)
