"""GET /runs/{id}/bundle — assembling a PUBLISHABLE bundle from a completed run.

This closes the free-researcher loop: after the runtime worker drives a run to
`completed` (traces + aggregates collected), Lab must be able to ASSEMBLE a
bundle/v1 from that run and that bundle must PUBLISH — i.e. clear the publish
handshake's replay + statistical recomputation, the exact verification the real
`POST /api/publications` runs before minting. If the reconstructed bundle's
provenance or traces were wrong, publish would refuse it; a passing publish is the
proof the assembly is correct (not just schema-shaped).

Lab assigns, the runtime executes: the server never runs an agent here — it
reconstructs the bundle from the assignment + the traces the runtime pushed back.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from lab_contracts import validate_artifact
from lab_runner import worker
from lab_server import make_runtime_server, plan_experiment
from lab_server.store import PublicationStore

from tests import support


def _experiment_document(repeats: int = 3) -> dict[str, object]:
    """A self-contained `.axl` assignment: the experiment/v1 block plus the full
    scenario + tool-manifest bodies the runtime needs to actually run (and Lab
    needs to reconstruct the bundle)."""
    conditions = support.conditions()
    scenario = support.banking_scenario()
    experiment = {
        "schema_version": "experiment/v1",
        "id": "exp_bundle_01",
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


def _planned(document: dict[str, object]) -> list[str]:
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

    def _drive_to_completed(self, document: dict[str, object]) -> str:
        """Assign the experiment, run the worker once, assert it reached completed,
        return the run_id."""
        conn = worker.connect(self.base, model="scripted", control_token=self.control_token)
        planned = _planned(document)
        run = self.store.create_run(conn["runtime_ref"], document, planned=planned)
        run_id = run["run_id"]
        processed = worker.serve(
            self.base, once=True, control_token=self.control_token, connection=conn,
        )
        self.assertEqual(len(processed), 1, processed)
        self.assertEqual(processed[0]["state"], "completed", processed)
        return run_id


class TestRunBundleEndpoint(_ServerBase):
    def test_bundle_from_completed_run_is_a_valid_bundle_with_traces(self) -> None:
        document = _experiment_document(repeats=3)
        run_id = self._drive_to_completed(document)

        status, payload = self._req("GET", f"/runs/{run_id}/bundle", token="ctl")
        self.assertEqual(status, 200, payload)
        bundle = payload["bundle"]
        traces = payload["traces"]

        # the bundle is schema-valid …
        self.assertEqual(validate_artifact(bundle, "bundle"), [], "bundle must be schema-valid")
        self.assertEqual(bundle["schema_version"], "bundle/v1")
        # … carries non-empty traces, keyed by trace_id (the shape publish expects) …
        self.assertTrue(traces, "traces must not be empty")
        for trace_id, trace in traces.items():
            self.assertEqual(trace_id, trace["trace_id"])
            self.assertEqual(validate_artifact(trace, "trace"), [])
        # … and every completed trial is bound to a trace with recomputed aggregates
        self.assertTrue(bundle["trials"])
        self.assertTrue(bundle["aggregates"])
        for trial in bundle["trials"]:
            self.assertEqual(trial["status"], "completed", trial)
        # provenance is DERIVED from the trials (reconstructed at build time on the
        # server, which never observed execution) — not asserted recorded_at_execution
        prov = bundle["environment"]["config_provenance"]
        self.assertEqual(prov["provenance_status"], "reconstructed_legacy", prov)

    def test_assembled_bundle_actually_publishes(self) -> None:
        """The load-bearing acceptance: the bundle assembled from the run PASSES the
        real publish handshake (content-hash verify + bit-identical replay +
        server-side statistical recomputation) — the same store.publish that backs
        POST /api/publications. A wrong reconstruction (bad trials/traces/provenance)
        would be refused here."""
        document = _experiment_document(repeats=4)
        run_id = self._drive_to_completed(document)

        _, payload = self._req("GET", f"/runs/{run_id}/bundle", token="ctl")
        bundle = payload["bundle"]
        traces = payload["traces"]

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = PublicationStore(root=Path(tmp.name))
        stored = store.publish(
            bundle=bundle, traces=traces,
            question="Does content-ledger governance reduce ASR on banking-exfil?",
            visibility="unlisted",
        )
        pid = stored.publication["publication_id"]
        self.assertTrue(pid, "publish must mint a publication id")
        self.assertEqual(stored.publication["origin"], "local")
        # the publication is retrievable and unlisted (the backend's safe default)
        self.assertEqual(store.get(pid).publication["visibility"], "unlisted")

    def test_bundle_before_completion_is_409(self) -> None:
        document = _experiment_document(repeats=2)
        conn = worker.connect(self.base, model="scripted", control_token=self.control_token)
        planned = _planned(document)
        run = self.store.create_run(conn["runtime_ref"], document, planned=planned)
        # a run that has not run yet (waiting_for_runtime) has no evidence to bundle
        status, payload = self._req("GET", f"/runs/{run['run_id']}/bundle", token="ctl")
        self.assertEqual(status, 409, payload)
        self.assertIn("not completed", payload["error"])

    def test_bundle_unknown_run_is_404(self) -> None:
        status, payload = self._req("GET", "/runs/run_9999_deadbeef/bundle", token="ctl")
        self.assertEqual(status, 404, payload)

    def test_bundle_requires_control_token(self) -> None:
        status, _ = self._req("GET", "/runs/run_0001_x/bundle")  # no token
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
