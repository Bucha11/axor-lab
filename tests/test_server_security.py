"""Server security fixes (review P0.4, P0.5, §7).

- a hostile trace_id cannot write outside traces/ (path traversal);
- unlisted publications are NEVER in the catalog, private is never served;
- token-gated writes reject unauthenticated publish/attest/takedown;
- a locally tampered stored file is not trusted on reload.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server import PublishRejected, make_server
from lab_server.store import PublicationStore

CREATED = "2026-07-19T12:00:00+00:00"


def _bundle_and_traces(visibility_traces_ok: bool = True):
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=6, run_id="r_sec",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", 0, len(pairs), test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_sec", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestPathTraversal(unittest.TestCase):
    def test_hostile_trace_id_cannot_escape_traces_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_root = root / "store"
            store = PublicationStore(root=store_root)

            # a single-trace bundle whose trace_id is a path-traversal payload
            scenario = support.banking_scenario()
            conditions = support.conditions()
            result = run_experiment_suite(
                [scenario], support.manifests(), conditions, support.kernel_registry(),
                repeats=1, run_id="r_evil",
            )
            from lab_contracts import content_hash

            trace = next(iter(result.traces.values()))
            trace["trace_id"] = "../../../../etc/pwned"
            traces = {trace["trace_id"]: trace}
            # the trial must bind to THIS trace's own coordinates (graph verifier)
            tt = trace["trial"]
            # rebuild the bundle so content hashes match the mutated trace
            bundle = build_bundle(
                bundle_id="b_evil", created=CREATED, scenarios=[scenario], conditions=conditions,
                tool_manifests=list(support.manifests().values()), environment=support.environment(),
                trials=[{"trial_id": "t0", "scenario_id": tt["scenario_id"],
                         "condition_id": tt["condition_id"], "seed": tt["seed"],
                         "repeat_index": tt["repeat_index"],
                         "status": "completed", "trace_ref": content_hash(trace)}],
                aggregates=[], traces=traces,
            )
            store.publish(bundle, traces, question="q")

            # nothing was written outside the store tree
            self.assertFalse((root / "etc").exists())
            trace_files = list(store_root.glob("e_*/traces/*.json"))
            self.assertTrue(trace_files)
            for path in trace_files:
                self.assertTrue(path.resolve().is_relative_to(store_root.resolve()))
                self.assertNotIn("..", path.name)  # named by content hash, not the id


class TestVisibility(unittest.TestCase):
    def test_unlisted_not_in_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            store.publish(bundle, traces, question="q", visibility="unlisted")
            self.assertEqual(store.catalog(), [])

    def test_public_in_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            store.publish(bundle, traces, question="q", visibility="public")
            self.assertEqual(len(store.catalog()), 1)


class TestWriteAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server(
            Path(cls.tmp.name) / "store", host="127.0.0.1", port=0,
            write_token="wsecret", admin_token="asecret",
        )
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.bundle, cls.traces = _bundle_and_traces()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _post(self, path, payload, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_publish_without_token_is_401(self) -> None:
        status, _ = self._post("/api/publications",
                               {"bundle": self.bundle, "traces": self.traces, "question": "q"})
        self.assertEqual(status, 401)

    def test_publish_with_token_succeeds(self) -> None:
        status, body = self._post(
            "/api/publications",
            {"bundle": self.bundle, "traces": self.traces, "question": "q"}, token="wsecret")
        self.assertEqual(status, 201, body)
        pid = body["publication_id"]
        # takedown requires the ADMIN token, not the write token
        status, _ = self._post(f"/api/publications/{pid}/takedown", {}, token="wsecret")
        self.assertEqual(status, 401)
        status, _ = self._post(f"/api/publications/{pid}/takedown", {}, token="asecret")
        self.assertEqual(status, 200)


class TestTamperedFileNotTrustedOnReload(unittest.TestCase):
    def test_locally_edited_trace_is_dropped_on_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = PublicationStore(root=root)
            bundle, traces = _bundle_and_traces()
            pid = str(store.publish(bundle, traces, question="q").publication["publication_id"])
            # tamper a stored trace file on disk
            trace_file = next((root / pid / "traces").glob("*.json"))
            data = json.loads(trace_file.read_text())
            for event in data["events"]:
                if event.get("type") == "gate_decision":
                    v = event["decision"]["verdict"]
                    event["decision"]["verdict"] = "ALLOW" if v == "DENY" else "DENY"
            trace_file.write_text(json.dumps(data))
            # a fresh store must not surface the tampered publication as trusted
            reloaded = PublicationStore(root=root)
            self.assertEqual(reloaded.catalog(), [])


if __name__ == "__main__":
    unittest.main()
