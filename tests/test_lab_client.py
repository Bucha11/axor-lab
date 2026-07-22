"""LabRuntimeClient + AgentAdapter — the Lab-side runtime job loop end-to-end.

A runtime registers with Lab (axlab_ token), the client polls + claims a job, runs
each trial through an AgentAdapter locally, and uploads the trace; Lab binds it to
the assigned unit and builds Results. No Control Plane involved (agent-connection.md).
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from lab_client import (
    AgentDescription,
    AgentRunResult,
    LabRuntimeClient,
    run_job_loop,
)
from lab_server import make_runtime_server

_TRACE: list[dict] = []
EXP = {"scenario_ids": ["s1"], "conditions": ["c1"], "repeats": 1}


def _fixture_trace() -> dict:
    if not _TRACE:
        from tests import support
        from lab_runner import run_experiment_suite
        r = run_experiment_suite([support.banking_scenario()], support.manifests(),
                                 support.conditions(), support.kernel_registry(),
                                 repeats=1, run_id="rt")
        _TRACE.extend(r.traces.values())
    return json.loads(json.dumps(_TRACE[0]))


class ScriptedAdapter:
    """A custom AgentAdapter (the required custom-agent scenario): returns a
    conformant trace stamped with the assigned unit coordinate."""

    def __init__(self) -> None:
        self.resets = 0

    async def describe(self) -> AgentDescription:
        return AgentDescription(name="scripted", adapter_kind="custom", models=["none"])

    async def reset(self) -> None:
        self.resets += 1

    async def run(self, input, execution_context) -> AgentRunResult:  # noqa: A002
        trace = _fixture_trace()
        trace["trial"] = dict(execution_context.trial)  # stamp the assigned unit
        return AgentRunResult(output="done", trace=trace, status="completed")


class TestLabRuntimeClient(unittest.TestCase):
    def setUp(self) -> None:
        self.server: ThreadingHTTPServer = make_runtime_server(
            host="127.0.0.1", port=0, control_token="ctl")
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _control(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": "Bearer ctl", "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_run_job_loop_end_to_end(self) -> None:
        # Lab registers the runtime (issues the axlab_ token) and assigns a run
        conn = self._control("POST", "/runtimes/connect", {"model": "scripted"})
        axlab_token = conn["ingest_key"]
        run = self._control("POST", "/runs",
                            {"runtime_ref": conn["runtime_ref"], "experiment": EXP})
        run_id = run["run_id"]

        # the runtime's Lab client drives the job loop with its axlab_ token
        client = LabRuntimeClient(self.base, axlab_token)
        adapter = ScriptedAdapter()
        ran = asyncio.run(run_job_loop(client, adapter, max_jobs=1))
        self.assertEqual(ran, 1)
        self.assertEqual(adapter.resets, 1)

        # Lab bound the uploaded trace to the assigned unit and built Results itself
        results = self._control("GET", f"/runs/{run_id}/results")
        self.assertEqual(results["state"], "completed")
        self.assertEqual(len(results["traces"]), 1)
        self.assertEqual(results["trials"][0]["status"], "completed")

    def test_client_uses_a_scoped_token_not_control(self) -> None:
        # the client's axlab_ token gates ONLY the runtime surface; it is not the
        # control token (two separate credential scopes)
        conn = self._control("POST", "/runtimes/connect", {})
        client = LabRuntimeClient(self.base, conn["ingest_key"])
        # a runtime token can poll the runtime surface...
        self.assertIsNone(client.poll_job())  # no jobs yet, but authorized (no error)
        # ...and a bogus token cannot
        bad = LabRuntimeClient(self.base, "not-a-real-token")
        with self.assertRaises(Exception) as ctx:
            bad.poll_job()
        self.assertEqual(getattr(ctx.exception, "status", None), 401)


if __name__ == "__main__":
    unittest.main()
