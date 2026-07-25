"""`POST /runs/local` — a first result without a runtime, a CLI, or a provider.

The bundled example is fully offline (scripted agent, reference kernel, simulated
tools), so the server can execute it directly. The run lands as an ordinary
COMPLETED job, which is what makes every downstream surface — results, bundle
assembly, publish — work over it unchanged.

Two things these tests pin hardest:
  * the UI-triggered run is the SAME run the CLI produces, byte for byte;
  * a run that would cost money is refused, not attempted.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from lab_contracts import content_hash, verify_bundle
from lab_server import make_runtime_server
from lab_server.local_run import MAX_LOCAL_TRIALS, LocalRunRefused, load_example, run_local


class _Base(unittest.TestCase):
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
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


class LocalRunEndpointTest(_Base):
    def test_runs_the_example_and_lands_a_completed_run(self) -> None:
        status, run = self._call("/runs/local", {})
        self.assertEqual(status, 201, run)
        self.assertEqual(run["state"], "completed")
        # not a connected runtime — the record says who executed it
        self.assertEqual(run["executed"], "local")
        self.assertGreater(run["trials"], 0)
        self.assertEqual(run["by_status"], {"completed": run["trials"]})
        # missingness is reported, denominator honesty before any aggregate
        self.assertIn("n=", run["missingness"])

    def test_results_and_bundle_read_like_any_other_run(self) -> None:
        _, run = self._call("/runs/local", {})
        run_id = run["run_id"]

        status, results = self._call(f"/runs/{run_id}/results")
        self.assertEqual(status, 200)
        self.assertEqual(results["state"], "completed")
        self.assertEqual(len(results["traces"]), run["trials"])
        metrics = {(a["metric"], a["condition_id"]) for a in results["aggregates"]}
        self.assertIn(("ASR", "governed"), metrics)
        self.assertIn(("ASR", "ungoverned"), metrics)

        status, payload = self._call(f"/runs/{run_id}/bundle")
        self.assertEqual(status, 200)
        verify_bundle(payload["bundle"], payload["traces"])  # publishable as-is

    def test_the_bundle_keeps_execution_time_provenance(self) -> None:
        # A bundle RECONSTRUCTED from collected traces has to recompute each
        # trial's runtime_config_hash and is marked reconstructed_legacy, which
        # the evidence export refuses. A run executed here recorded it, so the
        # bundle must not come back through the weaker path.
        _, run = self._call("/runs/local", {})
        _, payload = self._call(f"/runs/{run['run_id']}/bundle")
        for trial in payload["bundle"]["trials"]:
            self.assertEqual(trial["runtime_provenance"], "recorded_at_execution")

    def test_governance_actually_changed_the_outcome(self) -> None:
        # Not a smoke test: the example exists to show enforcement working, so a
        # run that reports no difference between the arms is a broken run.
        _, run = self._call("/runs/local", {})
        _, results = self._call(f"/runs/{run['run_id']}/results")
        asr = {a["condition_id"]: a["estimate"]
               for a in results["aggregates"] if a["metric"] == "ASR"}
        self.assertGreater(asr["ungoverned"], 0.0)
        self.assertEqual(asr["governed"], 0.0)

    def test_a_malformed_experiment_is_a_clean_422(self) -> None:
        status, body = self._call("/runs/local", {"experiment": {"nope": 1}})
        self.assertEqual(status, 422)
        self.assertIn("not a runnable experiment", body["error"])

    def test_a_non_object_experiment_is_a_400(self) -> None:
        status, _ = self._call("/runs/local", {"experiment": "banking"})
        self.assertEqual(status, 400)


class LocalRunParityTest(unittest.TestCase):
    def test_it_is_the_same_run_the_cli_produces(self) -> None:
        """Identity, not just similarity.

        The scripted agent is deterministic, so the run id is content-derived and
        a UI-triggered run must be indistinguishable from `axor-lab run` over the
        same file. If this ever diverges, the UI has quietly become a second,
        differently-behaving runner.
        """
        from lab_contracts import build_bundle
        from lab_runner.bundle_io import PACKAGING
        from lab_runner.cli import _aggregates, _derive_run_id, _environment
        from lab_runner.experiment_file import resolve
        from lab_runner.runner import run_experiment_suite

        document = load_example()
        resolved = resolve(document)
        agent_ref = str(resolved.experiment["agent_ref"])
        cli_run_id = _derive_run_id(None, resolved.experiment, agent_ref, deterministic=True)
        cli_result = run_experiment_suite(
            list(resolved.scenarios), resolved.manifests, list(resolved.conditions),
            resolved.kernel_registry, repeats=resolved.repeats,
            run_id=cli_run_id, agent=resolved.agent,
        )
        cli_bundle = build_bundle(
            bundle_id=f"b_{cli_run_id}",
            created="2026-01-01T00:00:00+00:00",  # the one field that is wall-clock
            scenarios=list(resolved.scenarios),
            conditions=list(resolved.conditions),
            tool_manifests=list(resolved.manifests.values()),
            environment=_environment(resolved, agent_ref, agent=resolved.agent),
            trials=cli_result.trials,
            aggregates=_aggregates(resolved, cli_result, resolved.agent),
            traces=cli_result.traces,
            packaging=dict(PACKAGING),
        )

        # `created` is the only wall-clock field, and it feeds the bundle's content
        # hashes — so it is pinned on both sides rather than patched afterwards.
        local = run_local(document, created=str(cli_bundle["created"]))
        self.assertEqual(local["run_id"], cli_run_id)
        # traces travel beside the bundle (trials reference them by ref)
        self.assertEqual(content_hash(local["traces"]), content_hash(cli_result.traces))

        # Compare the WHOLE bundle, so a future divergence anywhere in it fails
        # here rather than in whichever field this test happened to enumerate.
        self.assertEqual(content_hash(local["bundle"]), content_hash(cli_bundle))
        local_bundle = local["bundle"]
        # and the provenance build_bundle derives is the execution-time one
        self.assertEqual(
            local_bundle["environment"]["config_provenance"]["provenance_status"],
            "recorded_at_execution",
        )

    def test_the_bundle_replays_bit_identically(self) -> None:
        from lab_runner.kernel import default_registry
        from lab_runner.replay import replay_bundle

        local = run_local(load_example())
        bundle, traces = local["bundle"], local["traces"]
        versions = tuple(str(c["kernel"]) for c in bundle["conditions"])
        kernels = {k.version: k for k in default_registry(versions).kernels}
        report = replay_bundle(bundle, traces, kernels)
        self.assertTrue(report.bit_identical)
        self.assertEqual(len(report.decisions), len(traces))


class LocalRunGuardTest(unittest.TestCase):
    def test_a_model_backed_run_is_refused_not_attempted(self) -> None:
        """A browser must not be able to make the server spend money.

        The refusal is the design, not a gap: cost ceilings and the
        estimate-confirm gate live in the CLI, and nothing in an HTTP body can be
        trusted to bound a provider bill. The guard fires immediately after
        resolution, before any trial runs — so a stub resolution is enough to
        reach it, and reaching anything past it would itself be the bug.
        """
        class _LiveAgent:
            is_deterministic = False

        class _Resolved:
            agent = _LiveAgent()

        with mock.patch("lab_runner.experiment_file.resolve", return_value=_Resolved()):
            with self.assertRaises(LocalRunRefused) as caught:
                run_local(load_example())
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("live model", caught.exception.message)

    def test_an_oversized_suite_is_refused(self) -> None:
        document = load_example()
        document["experiment"]["repeats"] = MAX_LOCAL_TRIALS * 10
        with self.assertRaises(LocalRunRefused) as caught:
            run_local(document)
        self.assertEqual(caught.exception.status, 413)
        self.assertIn("ceiling", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
