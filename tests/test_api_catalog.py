"""`GET /api/publications` — the JSON catalog the SPA renders.

The JSON list must carry the same visibility semantics as the HTML catalog
(store.catalog() / render_catalog): PUBLIC publications are listed with their
question + three-axis provenance; an UNLISTED publication stays reachable by
its capability URL but never appears in the list; a PRIVATE one is served
nowhere. The listing is derived data only — it never invents fields the
publication does not have.
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
from lab_server import make_server

REPEATS = 8
CREATED = "2026-07-19T12:00:00+00:00"


def _publishable_bundle(
    run_id: str,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """A real, verifiable bundle (distinct run_id → distinct bundle_ref, so
    each publication in this suite is independent)."""
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=REPEATS, run_id=run_id,
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
        bundle_id=f"b_{run_id}", created=CREATED, scenarios=[scenario],
        conditions=conditions, tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials,
        aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestApiCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.tmp.name) / "store", host="127.0.0.1", port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
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

    def _get_json(self, path: str) -> tuple[int, dict[str, object]]:
        try:
            with urllib.request.urlopen(self.base + path) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _publish(self, run_id: str, question: str, visibility: str) -> str:
        bundle, traces = _publishable_bundle(run_id)
        status, body = self._post(
            "/api/publications",
            {"bundle": bundle, "traces": traces, "question": question,
             "visibility": visibility},
        )
        self.assertEqual(status, 201, body)
        return str(body["publication_id"])

    def test_json_catalog_lists_public_with_provenance(self) -> None:
        pid = self._publish("r_cat_pub", "Does governance stop the exfil? (json catalog)", "public")
        status, body = self._get_json("/api/publications")
        self.assertEqual(status, 200)
        listing: list[dict[str, object]] = body["publications"]  # type: ignore[assignment]
        entry = next(e for e in listing if e["publication_id"] == pid)
        self.assertEqual(entry["question"], "Does governance stop the exfil? (json catalog)")
        self.assertEqual(entry["url"], f"/e/{pid}")
        provenance: dict[str, object] = entry["provenance"]  # type: ignore[assignment]
        self.assertEqual(provenance["origin"], "local")
        self.assertEqual(provenance["integrity"], "hash_verified")
        reproductions: dict[str, object] = provenance["reproductions"]  # type: ignore[assignment]
        self.assertEqual(reproductions["count"], 0)

    def test_json_catalog_mirrors_html_visibility_semantics(self) -> None:
        # unlisted: reachable by capability URL, NEVER listed (same as the HTML
        # catalog); private: served nowhere, so also never listed
        unlisted = self._publish("r_cat_unl", "unlisted stays off the json catalog", "unlisted")
        private = self._publish("r_cat_priv", "private is never served", "private")

        status, body = self._get_json("/api/publications")
        self.assertEqual(status, 200)
        listed_ids = {e["publication_id"] for e in body["publications"]}  # type: ignore[union-attr]
        self.assertNotIn(unlisted, listed_ids)
        self.assertNotIn(private, listed_ids)

        # the unlisted publication is still reachable directly …
        status, _ = self._get_json(f"/api/publications/{unlisted}")
        self.assertEqual(status, 200)
        # … the private one is not, anywhere
        status, _ = self._get_json(f"/api/publications/{private}")
        self.assertEqual(status, 404)

    def test_reproduction_shows_up_in_the_listing(self) -> None:
        pid = self._publish("r_cat_repro", "reproductions grow the listed axis", "public")
        attestation = {
            "schema_version": "attestation/v1",
            "publication_id": pid,
            "by": "@ext-lab-mit",
            "kind": "fresh_live",
            "created": "2026-07-20T00:00:00Z",
            "result": {"estimate": 0.0},
        }
        status, _ = self._post(
            f"/api/publications/{pid}/reproductions", {"attestation": attestation},
        )
        self.assertEqual(status, 201)
        _, body = self._get_json("/api/publications")
        entry = next(e for e in body["publications"] if e["publication_id"] == pid)  # type: ignore[union-attr]
        self.assertEqual(entry["provenance"]["reproductions"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
