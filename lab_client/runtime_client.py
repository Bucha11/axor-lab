"""LabRuntimeClient — the Lab-side runtime job loop (agent-connection.md).

Outbound-only, stdlib-only (`urllib`). It reaches **Lab** with an `axlab_` token and
a Lab URL — a separate client from any Control Plane `PlaneClient`. Lab assigns; this
client claims a job, runs each trial through the `AgentAdapter` locally, and uploads
the resulting trace. It never dispatches a tool or lets Lab call the agent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .adapter import AgentAdapter, AgentInput, ExecutionContext, TraceSink


class LabRuntimeError(Exception):
    """A LabRuntimeClient request failed; carries the HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class LabRuntimeClient:
    """A runtime's outbound client to a Lab backend.

    `base_url` is the Lab URL (e.g. https://lab.useaxor.net); `token` is the `axlab_`
    runtime token issued by Lab's Runtime Registry at connect — NOT a Control Plane
    token and not a shared all-powerful token (agent-connection.md).
    """

    def __init__(self, base_url: str, token: str, *, adapter: AgentAdapter | None = None,
                 timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        # the adapter is attached to the client OUTSIDE the adapter itself
        # (adapters.md §10: `LabRuntimeClient(url, token, adapter=adapter)`); the
        # adapter knows nothing about Lab, the client drives it.
        self.adapter = adapter
        self.timeout = timeout

    # -- HTTP ------------------------------------------------------------
    def _request(self, method: str, path: str, body: object | None = None) -> tuple[int, object]:
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:  # a structured Lab error
            try:
                payload = json.loads(e.read() or b"null")
            except ValueError:
                payload = None
            raise LabRuntimeError(e.code, str((payload or {}).get("error", e.reason))) from None

    # -- runtime job protocol -------------------------------------------
    def poll_job(self) -> dict[str, object] | None:
        """Return the next assignable job, or None when idle."""
        _, out = self._request("GET", "/runtime/jobs")
        jobs = out.get("jobs", []) if isinstance(out, dict) else []
        return jobs[0] if jobs else None

    def claim(self, job_id: str) -> dict[str, object]:
        """Claim a job → its assignment + the assigned TrialUnits (with coordinates)."""
        _, out = self._request("POST", f"/runtime/jobs/{job_id}/claim", {})
        return out  # type: ignore[return-value]

    def upload_events(self, job_id: str, trial_id: str,
                      events: list[dict[str, object]], batch_id: str | None = None) -> None:
        body: dict[str, object] = {"events": events}
        if batch_id is not None:
            body["batch_id"] = batch_id
        self._request("POST", f"/runtime/jobs/{job_id}/trials/{trial_id}/events", body)

    def complete_trial(self, job_id: str, trial_id: str,
                       trace: dict[str, object] | None, status: str = "completed",
                       failure: dict[str, object] | None = None) -> dict[str, object]:
        body: dict[str, object] = {"status": status}
        if trace is not None:
            body["trace"] = trace
        if failure is not None:
            body["failure"] = failure
        _, out = self._request("POST", f"/runtime/jobs/{job_id}/trials/{trial_id}/complete", body)
        return out  # type: ignore[return-value]

    def request_retry(self, job_id: str, trial_id: str, reason: str = "") -> dict[str, object]:
        """Ask Lab to re-run a trial (advisory only — Lab decides; retry itself is a
        control action, agent-connection.md / v0.3-retry)."""
        _, out = self._request(
            "POST", f"/runtime/jobs/{job_id}/trials/{trial_id}/retry-request", {"reason": reason})
        return out  # type: ignore[return-value]

    async def run_job_loop(self, adapter: AgentAdapter | None = None, *,
                           max_jobs: int | None = None) -> int:
        """Drive the runtime job loop with the attached (or passed) adapter."""
        return await run_job_loop(self, adapter or self.adapter, max_jobs=max_jobs)


async def run_one_job(client: LabRuntimeClient, adapter: AgentAdapter,
                      job: dict[str, object]) -> dict[str, object]:
    """Claim `job`, run every assigned trial through the adapter locally, and upload
    each trial's events + trace. Returns the last complete response.

    For each trial the client BUILDS the ExecutionContext (adapters.md §10): the
    assigned coordinate, a thin `condition` ref, and a fresh append-only `TraceSink`.
    The adapter runs locally and emits its events into the sink — the ONLY egress;
    the client then ships those events and completes with the produced trace, which
    is bound to the assigned unit."""
    job_id = str(job["job_id"])
    claim = client.claim(job_id)
    assignment = claim.get("assignment", {})
    task = str(assignment.get("task", "")) if isinstance(assignment, dict) else ""
    inputs = assignment.get("inputs", {}) if isinstance(assignment, dict) else {}
    last: dict[str, object] = {}
    for unit in claim.get("units", []):  # type: ignore[union-attr]
        await adapter.reset()  # clear the AGENT's state between trials (adapters.md §9)
        trial_id = str(unit["trial_id"])
        coordinate = {k: v for k, v in unit.items() if k != "trial_id"}
        sink = TraceSink()
        ctx = ExecutionContext(
            condition={"condition_id": coordinate.get("condition_id")},
            trace_sink=sink, run_id=str(claim.get("run_id", job_id)),
            trial_id=trial_id, trial=coordinate)
        result = await adapter.run(AgentInput(task=task, inputs=dict(inputs)), ctx)
        if result.status != "completed":
            last = client.complete_trial(
                job_id, trial_id, None, status="failed",
                failure={"kind": "agent_failed", "output": repr(result.output)})
            continue
        # ship the events the adapter emitted through the sink, then finalize
        if sink.events:
            client.upload_events(job_id, trial_id, sink.events, batch_id=trial_id)
        last = client.complete_trial(job_id, trial_id, result.trace, status="completed")
    return last


async def run_job_loop(client: LabRuntimeClient, adapter: AgentAdapter | None = None, *,
                       max_jobs: int | None = None) -> int:
    """Drive the runtime job loop: poll → claim → run trials → upload, until no job
    remains (or `max_jobs` processed). Returns the number of jobs run. Lab assigns,
    the runtime executes locally; the loop calls no tool and never lets Lab call the
    agent (agent-connection.md)."""
    adapter = adapter or client.adapter
    if adapter is None:
        raise LabRuntimeError(0, "no adapter attached to the client or passed")
    ran = 0
    while max_jobs is None or ran < max_jobs:
        job = client.poll_job()
        if job is None:
            break
        await run_one_job(client, adapter, job)
        ran += 1
    return ran
