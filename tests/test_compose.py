"""`GET /catalog` + `POST /experiments/compose` — a builder over REAL scenarios.

The builder used to invent its own menu client-side: four suites with fabricated
scenario ids (`banking-01`…`banking-07`, `slack-*`, `travel-*`). None existed, so
nothing it composed could run. These tests pin the fix from both ends: the menu
comes from the code that owns the scenarios, and everything the menu offers
composes into a document that actually executes.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_server import make_runtime_server
from lab_server.compose import (
    BASELINE_CONDITION,
    DEFAULT_REPEATS,
    MAX_REPEATS,
    MIN_POWERED_REPEATS,
    REFERENCE_KERNEL,
    ComposeRefused,
    catalogue,
    compose,
)


class CatalogueTest(unittest.TestCase):
    def test_every_offered_scenario_actually_exists(self) -> None:
        from lab_adapters import import_suite

        cat = catalogue()
        self.assertTrue(cat["suites"])
        for suite in cat["suites"]:
            real = {str(s["name"]) for s in import_suite(suite["id"])}
            offered = {s["name"] for s in suite["scenarios"]}
            self.assertEqual(offered, real)
            # and each carries the task text a chooser needs to tell them apart
            for scenario in suite["scenarios"]:
                self.assertTrue(scenario["task"].strip(), scenario)

    def test_the_baseline_is_marked_as_such(self) -> None:
        cat = catalogue()
        baselines = [c["id"] for c in cat["conditions"] if c["baseline"]]
        self.assertEqual(baselines, [BASELINE_CONDITION])

    def test_it_offers_a_kernel_that_is_always_present(self) -> None:
        # A composed run must be runnable. Pinning a real axor-core build would
        # make it fail whenever the installed core differs — which is exactly what
        # `import-agentdojo`'s axor-core@0.4.2 pin does.
        self.assertEqual(catalogue()["kernel"], REFERENCE_KERNEL)
        self.assertNotIn("axor-core@", REFERENCE_KERNEL)

    def test_the_agent_is_declared_free_and_deterministic(self) -> None:
        agent = catalogue()["agent"]
        self.assertTrue(agent["deterministic"])
        self.assertIn("scripted", agent["ref"])

    def test_repeats_advertises_the_power_floor(self) -> None:
        repeats = catalogue()["repeats"]
        self.assertEqual(repeats["default"], DEFAULT_REPEATS)
        self.assertEqual(repeats["max"], MAX_REPEATS)
        self.assertEqual(repeats["min_powered"], MIN_POWERED_REPEATS)
        self.assertLessEqual(repeats["min_powered"], repeats["default"])


class ComposeTest(unittest.TestCase):
    def test_a_bare_suite_choice_composes_and_resolves(self) -> None:
        # One decision in, a runnable experiment out — that is the whole point of
        # the simple level of the builder.
        result = compose({"suite": "banking"})
        experiment = result["document"]["experiment"]
        self.assertEqual(experiment["repeats"], DEFAULT_REPEATS)
        self.assertEqual(
            [c["id"] for c in experiment["conditions"]],
            [BASELINE_CONDITION, "governed"],
        )
        self.assertEqual(
            result["estimate"]["trials"], len(result["planned_trials"])
        )

    def test_every_catalogued_suite_composes(self) -> None:
        for suite in catalogue()["suites"]:
            with self.subTest(suite=suite["id"]):
                composed = compose({"suite": suite["id"]})
                self.assertEqual(
                    composed["estimate"]["scenarios"], len(suite["scenarios"])
                )

    def test_dropping_the_baseline_is_refused_not_patched(self) -> None:
        # Silently adding the baseline back would hand the user a comparison they
        # did not ask for; running without one would hand them a delta with
        # nothing to be a delta against.
        with self.assertRaises(ComposeRefused) as caught:
            compose({"suite": "banking", "conditions": ["governed"]})
        self.assertEqual(caught.exception.status, 400)
        self.assertIn(BASELINE_CONDITION, caught.exception.message)

    def test_an_unknown_condition_is_refused(self) -> None:
        with self.assertRaises(ComposeRefused) as caught:
            compose({"suite": "banking", "conditions": [BASELINE_CONDITION, "yolo"]})
        self.assertEqual(caught.exception.status, 400)

    def test_an_unknown_suite_is_a_404(self) -> None:
        with self.assertRaises(ComposeRefused) as caught:
            compose({"suite": "slack"})
        self.assertEqual(caught.exception.status, 404)

    def test_repeats_are_bounded(self) -> None:
        for bad in (0, -1, MAX_REPEATS + 1):
            with self.subTest(repeats=bad), self.assertRaises(ComposeRefused) as caught:
                compose({"suite": "banking", "repeats": bad})
            self.assertEqual(caught.exception.status, 400)


class ComposedRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server: ThreadingHTTPServer = make_runtime_server(
            host="127.0.0.1", port=0, control_token=None,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _call(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_catalog_is_served(self) -> None:
        status, cat = self._call("/catalog")
        self.assertEqual(status, 200)
        self.assertEqual({s["id"] for s in cat["suites"]}, {"banking"})

    def test_compose_and_run_in_one_round_trip(self) -> None:
        # The builder's Run button is one call: compose the selection, execute it.
        status, run = self._call(
            "/runs/local", {"compose": {"suite": "banking", "repeats": DEFAULT_REPEATS}},
        )
        self.assertEqual(status, 201, run)
        self.assertEqual(run["state"], "completed")

        status, results = self._call(f"/runs/{run['run_id']}/results")
        self.assertEqual(status, 200)
        asr = {a["condition_id"]: a for a in results["aggregates"] if a["metric"] == "ASR"}
        # governance did something, and at the default repeats the paired test is
        # actually conclusive — a default that publishes no p-value is a dead end
        self.assertGreater(asr[BASELINE_CONDITION]["estimate"], 0.0)
        self.assertEqual(asr["governed"]["estimate"], 0.0)
        self.assertEqual(asr["governed"]["test"]["name"], "mcnemar")
        self.assertEqual(asr["governed"]["test"]["status"], "conclusive")

    def test_compose_errors_surface_with_their_status(self) -> None:
        status, body = self._call(
            "/runs/local", {"compose": {"suite": "banking", "conditions": ["governed"]}},
        )
        self.assertEqual(status, 400)
        self.assertIn(BASELINE_CONDITION, body["error"])

    def test_compose_endpoint_returns_a_document_without_running_it(self) -> None:
        status, body = self._call("/experiments/compose", {"suite": "banking"})
        self.assertEqual(status, 200)
        self.assertEqual(body["document"]["experiment"]["type"], "benchmark")
        self.assertGreater(body["estimate"]["trials"], 0)


if __name__ == "__main__":
    unittest.main()
