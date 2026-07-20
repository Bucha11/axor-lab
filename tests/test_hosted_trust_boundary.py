"""Hosted trust boundary: private/auth/validation on every route (review r7 P0).

- A private publication must be hidden on EVERY read route (HTML page, JSON API,
  EvidenceCase), not just the main page.
- Appending a reproduction attestation is a WRITE and requires the write token.
- The server must schema-validate raw traces (they live outside the bundle
  schema), matching the local read pipeline.
"""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests import support
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server import make_server

CREATED = "2026-07-19T12:00:00+00:00"


def _bundle():
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=6, run_id="r_tb",
    )
    bundle = build_bundle(
        bundle_id="b_tb", created=CREATED, scenarios=[scenario], conditions=support.conditions(),
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=[], traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    denied = next(
        tid for tid, t in traces.items()
        if any(e.get("type") == "gate_decision" and e["decision"]["verdict"] == "DENY"
               for e in t["events"])
    )
    return bundle, traces, denied


class TestHostedTrustBoundary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = make_server(
            Path(self.tmp.name) / "store", host="127.0.0.1", port=0, write_token="wt",
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.bundle, self.traces, self.denied = _bundle()

    def _post(self, path, payload, token="wt"):
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

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def _publish_private(self) -> str:
        status, body = self._post(
            "/api/publications",
            {"bundle": self.bundle, "traces": self.traces, "question": "secret",
             "visibility": "private"},
        )
        self.assertEqual(status, 201, body)
        return body["publication_id"]

    def test_private_hidden_from_html_page(self) -> None:
        pid = self._publish_private()
        self.assertEqual(self._get(f"/e/{pid}")[0], 404)

    def test_private_hidden_from_json_api(self) -> None:
        pid = self._publish_private()
        self.assertEqual(self._get(f"/api/publications/{pid}")[0], 404)

    def test_private_hidden_from_evidence_route(self) -> None:
        pid = self._publish_private()
        self.assertEqual(self._get(f"/e/{pid}/evidence/{self.denied}")[0], 404)

    def test_reproduction_requires_write_token(self) -> None:
        status, body = self._post(
            "/api/publications",
            {"bundle": self.bundle, "traces": self.traces, "question": "q", "visibility": "public"},
        )
        pid = body["publication_id"]
        attestation = {"schema_version": "attestation/v1", "publication_id": pid,
                       "by": "anon", "kind": "fresh_live", "created": CREATED}
        # no token → rejected
        self.assertEqual(
            self._post(f"/api/publications/{pid}/reproductions",
                       {"attestation": attestation}, token=None)[0],
            401,
        )
        # with the write token → accepted
        self.assertEqual(
            self._post(f"/api/publications/{pid}/reproductions", {"attestation": attestation})[0],
            201,
        )

    def test_schema_invalid_trace_is_rejected(self) -> None:
        traces = copy.deepcopy(self.traces)
        victim = next(iter(traces))
        del traces[victim]["events"]  # a trace without events is schema-invalid
        status, body = self._post(
            "/api/publications",
            {"bundle": self.bundle, "traces": traces, "question": "q", "visibility": "public"},
        )
        self.assertIn(status, (400, 422))


if __name__ == "__main__":
    unittest.main()
