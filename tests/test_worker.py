"""Runtime worker — the connected-runtime driver, end-to-end against a REAL server.

Lab assigns, the runtime executes: these tests stand up an in-process
runtime-jobs server, assign a small real banking experiment to a connected
runtime, then run `worker.serve(once=True)` against it. The worker must claim the
job, execute it locally through the reference runner, push every trial's trace
keyed by the plan coordinate, post the aggregates, and drive the run to
`completed` — the whole Builder -> Run -> Results path the store otherwise hangs
on at waiting_for_runtime.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_runner import worker
from lab_server import make_runtime_server, plan_experiment

from tests import support


def _experiment_document(repeats: int = 2) -> dict[str, object]:
    """A self-contained .axl assignment: a valid experiment/v1 block plus the
    full scenario + tool-manifest bodies the runtime needs to actually run."""
    conditions = support.conditions()
    scenario = support.banking_scenario()
    experiment = {
        "schema_version": "experiment/v1",
        "id": "exp_worker_01",
        "type": "benchmark",
        "scenario_ids": [str(scenario["name"])],
        "conditions": conditions,
        "repeats": repeats,
        "agent_ref": "scripted@0.6",
        "run_mode": "compare",
    }
    return {
        "experiment": experiment,
        "scenarios": [scenario],
        "tool_manifests": list(support.manifests().values()),
    }


def _planned_from_plan_experiment(document: dict[str, object]) -> list[str]:
    """The planned ids exactly as Lab's `plan_experiment` computes them, fed the
    scenario names + condition ids as strings (its canonical inputs)."""
    experiment: dict[str, object] = document["experiment"]  # type: ignore[assignment]
    conditions: list[dict[str, object]] = experiment["conditions"]  # type: ignore[assignment]
    return plan_experiment({
        "scenario_ids": experiment["scenario_ids"],
        "condition_ids": [str(c["id"]) for c in conditions],
        "repeats": experiment["repeats"],
    })["trials"]


class _ServerBase(unittest.TestCase):
    control_token: str | None = "ctl"

    def setUp(self) -> None:
        self.server: ThreadingHTTPServer = make_runtime_server(
            host="127.0.0.1", port=0, control_token=self.control_token,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.store = self.server.job_store  # type: ignore[attr-defined]

    def _req(self, method: str, path: str, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class TestWorkerEndToEnd(_ServerBase):
    def _assign_run(self, document: dict[str, object]):
        """Connect this runtime (via the worker's own connect) and assign the
        experiment as a run whose planned trial ids are Lab's own
        `plan_experiment` output. Returns (run_id, planned, connection)."""
        conn = worker.connect(self.base, model="scripted", control_token=self.control_token)
        planned = _planned_from_plan_experiment(document)
        run = self.store.create_run(conn["runtime_ref"], document, planned=planned)
        return run["run_id"], planned, conn

    def test_serve_once_drives_run_to_completed_with_traces_and_aggregates(self) -> None:
        document = _experiment_document(repeats=2)
        run_id, planned, conn = self._assign_run(document)

        # the worker claims, executes locally, and pushes everything back
        processed = worker.serve(
            self.base, once=True, control_token=self.control_token, connection=conn,
        )
        self.assertEqual(len(processed), 1, processed)
        self.assertEqual(processed[0]["run_id"], run_id)
        self.assertEqual(processed[0]["state"], "completed")

        # the run is finalized and its results carry traces + non-empty aggregates
        status, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        self.assertEqual(status, 200, results)
        self.assertEqual(results["state"], "completed")
        self.assertGreaterEqual(len(results["traces"]), 1)
        self.assertTrue(results["aggregates"], "aggregates must not be empty")

        # every completed trial carries a real trace/v1
        for trial in results["trials"]:
            self.assertEqual(trial["status"], "completed", trial)
            self.assertTrue(trial["has_trace"], trial)

    def test_pushed_trial_ids_match_the_planned_scheme(self) -> None:
        document = _experiment_document(repeats=2)
        run_id, planned, conn = self._assign_run(document)

        # the worker's own planned-id helper agrees with plan_experiment — one scheme
        self.assertEqual(set(worker.planned_trials(document)), set(planned))

        worker.serve(self.base, once=True, control_token=self.control_token, connection=conn)

        _, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        completed_ids = {t["trial_id"] for t in results["trials"]}
        # exactly the planned coordinates came back — no id drift between the
        # runner's internal ids and what Lab planned
        self.assertEqual(completed_ids, set(planned))
        # a 1 scenario x 2 conditions x 2 repeats plan is 4 trials
        self.assertEqual(len(planned), 4)

    def test_aggregates_carry_asr_and_utility(self) -> None:
        document = _experiment_document(repeats=3)
        run_id, _, conn = self._assign_run(document)
        worker.serve(self.base, once=True, control_token=self.control_token, connection=conn)

        _, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        metrics = {a["metric"] for a in results["aggregates"]}
        self.assertIn("ASR", metrics)
        self.assertIn("task_success_rate", metrics)
        # each aggregate has a Wilson interval and an n
        for aggregate in results["aggregates"]:
            self.assertIn("interval", aggregate)
            self.assertGreater(aggregate["n"], 0)


class TestWorkerNoJobs(_ServerBase):
    def test_serve_once_with_no_jobs_returns_cleanly(self) -> None:
        # a runtime is available but nothing is assigned — once=True exits with an
        # empty result rather than hanging on the poll loop
        processed = worker.serve(self.base, once=True, control_token=self.control_token)
        self.assertEqual(processed, [])


if __name__ == "__main__":
    unittest.main()
