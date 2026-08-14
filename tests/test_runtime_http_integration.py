"""The connected-runtime path over REAL HTTP, end to end.

Every other test of this path talks to `RuntimeJobStore` in process. That is
fast and it masked three genuine bugs, because an in-process harness lets the
test hand Lab data the wire never carries. This one starts the actual
runtime-jobs server, drives it with a client that speaks only the documented
protocol, and asserts on what comes out the other side.

The client here is a local stand-in for axor-wrap's `LabRuntimeConnector`
(`poll → claim → post events → complete`), so this suite has no dependency on
that repo being checked out beside this one. It was written against the real
connector and matches its request shapes.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_capabilities.governance import gate_for_condition
from lab_contracts import validate_artifact
from lab_runner.loop import Finish, ScriptedProgram, ToolCall, run_loop_trial
from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_suite import assign_suite, builtin_registry, collect_suite_run

CONTROL = "control-token-for-tests"
CREATED = "2026-08-03T00:00:00+00:00"


class _Connector:
    """Speaks the runtime-jobs protocol over HTTP, and nothing else."""

    def __init__(self, base_url: str, control_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._control_token = control_token
        self.runtime_ref: str | None = None
        self.ingest_key: str | None = None

    def connect(self, runtime_label: str = "", agent_ref: str | None = None) -> dict[str, object]:
        body: dict[str, object] = {"runtime_label": runtime_label}
        if agent_ref is not None:
            body["agent_ref"] = agent_ref
        payload = self._request("POST", "/runtimes/connect", body, token=self._control_token)
        self.runtime_ref = str(payload["runtime_ref"])
        self.ingest_key = str(payload["ingest_key"])
        return payload

    def poll_jobs(self) -> list[dict[str, object]]:
        payload = self._request("GET", "/runtime/jobs", token=self.ingest_key)
        return list(payload.get("jobs", []))  # type: ignore[arg-type]

    def claim(self, job_id: str) -> dict[str, object]:
        return self._request("POST", f"/runtime/jobs/{job_id}/claim", {},
                             token=self.ingest_key)

    def post_events(self, job_id: str, trial_id: str,
                    events: list[dict[str, object]]) -> dict[str, object]:
        return self._request("POST", f"/runtime/jobs/{job_id}/trials/{trial_id}/events",
                             {"events": events}, token=self.ingest_key)

    def complete_trial(self, job_id: str, trial_id: str,
                       trace: dict[str, object] | None, status: str = "completed",
                       metrics: dict[str, object] | None = None) -> dict[str, object]:
        body: dict[str, object] = {"trace": trace, "status": status}
        if metrics:
            body["metrics"] = metrics
        return self._request("POST", f"/runtime/jobs/{job_id}/trials/{trial_id}/complete",
                             body, token=self.ingest_key)

    def _request(self, method: str, path: str, body: dict[str, object] | None = None,
                 token: str | None = None) -> dict[str, object]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return json.loads(response.read() or b"{}")


class RuntimeHttpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RuntimeJobStore()
        self.server = make_runtime_server(port=0, control_token=CONTROL, store=self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _connected(self) -> _Connector:
        connector = _Connector(self.base, control_token=CONTROL)
        connector.connect(runtime_label="gpt-4o", agent_ref="acme/support-bot")
        return connector

    def _execute(self, connector: _Connector, job_id: str, claimed: dict[str, object],
                 *, send_metrics: bool = True) -> None:
        scenarios = {str(s["name"]): s for s in claimed["assignment"]["scenarios"]}  # type: ignore[index,union-attr]
        manifests = {str(m["id"]): m for m in claimed["assignment"]["tool_manifests"]}  # type: ignore[index,union-attr]
        arms = {str(c["id"]): c for c in claimed["assignment"].get("conditions", [])}  # type: ignore[index,union-attr]
        for unit in claimed["planned_trials"]:  # type: ignore[union-attr]
            scenario_id, condition_id, index = str(unit).rsplit(":", 2)
            condition = arms.get(condition_id, {
                "schema_version": "condition/v1", "id": condition_id,
                "enforcement": "off",
            })
            # a wrapped runtime gates through the kernel even with enforcement
            # OFF — ungoverned is not unwrapped
            gate = gate_for_condition(
                condition, manifests, scenarios[scenario_id].get("inputs", {}),
            )
            outcome = run_loop_trial(
                scenarios[scenario_id], manifests, condition, gate,
                "runtime", f"s{int(index):03d}", int(index),
                ScriptedProgram([ToolCall("read_txns", {}), Finish("summary")]),
            )
            trace = dict(outcome.trace)
            trace["trial"] = {**trace["trial"], "scenario_id": scenario_id,
                              "condition_id": condition_id, "repeat_index": int(index)}
            connector.post_events(job_id, str(unit), list(trace["events"]))  # type: ignore[arg-type]
            connector.complete_trial(
                job_id, str(unit), trace,
                metrics=outcome.metrics if send_metrics else None,
            )


class TestFullRoundTrip(RuntimeHttpTestCase):
    def _run_suite_over_http(self, *, send_metrics: bool = True):
        connector = self._connected()
        suite = builtin_registry().get("budget")
        assignment = assign_suite(
            suite.manifest(), str(connector.runtime_ref), self.store,
        )
        jobs = connector.poll_jobs()
        self.assertEqual(len(jobs), 1)
        job_id = str(jobs[0]["job_id"])
        claimed = connector.claim(job_id)
        self._execute(connector, job_id, claimed, send_metrics=send_metrics)
        return assignment, collect_suite_run(assignment, self.store)

    def test_the_suite_reaches_the_runtime_and_the_results_come_back(self) -> None:
        assignment, run = self._run_suite_over_http()
        self.assertEqual(len(run.trials), 5)
        self.assertEqual({str(t["status"]) for t in run.trials}, {"completed"})
        self.assertEqual(self.store.run_state(assignment.run_id), "completed")

    def test_the_assignment_crosses_the_wire_intact(self) -> None:
        """The runtime must receive executable bodies, not references it cannot
        resolve — it has no access to Lab's registries."""
        connector = self._connected()
        suite = builtin_registry().get("budget")
        assign_suite(suite.manifest(), str(connector.runtime_ref), self.store)
        claimed = connector.claim(str(connector.poll_jobs()[0]["job_id"]))
        assignment: dict[str, object] = claimed["assignment"]  # type: ignore[assignment]
        self.assertTrue(assignment["scenarios"])
        self.assertTrue(assignment["tool_manifests"])
        self.assertEqual(len(claimed["planned_trials"]), 5)  # type: ignore[arg-type]

    def test_lab_evaluates_the_predicates_itself(self) -> None:
        """task_success is Lab's verdict over the returned trace, not something
        the runtime reports. A runtime grading its own homework would put an
        unverifiable claim into a published artifact.

        This is the bug the in-process tests hid: dispatch never evaluated the
        scenario's predicates, so no rate aggregate existed on this path at all.
        """
        _, run = self._run_suite_over_http()
        metrics: dict[str, object] = run.trials[0]["metrics"]  # type: ignore[assignment]
        self.assertIn("task_success", metrics)
        rates = [a for a in run.aggregates if str(a["metric"]) == "task_success"]
        self.assertEqual(len(rates), 1)
        self.assertEqual(int(rates[0]["n"]), 5)

    def test_runtime_measurements_arrive_and_feed_the_invariants(self) -> None:
        """duration_ms can only come from the machine that ran the trial."""
        assignment, run = self._run_suite_over_http()
        self.assertIn("duration_ms", run.trials[0]["metrics"])  # type: ignore[operator]
        by_id = {
            str(regression["id"]): result
            for regression, result in zip(assignment.resolved.regressions, run.invariants)
        }
        self.assertEqual(by_id["RG-budget-latency"].status, "passed")

    def test_without_reported_metrics_the_latency_invariant_errors(self) -> None:
        """Lab does not time a run on someone else's machine, and will not
        invent a number it did not observe — so the invariant reports that it
        could not be evaluated rather than passing."""
        assignment, run = self._run_suite_over_http(send_metrics=False)
        self.assertNotIn("duration_ms", run.trials[0]["metrics"])  # type: ignore[operator]
        by_id = {
            str(regression["id"]): result
            for regression, result in zip(assignment.resolved.regressions, run.invariants)
        }
        self.assertEqual(by_id["RG-budget-latency"].status, "error")

    def test_the_collected_run_packages_as_a_valid_artifact(self) -> None:
        _, run = self._run_suite_over_http()
        artifact = run.artifact(
            "art_http", CREATED, {"model": {"provider": "byo", "id": "acme/support-bot"}},
        )
        self.assertEqual(validate_artifact(artifact, "artifact"), [])
        # the ungoverned arm still ran THROUGH the kernel, so its recorded
        # verdicts replay bit-identically — one of the concrete things a wrapped
        # ungoverned run buys that an unwrapped one cannot
        self.assertEqual(
            artifact["reproduce"]["reproducibility"], "exact_replay",  # type: ignore[index]
        )


class TestAuthorizationOverTheWire(RuntimeHttpTestCase):
    def test_an_unregistered_runtime_cannot_poll(self) -> None:
        connector = _Connector(self.base, control_token=CONTROL)
        connector.ingest_key = "not-a-real-key"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            connector.poll_jobs()
        self.assertIn(ctx.exception.code, (401, 403))

    def test_assignment_requires_the_control_token(self) -> None:
        connector = _Connector(self.base, control_token=None)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            connector.connect(runtime_label="x")
        self.assertIn(ctx.exception.code, (401, 403))

    def test_one_runtime_cannot_claim_anothers_job(self) -> None:
        """Assignments are per-runtime; a second runtime must not see them."""
        first = self._connected()
        second = self._connected()
        suite = builtin_registry().get("budget")
        assign_suite(suite.manifest(), str(first.runtime_ref), self.store)
        self.assertEqual(second.poll_jobs(), [])


if __name__ == "__main__":
    unittest.main()
