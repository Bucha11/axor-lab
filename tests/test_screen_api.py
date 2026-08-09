"""Every screen has a named endpoint returning a schema-conforming payload.

That is Phase 3's exit criterion and ui-backend-contract.md's opening rule. The
server had six of the nine screens' endpoints and none of Home, Playground, Run
Report, Trial detail, EvidenceCase, Regression or Artifacts — so the contract
table read as a description of a server that did not exist, which is why that
document now states separately which half is implemented.

The second rule this pins is **rendered, never computed**: a screen displays
aggregates and verdicts the backend already stored and never recomputes a rate
or a threshold outcome. Two implementations of one statistic is how a published
number and a displayed number diverge, and the divergence shows up only after
someone has cited the wrong one.

Driven over real HTTP against the real handler, for the reason
`test_suite_entry_points.py` gives: an endpoint tested by calling the function
underneath it is an island with a longer bridge.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_contracts import validate_artifact
from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_server.screens import ScreenStore
from lab_suite import builtin_registry, run_suite

CONTROL = "control-token-for-tests"
CREATED = "2026-08-08T00:00:00+00:00"
ENVIRONMENT = {"model": {"provider": "scripted", "id": "t", "inference_params": {}}}


class ScreenApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = RuntimeJobStore()
        self.shelf = ScreenStore()
        self.server = make_runtime_server(
            port=0, control_token=CONTROL, store=self.jobs, screens=self.shelf,
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body: object = None,
             token: str | None = CONTROL) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class TestHomeIsAboutTheNextAction(ScreenApiTestCase):
    def test_a_fresh_workspace_is_told_to_connect_a_runtime(self) -> None:
        status, payload = self.call("GET", "/home")
        self.assertEqual(status, 200)
        self.assertEqual(payload["onboarding_step"], "connect_runtime")

    def test_a_connected_workspace_is_told_to_run_something(self) -> None:
        """The step is derived from what the workspace HAS, in the order the
        workflow needs it — not from a stored wizard position that can drift
        from reality."""
        self.call("POST", "/runtimes/connect", {"model": "gpt-4o"})
        _, payload = self.call("GET", "/home")
        self.assertEqual(payload["onboarding_step"], "run_a_suite")

    def test_it_offers_only_suites_that_exist(self) -> None:
        """The catalog marks three announced suites unavailable. Home is the
        launch surface: offering one there is offering a button that runs
        nothing."""
        _, payload = self.call("GET", "/home")
        self.assertTrue(payload["suites"])
        self.assertTrue(all(s["available"] for s in payload["suites"]))

    def test_it_counts_what_the_workspace_holds(self) -> None:
        _, payload = self.call("GET", "/home")
        self.assertEqual(payload["counts"]["evidence_cases"], 0)
        self.assertEqual(payload["counts"]["runs"], 0)


class TestThePlaygroundIsNotARun(ScreenApiTestCase):
    def test_one_trial_comes_back_with_its_trace(self) -> None:
        status, payload = self.call("POST", "/playground/trial", {"suite_id": "budget"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["trial"]["status"], "completed")
        self.assertEqual(payload["trace"]["schema_version"], "trace/v1")
        self.assertEqual(validate_artifact(payload["trace"], "trace"), [])

    def test_it_runs_exactly_one_trial_whatever_the_suite_declares(self) -> None:
        """`budget` declares repeats=5. A preview that silently ran the whole
        matrix would bill a debugging click as an experiment."""
        _, payload = self.call("POST", "/playground/trial", {"suite_id": "budget"})
        self.assertIsNotNone(payload["trial"])
        self.assertEqual(payload["trial"]["repeat_index"], 0)

    def test_it_says_out_loud_that_it_is_not_counted(self) -> None:
        """lifecycle.md: a Playground trial must never be counted into a Run's
        aggregates. Saying so in the payload, not only in the docs, is what
        stops a client storing it beside a Run's trials and corrupting the
        denominator."""
        _, payload = self.call("POST", "/playground/trial", {"suite_id": "budget"})
        self.assertIs(payload["counted_in_a_run"], False)

    def test_it_stores_nothing(self) -> None:
        self.call("POST", "/playground/trial", {"suite_id": "budget"})
        _, home = self.call("GET", "/home")
        self.assertEqual(home["counts"]["runs"], 0)

    def test_a_posted_manifest_previews_too(self) -> None:
        manifest = builtin_registry().get("budget").manifest()
        status, payload = self.call("POST", "/playground/trial", {"suite": manifest})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["trial"]["status"], "completed")

    def test_a_named_scenario_can_be_selected(self) -> None:
        manifest = builtin_registry().get("agentdojo").manifest()
        name = str(manifest["scenarios"][1]["name"])
        _, payload = self.call(
            "POST", "/playground/trial", {"suite": manifest, "scenario": name},
        )
        self.assertEqual(payload["trial"]["scenario_id"], name)

    def test_a_comparison_suite_previews_on_one_arm(self) -> None:
        """AgentDojo declares two arms and a `mcnemar` aggregation over them.
        Trimming the preview to one arm without trimming the aggregation
        produces a manifest the validator correctly refuses — and an aggregate
        over one trial is not a rate anyway."""
        manifest = builtin_registry().get("agentdojo").manifest()
        status, payload = self.call("POST", "/playground/trial", {"suite": manifest})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["trial"]["status"], "completed")

    def test_an_unknown_scenario_is_a_404_not_a_silent_first_one(self) -> None:
        status, _ = self.call(
            "POST", "/playground/trial", {"suite_id": "budget", "scenario": "nope"},
        )
        self.assertEqual(status, 404)

    def test_a_request_naming_no_suite_is_refused(self) -> None:
        status, _ = self.call("POST", "/playground/trial", {})
        self.assertEqual(status, 400)


class TestTheBuilderCanSave(ScreenApiTestCase):
    def _manifest(self) -> dict:
        return builtin_registry().get("budget").manifest()

    def test_a_suite_can_be_created(self) -> None:
        status, payload = self.call("POST", "/suites", {"suite": self._manifest()})
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["id"], "budget")

    def test_an_invalid_manifest_is_refused_before_storage(self) -> None:
        """A store that accepts a malformed document serves it back to a screen
        that cannot render it, and nobody can tell whether the producer or the
        renderer was wrong."""
        status, payload = self.call(
            "POST", "/suites", {"suite": {"schema_version": "suite/v1"}},
        )
        self.assertEqual(status, 422)
        self.assertIn("not conformant", payload["error"])

    def test_a_put_whose_id_disagrees_with_the_path_is_refused(self) -> None:
        """Saving it anyway silently creates a SECOND suite, and the Builder
        goes on editing the one nobody will run."""
        status, payload = self.call(
            "PUT", "/suites/something-else", {"suite": self._manifest()},
        )
        self.assertEqual(status, 400)
        self.assertIn("does not match", payload["error"])

    def test_a_put_that_matches_saves(self) -> None:
        status, _ = self.call("PUT", "/suites/budget", {"suite": self._manifest()})
        self.assertEqual(status, 200)

    def test_what_was_saved_is_what_comes_back(self) -> None:
        """The Builder loaded from the built-in registry and saved to the store,
        so a save reported success and the next load served the ORIGINAL
        document — the edit vanished with a green tick beside it. Reading and
        writing have to name the same place."""
        edited = {**self._manifest(), "description": "edited"}
        self.call("PUT", "/suites/budget", {"suite": edited})
        _, fetched = self.call("GET", "/suites/budget")
        self.assertEqual(fetched["description"], "edited")

    def test_the_catalog_shows_the_saved_name_not_the_builtin_one(self) -> None:
        self.call("PUT", "/suites/budget", {"suite": {**self._manifest(), "name": "Renamed"}})
        _, catalog = self.call("GET", "/suites")
        card = next(c for c in catalog["suites"] if c["id"] == "budget")
        self.assertEqual(card["name"], "Renamed")

    def test_a_suite_this_workspace_authored_appears_in_the_catalog(self) -> None:
        mine = {**self._manifest(), "id": "mine", "name": "Mine"}
        self.call("POST", "/suites", {"suite": mine})
        _, catalog = self.call("GET", "/suites")
        ids = [c["id"] for c in catalog["suites"]]
        self.assertIn("mine", ids)
        # appended after the built-ins and the announced placeholders, so the
        # catalog reads the same way it did before the workspace had any
        self.assertEqual(ids[-1], "mine")

    def test_the_yaml_view_serves_the_saved_document_too(self) -> None:
        from lab_suite import from_yaml

        self.call("PUT", "/suites/budget", {"suite": {**self._manifest(), "version": "9.9"}})
        request = urllib.request.Request(f"{self.base}/suites/budget/yaml")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            self.assertEqual(from_yaml(response.read().decode())["version"], "9.9")


class TestTheBuilderCanDispatch(ScreenApiTestCase):
    """`POST /suites/{id}/dispatch` — the Run button.

    Bring your agent on Integrations, pick it from the dropdown, run. Before
    this route existed the whole dispatch path (`build_assignment` →
    `POST /runs` → SSE) was reachable only from tests: the UI could author and
    validate a suite and then had no way to run it against anything.
    """

    def _connect(self) -> dict:
        _, connection = self.call("POST", "/runtimes/connect", {"model": "gpt-4o"})
        return connection

    def test_dispatch_binds_the_agent_and_creates_the_run(self) -> None:
        connection = self._connect()
        status, created = self.call(
            "POST", "/suites/budget/dispatch",
            {"runtime_ref": connection["runtime_ref"]},
        )
        self.assertEqual(status, 201, created)
        self.assertEqual(created["state"], "waiting_for_runtime")
        # budget: 1 scenario x 5 repeats x 1 (synthesized ungoverned) arm
        self.assertEqual(len(created["planned_trials"]), 5)

    def test_the_connected_agent_receives_the_executable_suite(self) -> None:
        """The runtime polls its jobs with the ingest key and must find the
        RESOLVED payload — scenario bodies and tool manifests, not references —
        because it has no access to Lab's registries."""
        connection = self._connect()
        _, created = self.call(
            "POST", "/suites/budget/dispatch",
            {"runtime_ref": connection["runtime_ref"]},
        )
        _, jobs = self.call("GET", "/runtime/jobs", token=connection["ingest_key"])
        self.assertIn(created["run_id"], [j["job_id"] for j in jobs["jobs"]])
        _, claimed = self.call(
            "POST", f"/runtime/jobs/{created['run_id']}/claim", {},
            token=connection["ingest_key"],
        )
        assignment = claimed["assignment"]
        self.assertEqual(assignment["suite_ref"], "budget")
        self.assertTrue(assignment["scenarios"][0]["task"])
        self.assertTrue(assignment["conditions"])

    def test_it_dispatches_the_saved_suite_not_the_builtin(self) -> None:
        """Save-then-run must run what is on screen. The Builder already had
        this bug once on the read side — the save vanished behind a green
        tick — so the dispatch side is pinned too."""
        manifest = builtin_registry().get("budget").manifest()
        manifest["execution"]["repeats"] = 2  # type: ignore[index]
        self.call("PUT", "/suites/budget", {"suite": manifest})
        connection = self._connect()
        _, created = self.call(
            "POST", "/suites/budget/dispatch",
            {"runtime_ref": connection["runtime_ref"]},
        )
        self.assertEqual(len(created["planned_trials"]), 2)

    def test_no_agent_chosen_is_a_400_that_says_where_agents_come_from(self) -> None:
        status, payload = self.call("POST", "/suites/budget/dispatch", {})
        self.assertEqual(status, 400)
        self.assertIn("Integrations", payload["error"])

    def test_an_unknown_agent_is_a_404_not_a_created_run(self) -> None:
        status, _ = self.call(
            "POST", "/suites/budget/dispatch", {"runtime_ref": "rt_gone"},
        )
        self.assertEqual(status, 404)

    def test_an_unknown_suite_is_a_404(self) -> None:
        connection = self._connect()
        status, _ = self.call(
            "POST", "/suites/no-such/dispatch",
            {"runtime_ref": connection["runtime_ref"]},
        )
        self.assertEqual(status, 404)

    def test_dispatch_is_gated_by_the_control_token(self) -> None:
        status, _ = self.call(
            "POST", "/suites/budget/dispatch", {"runtime_ref": "rt"}, token=None,
        )
        self.assertEqual(status, 401)


class TestEvidenceAndRegressionsAndArtifacts(ScreenApiTestCase):
    def _case(self) -> dict:
        return {
            "schema_version": "evidence-case/v1", "id": "EC-1",
            "kind": "latency_spike", "title": "A slow trial",
            "trial_ref": {"trial_id": "t1"}, "severity": "medium",
        }

    def _regression(self) -> dict:
        return {
            "schema_version": "regression/v1", "id": "RG-1",
            "name": "latency under 10s",
            "rule": {"kind": "metric_threshold", "metric": "duration_ms",
                     "op": "lt", "value": 10000},
        }

    def test_a_case_round_trips_through_the_api(self) -> None:
        status, created = self.call("POST", "/evidence", {"evidence_case": self._case()})
        self.assertEqual(status, 201, created)
        status, fetched = self.call("GET", f"/evidence/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched, self._case())

    def test_the_list_view_returns_rows_not_whole_documents(self) -> None:
        """A list endpoint returning whole documents makes the client decide
        what a row is, and two screens then disagree about it."""
        self.call("POST", "/evidence", {"evidence_case": self._case()})
        _, payload = self.call("GET", "/evidence")
        row = payload["evidence_cases"][0]
        self.assertEqual(set(row), {"id", "kind", "title", "severity", "trial_ref"})

    def test_a_regression_round_trips(self) -> None:
        status, created = self.call(
            "POST", "/regressions", {"regression": self._regression()},
        )
        self.assertEqual(status, 201, created)
        status, fetched = self.call("GET", f"/regressions/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["rule"]["metric"], "duration_ms")

    def test_an_artifact_is_keyed_by_artifact_id(self) -> None:
        suite = builtin_registry().get("budget")
        run = run_suite(suite.manifest(), run_id="r1", suite=suite)
        artifact = run.artifact("a_screen", CREATED, ENVIRONMENT)
        status, created = self.call("POST", "/artifacts", {"artifact": artifact})
        self.assertEqual(status, 201, created)
        self.assertEqual(created["id"], "a_screen")
        status, fetched = self.call("GET", "/artifacts/a_screen")
        self.assertEqual(status, 200)
        self.assertEqual(validate_artifact(fetched, "artifact"), [])

    def test_an_unknown_id_is_a_404(self) -> None:
        for path in ("/evidence/nope", "/regressions/nope", "/artifacts/nope"):
            with self.subTest(path=path):
                status, _ = self.call("GET", path)
                self.assertEqual(status, 404)

    def test_home_counts_them_once_stored(self) -> None:
        self.call("POST", "/evidence", {"evidence_case": self._case()})
        _, home = self.call("GET", "/home")
        self.assertEqual(home["counts"]["evidence_cases"], 1)
        self.assertEqual(home["onboarding_step"], "connect_runtime")


class TestTheRunScreens(ScreenApiTestCase):
    def _seeded_run(self) -> str:
        """A run with one completed trial, pushed the way a real runtime does."""
        connect = self.call("POST", "/runtimes/connect", {"model": "m"})[1]
        suite = builtin_registry().get("budget")
        local = run_suite(suite.manifest(), run_id="seed", suite=suite)
        trace = next(iter(local.traces.values()))
        experiment = {
            "schema_version": "experiment/v1", "id": "exp", "type": "benchmark",
            "scenario_ids": [str(trace["trial"]["scenario_id"])], "repeats": 1,
            "agent_ref": "scripted@0.6",
        }
        unit = (
            f"{trace['trial']['scenario_id']}:{trace['trial']['condition_id']}:"
            f"{trace['trial']['repeat_index']}"
        )
        run = self.call("POST", "/runs", {
            "experiment": experiment, "runtime_ref": connect["runtime_ref"],
            "planned_trials": [unit],
        })[1]
        self._ingest(connect["ingest_key"], run["run_id"], unit, trace)
        return str(run["run_id"])

    def _ingest(self, key: str, run_id: str, unit: str, trace: dict) -> None:
        self.call("POST", f"/runtime/jobs/{run_id}/claim", {}, token=key)
        self.call(
            "POST", f"/runtime/jobs/{run_id}/trials/{unit}/complete",
            {"trace": trace, "status": "completed",
             "metrics": {"duration_ms": 1.5, "steps": 4}}, token=key,
        )

    def test_the_report_states_coverage_before_aggregates(self) -> None:
        """statistics.md §5: a report showing aggregates without saying what
        fraction of the plan produced them invites reading a partial run as a
        complete one."""
        run_id = self._seeded_run()
        status, report = self.call("GET", f"/runs/{run_id}/report")
        self.assertEqual(status, 200, report)
        self.assertEqual(report["coverage"], {"completed": 1, "planned": 1})
        self.assertEqual(report["trials_by_status"], {"completed": 1})

    def test_the_report_renders_stored_aggregates_and_computes_none(self) -> None:
        run_id = self._seeded_run()
        _, report = self.call("GET", f"/runs/{run_id}/report")
        # nothing attached any, so there are none — the screen does not invent a
        # rate from the trials it can see
        self.assertEqual(report["aggregates"], [])

    def test_metric_coverage_counts_only_what_was_measured(self) -> None:
        run_id = self._seeded_run()
        _, report = self.call("GET", f"/runs/{run_id}/report")
        self.assertEqual(report["metric_coverage"]["duration_ms"], 1)
        self.assertNotIn("cost_usd", report["metric_coverage"])

    def test_trial_detail_joins_the_record_and_the_trace(self) -> None:
        run_id = self._seeded_run()
        results = self.call("GET", f"/runs/{run_id}/results")[1]
        trial_id = str(results["trials"][0]["trial_id"])
        status, detail = self.call("GET", f"/runs/{run_id}/trials/{trial_id}")
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["trial"]["trial_id"], trial_id)
        self.assertEqual(detail["trace"]["schema_version"], "trace/v1")

    def test_an_unknown_trial_is_a_404(self) -> None:
        run_id = self._seeded_run()
        status, _ = self.call("GET", f"/runs/{run_id}/trials/nope")
        self.assertEqual(status, 404)

    def test_a_stored_regression_runs_against_a_run(self) -> None:
        run_id = self._seeded_run()
        self.call("POST", "/regressions", {"regression": {
            "schema_version": "regression/v1", "id": "RG-lat",
            "name": "latency", "rule": {"kind": "metric_threshold",
                                        "metric": "duration_ms", "op": "lt",
                                        "value": 10000},
        }})
        status, result = self.call(
            "POST", "/regressions/RG-lat/run", {"run_id": run_id},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["trials_checked"], 1)

    def test_an_unevaluable_regression_reports_error_not_passed(self) -> None:
        """The cardinal rule, over HTTP this time: not-knowing is not knowing
        the invariant held."""
        run_id = self._seeded_run()
        self.call("POST", "/regressions", {"regression": {
            "schema_version": "regression/v1", "id": "RG-cost",
            "name": "cost", "rule": {"kind": "metric_threshold",
                                     "metric": "cost_usd", "op": "lt", "value": 1},
        }})
        _, result = self.call("POST", "/regressions/RG-cost/run", {"run_id": run_id})
        self.assertEqual(result["status"], "error")

    def test_evaluator_outcome_resolves_from_the_runs_own_suite(self) -> None:
        """The client sends only {run_id}; an evaluator_outcome rule names an
        evaluator declared by the suite, not the request. Defaulting the
        evaluator table from the run's own suite is what turns this from a
        permanent `error` into a real verdict — but only when the run carries
        its suite, i.e. it was dispatched."""
        connect = self.call("POST", "/runtimes/connect", {"model": "m"})[1]
        created = self.call(
            "POST", "/suites/budget/dispatch",
            {"runtime_ref": connect["runtime_ref"]})[1]
        run_id = created["run_id"]
        suite = builtin_registry().get("budget")
        local = run_suite(suite.manifest(), run_id="seed", suite=suite)
        by_unit = {
            f"{t['scenario_id']}:{t['condition_id']}:{t['repeat_index']}":
                local.traces[str(t["trace_ref"])]
            for t in local.trials if t.get("status") == "completed"
        }
        self.call("POST", f"/runtime/jobs/{run_id}/claim", {}, token=connect["ingest_key"])
        for unit in created["planned_trials"]:
            self.call(
                "POST", f"/runtime/jobs/{run_id}/trials/{unit}/complete",
                {"trace": by_unit[unit], "status": "completed",
                 "metrics": {"duration_ms": 1.5}}, token=connect["ingest_key"])
        # budget ships evaluator "read_count" (produces "reads"); expect 1
        self.call("POST", "/regressions", {"regression": {
            "schema_version": "regression/v1", "id": "RG-reads",
            "name": "one read", "rule": {"kind": "evaluator_outcome",
                                         "evaluator": "read_count", "expect": 1},
        }})
        status, result = self.call("POST", "/regressions/RG-reads/run", {"run_id": run_id})
        self.assertEqual(status, 200, result)
        self.assertNotEqual(result["status"], "error")
        self.assertEqual(result["status"], "passed")

    def _dispatch_and_finish(self, suite_id: str) -> str:
        connect = self.call("POST", "/runtimes/connect", {"model": "m"})[1]
        created = self.call(
            "POST", f"/suites/{suite_id}/dispatch",
            {"runtime_ref": connect["runtime_ref"]})[1]
        run_id = created["run_id"]
        suite = builtin_registry().get(suite_id)
        local = run_suite(suite.manifest(), run_id="seed", suite=suite)
        by_unit = {
            f"{t['scenario_id']}:{t['condition_id']}:{t['repeat_index']}":
                local.traces[str(t["trace_ref"])]
            for t in local.trials if t.get("status") == "completed"
        }
        self.call("POST", f"/runtime/jobs/{run_id}/claim", {}, token=connect["ingest_key"])
        for unit in created["planned_trials"]:
            self.call(
                "POST", f"/runtime/jobs/{run_id}/trials/{unit}/complete",
                {"trace": by_unit[unit], "status": "completed",
                 "metrics": {"duration_ms": 1.5}}, token=connect["ingest_key"])
        return run_id

    def test_a_finished_dispatched_run_produces_a_stored_artifact(self) -> None:
        """`completed` produces an Artifact (lifecycle.md). The collect step that
        builds it ran nowhere on the dispatched path, so the Artifacts screen
        stayed empty after a run whose whole output is a portable artifact."""
        self._dispatch_and_finish("budget")
        _, artifacts = self.call("GET", "/artifacts")
        self.assertEqual(len(artifacts["artifacts"]), 1)

    def test_a_finished_dispatched_run_stores_its_evidence_cases(self) -> None:
        """agentdojo raises prompt_injection cases; they were computed in-process
        and dropped over the wire, leaving the Evidence screen empty after a run
        with proven breaches."""
        self._dispatch_and_finish("agentdojo")
        _, evidence = self.call("GET", "/evidence")
        self.assertTrue(evidence["evidence_cases"])

    def test_a_finished_dispatched_run_persists_labs_recomputed_metrics(self) -> None:
        """The trial screen read the runtime's self-reported metrics; Lab's
        recomputation (budget's suite metric `reads`) is now persisted so the UI
        and regressions see the authoritative numbers."""
        run_id = self._dispatch_and_finish("budget")
        _, results = self.call("GET", f"/runs/{run_id}/results")
        completed = [t for t in results["trials"] if t["status"] == "completed"]
        self.assertTrue(all("reads" in t["metrics"] for t in completed))

    def test_running_a_regression_without_a_run_id_is_refused(self) -> None:
        self.call("POST", "/regressions", {"regression": {
            "schema_version": "regression/v1", "id": "RG-x", "name": "x",
            "rule": {"kind": "metric_threshold", "metric": "duration_ms",
                     "op": "lt", "value": 1},
        }})
        status, _ = self.call("POST", "/regressions/RG-x/run", {})
        self.assertEqual(status, 400)


class TestEveryScreenEndpointIsGated(ScreenApiTestCase):
    def test_none_of_them_answer_without_the_control_token(self) -> None:
        for method, path, body in (
            ("GET", "/home", None),
            ("GET", "/evidence", None),
            ("GET", "/regressions", None),
            ("GET", "/artifacts", None),
            ("GET", "/evidence/x", None),
            ("POST", "/evidence", {"evidence_case": {}}),
            ("POST", "/playground/trial", {"suite_id": "budget"}),
            ("PUT", "/suites/budget", {"suite": {}}),
        ):
            with self.subTest(path=f"{method} {path}"):
                status, _ = self.call(method, path, body, token=None)
                self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
