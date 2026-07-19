"""End-to-end tests of lab_server over real HTTP (Phase 4 + minimal Phase 5).

Runs a live experiment, uploads the bundle through the publish handshake,
and checks: server-side re-verification (acceptance 8), the catalog +
publication page + EvidenceCase HTML, three-axis provenance that grows with
an appended reproduction without changing origin (acceptance 9), untrusted
content escaped (threat-model §4), and a tampered bundle rejected.
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
from lab_contracts import build_bundle, content_hash
from lab_runner import run_experiment_suite
from lab_server import make_server

REPEATS = 8
CREATED = "2026-07-19T12:00:00+00:00"


def _bundle_with_xss_question() -> tuple[dict[str, object], dict[str, dict[str, object]], str]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=REPEATS, run_id="r_srv",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate(
            "ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
            test=mcnemar_test(pairs, vs="ungoverned"),
        ),
    ]
    bundle = build_bundle(
        bundle_id="b_srv", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    denied = next(
        tid for tid, t in traces.items()
        if any(
            e.get("type") == "gate_decision" and e["decision"]["verdict"] == "DENY"
            for e in t["events"]
        )
    )
    return bundle, traces, str(denied)


class TestServerEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.tmp.name) / "store", host="127.0.0.1", port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.bundle, cls.traces, cls.denied = _bundle_with_xss_question()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _post(self, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _get(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(self.base + path) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def _publish(self, question: str = "Does governance stop the exfil?") -> str:
        status, body = self._post(
            "/api/publications",
            {"bundle": self.bundle, "traces": self.traces, "question": question},
        )
        self.assertEqual(status, 201, body)
        return str(body["publication_id"])

    def test_publish_handshake_mints_local_origin(self) -> None:
        pid = self._publish()
        status, pub = self._get_json(f"/api/publications/{pid}")
        self.assertEqual(status, 200)
        self.assertEqual(pub["origin"], "local")
        self.assertEqual(pub["integrity"], "hash_verified")
        kinds = {c["kind"] for c in pub["claims"]}
        self.assertIn("exactly_replayable", kinds)
        self.assertIn("statistically_reproducible", kinds)

    def test_tampered_bundle_is_rejected_server_side(self) -> None:
        tampered_traces = json.loads(json.dumps(self.traces))
        victim = tampered_traces[self.denied]
        for event in victim["events"]:
            if event.get("type") == "gate_decision":
                event["decision"]["verdict"] = "ALLOW"
        status, body = self._post(
            "/api/publications",
            {"bundle": self.bundle, "traces": tampered_traces, "question": "tampered"},
        )
        self.assertIn(status, (409, 422))
        self.assertIn("error", body)

    def test_catalog_and_publication_pages_render(self) -> None:
        pid = self._publish()
        status, catalog = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Axor Lab — Catalog", catalog)
        self.assertIn(pid, catalog)

        status, page = self._get(f"/e/{pid}")
        self.assertEqual(status, 200)
        self.assertIn("Exactly replayable", page)
        self.assertIn("Statistically reproducible", page)
        self.assertIn("axor-lab replay", page)
        self.assertIn("origin: local", page)

    def test_evidence_page_renders_the_chain(self) -> None:
        pid = self._publish()
        status, page = self._get(f"/e/{pid}/evidence/{self.denied}")
        self.assertEqual(status, 200)
        self.assertIn("EvidenceCase", page)
        self.assertIn("send_money", page)
        self.assertIn("DENY", page)

    def test_untrusted_question_is_escaped(self) -> None:
        pid = self._publish(question="<script>alert('xss')</script>")
        status, page = self._get(f"/e/{pid}")
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_reproduction_grows_axis_without_changing_origin(self) -> None:
        pid = self._publish()
        attestation = {
            "schema_version": "attestation/v1",
            "publication_id": pid,
            "by": "@ext-lab-mit",
            "kind": "fresh_live",
            "created": "2026-07-20T00:00:00Z",
            "result": {"estimate": 0.0},
        }
        status, body = self._post(f"/api/publications/{pid}/reproductions", {"attestation": attestation})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["reproductions"]["count"], 1)

        _, pub = self._get_json(f"/api/publications/{pid}")
        self.assertEqual(pub["origin"], "local")  # origin unchanged
        self.assertEqual(pub["provenance"]["reproductions"]["count"], 1)

    def _get_json(self, path: str) -> tuple[int, dict[str, object]]:
        status, text = self._get(path)
        return status, json.loads(text)


if __name__ == "__main__":
    unittest.main()
