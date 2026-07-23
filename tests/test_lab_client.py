"""LabRuntimeClient + a REAL AgentAdapter — actual agent connection, end-to-end.

A runtime registers with Lab (axlab_ token); its Lab client polls + claims a job and,
for each assigned trial, RUNS THE AGENT LOCALLY through the real axor-core kernel
(`lab_runner.run_trial` via `RunnerAgentAdapter`) — the agent decides, the kernel
governs, provenance is built — then uploads the GENUINE trace it produced. Lab binds
each trace to its assigned unit and builds Results. No canned traces, no Control
Plane (agent-connection.md).
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tests import support
from lab_client import LabRuntimeClient, RunnerAgentAdapter, run_job_loop
from lab_runner import ScriptedAgent
from lab_server import make_runtime_server


class TestRealAgentConnection(unittest.TestCase):
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

    def _local_runtime(self):
        """The runtime side: it holds the scenarios + kernel + agent LOCALLY (Lab
        never executes the agent). A deterministic ScriptedAgent that always follows
        the injection makes the governed vs ungoverned outcomes stable to assert."""
        scenario = support.banking_scenario()
        conditions = support.conditions()  # ungoverned + governed
        adapter = RunnerAgentAdapter(
            scenarios={str(scenario["name"]): scenario},
            manifests=support.manifests(),
            conditions={str(c["id"]): c for c in conditions},
            kernel_registry=support.kernel_registry(),
            agent=ScriptedAgent(attack_rate=1.0),  # always attacks → deterministic
        )
        return scenario, conditions, adapter

    def test_runtime_runs_the_agent_locally_and_lab_builds_results(self) -> None:
        scenario, conditions, adapter = self._local_runtime()

        # Lab registers the runtime (mints the axlab_ token) and assigns the run.
        conn = self._control("POST", "/runtimes/connect", {"model": "scripted-agent"})
        axlab_token = conn["ingest_key"]
        experiment = {
            "scenario_ids": [str(scenario["name"])],
            "condition_ids": [str(c["id"]) for c in conditions],
            "repeats": 3,
            # Lab keeps the scenario defs so it can recompute ASR from the traces
            "scenarios": [scenario],
        }
        run = self._control("POST", "/runs",
                            {"runtime_ref": conn["runtime_ref"], "experiment": experiment})
        run_id = run["run_id"]
        self.assertEqual(len(run["planned_trials"]), 6)  # 1 scenario × 2 conditions × 3

        # the runtime's Lab client drives the loop: it RUNS each trial locally
        # through the real kernel and uploads the genuine trace it produced. The
        # adapter is attached to the CLIENT (adapters.md §10), not the other way.
        client = LabRuntimeClient(self.base, axlab_token, adapter=adapter)
        ran = asyncio.run(client.run_job_loop(max_jobs=1))
        self.assertEqual(ran, 1)

        # Lab bound every uploaded trace to its unit and built Results ITSELF
        results = self._control("GET", f"/runs/{run_id}/results")
        self.assertEqual(results["state"], "completed")
        self.assertEqual(len(results["traces"]), 6)
        self.assertTrue(all(t["status"] == "completed" for t in results["trials"]))
        # the adapter's events reached Lab through the trace_sink egress (not a
        # direct adapter HTTP call) — every trial has streamed kernel events
        self.assertTrue(all(a["events"] > 0 for t in results["trials"] for a in t["attempts"]))

        # the traces are REAL governed executions: each carries a gate_decision the
        # local kernel actually made (not a canned trace)
        verdicts = {
            ev["decision"]["verdict"]
            for tr in results["traces"] for ev in tr["events"]
            if ev.get("type") == "gate_decision"
        }
        self.assertTrue(verdicts <= {"ALLOW", "DENY"})
        self.assertIn("DENY", verdicts)  # the governed condition blocked the attack

        # Lab's own ASR aggregates: the ungoverned attack succeeds, governed is lower
        aggs = {a["condition_id"]: a for a in results["aggregates"]}
        self.assertEqual(set(aggs), {c["id"] for c in conditions})
        gov = next(c["id"] for c in conditions if str(c["enforcement"]) == "on")
        ungov = next(c["id"] for c in conditions if str(c["enforcement"]) == "off")
        self.assertEqual(aggs[ungov]["n"], 3)
        self.assertGreaterEqual(aggs[ungov]["estimate"], aggs[gov]["estimate"])

    def test_bogus_axlab_token_cannot_pull_jobs(self) -> None:
        bad = LabRuntimeClient(self.base, "not-a-real-token")
        with self.assertRaises(Exception) as ctx:
            bad.poll_job()
        self.assertEqual(getattr(ctx.exception, "status", None), 401)


if __name__ == "__main__":
    unittest.main()
