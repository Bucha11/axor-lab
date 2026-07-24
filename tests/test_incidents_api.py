"""The Control Plane → Lab cross-link: incident intake over HTTP.

`POST /api/incidents` must be the CLI `import-incident` semantics EXACTLY —
both call the shared core lab_runner.incident.import_incident (schema +
semantic + cross-reference validation, config-hash verification, replay under
the recorded condition BEFORE any write). Covers: the shared core itself, a
valid package → 201 + retrievable, a doctored config_hash → 4xx, a replay
mismatch → 422 with honest divergence detail, missing write token → 401, and
the /api/traces/{trace_id} resolver over both publications and incidents.
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
from lab_runner import (
    IncidentImportError,
    IncidentReplayMismatch,
    ScriptedAgent,
    import_incident,
    run_trial,
)

WRITE_TOKEN = "test-write-token"


def _incident_fixture() -> tuple[
    dict[str, object], dict[str, object], list[dict[str, object]], dict[str, object]
]:
    """A production-style incident, produced the same way the CLI import test
    builds its fixture: one governed trial with an attacking agent → DENY."""
    governed = support.conditions()[1]
    trace = run_trial(
        support.banking_scenario(), support.manifests(), governed,
        support.kernel_registry().get(support.KERNEL_PINNED),
        run_id="prod", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
    ).trace
    return trace, support.banking_scenario(), list(support.manifests().values()), governed


def _package(
    trace: dict[str, object], scenario: dict[str, object],
    manifests: list[dict[str, object]], condition: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "axor-lab-incident/v1",
        "trace": trace, "scenario": scenario,
        "manifests": manifests, "condition": condition,
        "source": {"product": "control-plane", "run_id": "cp_run_42",
                   "url": "https://cp.example/runs/cp_run_42"},
    }


class TestImportIncidentCore(unittest.TestCase):
    """The shared core the CLI and HTTP surface both call."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trace, cls.scenario, cls.manifests, cls.condition = _incident_fixture()

    def test_valid_import_builds_replaying_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            result = import_incident(
                self.trace, self.scenario, self.manifests, self.condition, out
            )
            self.assertEqual(result.replay_status, "match")
            self.assertEqual(result.trace_id, str(self.trace["trace_id"]))
            self.assertEqual(result.scenario_id, "banking-exfil-01")
            self.assertEqual(result.condition_id, "governed")
            # the bundle dir was written and carries the honest provenance
            bundle = json.loads((out / "bundle.json").read_text())
            trial = next(t for t in bundle["trials"] if t["status"] == "completed")
            self.assertEqual(trial["runtime_provenance"], "reconstructed_incident")

    def test_no_out_dir_writes_nothing_but_returns_bundle(self) -> None:
        result = import_incident(self.trace, self.scenario, self.manifests, self.condition)
        self.assertTrue(str(result.bundle["bundle_id"]).startswith("b_incident_"))

    def test_bad_config_hash_is_rejected(self) -> None:
        doctored = dict(self.condition)
        doctored["config_hash"] = "sha256:" + "0" * 64
        with self.assertRaises(IncidentImportError):
            import_incident(self.trace, self.scenario, self.manifests, doctored)

    def test_replay_mismatch_raises_with_detail(self) -> None:
        wrong = {k: v for k, v in self.condition.items() if k != "config_hash"}
        wrong["enforcement"] = "off"  # the recorded DENY would replay as ALLOW
        with self.assertRaises(IncidentReplayMismatch) as ctx:
            import_incident(self.trace, self.scenario, self.manifests, wrong)
        detail = ctx.exception.detail
        self.assertEqual(detail["status"], "mismatch")
        recorded = [d["verdict"] for d in detail["recorded_verdicts"]]
        recomputed = [d["verdict"] for d in detail["recomputed_verdicts"]]
        self.assertIn("DENY", recorded)
        self.assertNotEqual(recorded, recomputed)


class TestIncidentsAPI(unittest.TestCase):
    """HTTP intake in the style of test_server_e2e, with a write token set."""

    @classmethod
    def setUpClass(cls) -> None:
        from lab_server import make_server

        cls.tmp = tempfile.TemporaryDirectory()
        cls.store_root = Path(cls.tmp.name) / "store"
        cls.server = make_server(
            cls.store_root, host="127.0.0.1", port=0, write_token=WRITE_TOKEN,
        )
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.trace, cls.scenario, cls.manifests, cls.condition = _incident_fixture()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _post(
        self, path: str, payload: dict[str, object], token: str | None = WRITE_TOKEN
    ) -> tuple[int, dict[str, object]]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _get(self, path: str) -> tuple[int, dict[str, object]]:
        try:
            with urllib.request.urlopen(self.base + path) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _pkg(self) -> dict[str, object]:
        return _package(self.trace, self.scenario, self.manifests, self.condition)

    def test_import_valid_package_then_get(self) -> None:
        status, body = self._post("/api/incidents", self._pkg())
        self.assertEqual(status, 201, body)
        self.assertEqual(body["replay"], "match")
        self.assertEqual(body["trace_id"], str(self.trace["trace_id"]))
        incident_id = str(body["incident_id"])
        self.assertEqual(body["url"], f"/i/{incident_id}")

        # listed
        status, listing = self._get("/api/incidents")
        self.assertEqual(status, 200)
        entry = next(i for i in listing["incidents"] if i["incident_id"] == incident_id)
        self.assertEqual(entry["scenario_id"], "banking-exfil-01")
        self.assertEqual(entry["trace_id"], str(self.trace["trace_id"]))
        self.assertEqual(entry["source"]["product"], "control-plane")
        self.assertTrue(entry["imported_at"])

        # full package retrievable, with the recorded condition verbatim and
        # the built trace-replay bundle
        status, full = self._get(f"/api/incidents/{incident_id}")
        self.assertEqual(status, 200)
        self.assertEqual(full["schema_version"], "axor-lab-incident/v1")
        self.assertEqual(full["condition"], self.condition)
        self.assertEqual(full["trace"]["trace_id"], str(self.trace["trace_id"]))
        self.assertEqual(len(full["manifests"]), len(self.manifests))
        trial = next(t for t in full["bundle"]["trials"] if t["status"] == "completed")
        self.assertEqual(trial["runtime_provenance"], "reconstructed_incident")

        # idempotent re-import: same content-derived id, no duplicate (200)
        status, again = self._post("/api/incidents", self._pkg())
        self.assertEqual(status, 200, again)
        self.assertEqual(again["incident_id"], incident_id)

    def test_replay_fidelity_round_trips_normalized(self) -> None:
        """A CP-authored replay_fidelity block is kept (bounded to known keys)
        and returned on the full incident GET — so a reader sees which gates
        the reference replay reproduces and which it cannot."""
        # a DISTINCT trace so this is a fresh incident (ids are content-derived,
        # so reusing self._pkg() would hit the already-imported one at 200)
        trace = copy.deepcopy(self.trace)
        trace["trace_id"] = "t_fidelity_probe"
        pkg = _package(trace, self.scenario, self.manifests, self.condition)
        pkg["replay_fidelity"] = {
            "backend": "reference_taint_floor_kernel",
            "recorded_kernel": "axor-core@9.9.9",
            "reproducible_gates": ["taint_floor"],
            "not_reproducible_gates": ["ssrf", "value_policy"],
            "note": "CP records observations, not bodies.",
            "evil_extra_key": "x" * 5000,  # dropped: not a known key
        }
        status, imported = self._post("/api/incidents", pkg)
        self.assertEqual(status, 201, imported)
        _, full = self._get(f"/api/incidents/{imported['incident_id']}")
        fidelity = full["replay_fidelity"]
        self.assertEqual(fidelity["reproducible_gates"], ["taint_floor"])
        self.assertIn("value_policy", fidelity["not_reproducible_gates"])
        self.assertNotIn("evil_extra_key", fidelity)

    def test_bad_config_hash_is_4xx(self) -> None:
        pkg = self._pkg()
        doctored = dict(self.condition)
        doctored["config_hash"] = "sha256:" + "0" * 64
        pkg["condition"] = doctored
        status, body = self._post("/api/incidents", pkg)
        self.assertEqual(status, 422, body)
        self.assertIn("config_hash", body["error"])

    def test_replay_mismatch_is_422_with_detail(self) -> None:
        # snapshot the store first: other tests share this class-level store, so
        # assert the mismatch attempt mints NOTHING NEW rather than an absolute count
        _, before = self._get("/api/incidents")
        before_ids = {i["trace_id"] for i in before["incidents"]}
        pkg = self._pkg()
        wrong = {k: v for k, v in self.condition.items() if k != "config_hash"}
        wrong["enforcement"] = "off"
        pkg["condition"] = wrong
        status, body = self._post("/api/incidents", pkg)
        self.assertEqual(status, 422, body)
        self.assertIn("does not replay", body["error"])
        self.assertEqual(body["replay"]["status"], "mismatch")
        self.assertIn("DENY", [d["verdict"] for d in body["replay"]["recorded_verdicts"]])
        # the mismatch attempt stored nothing: the trace-id set is unchanged
        _, listing = self._get("/api/incidents")
        after_ids = {i["trace_id"] for i in listing["incidents"]}
        self.assertEqual(after_ids, before_ids)
        self.assertNotIn(str(self.trace["trace_id"]), after_ids - before_ids)

    def test_not_an_incident_package_is_rejected(self) -> None:
        status, body = self._post("/api/incidents", {"schema_version": "nope/v1"})
        self.assertEqual(status, 422, body)
        self.assertIn("axor-lab-incident/v1", body["error"])

    def test_missing_write_token_is_401(self) -> None:
        status, body = self._post("/api/incidents", self._pkg(), token=None)
        self.assertEqual(status, 401, body)
        self.assertIn("error", body)

    def test_trace_resolver_finds_incident_and_404_on_unknown(self) -> None:
        status, body = self._post("/api/incidents", self._pkg())
        self.assertIn(status, (200, 201), body)  # idempotent with the other tests
        incident_id = str(body["incident_id"])
        trace_id = str(self.trace["trace_id"])

        status, resolved = self._get(f"/api/traces/{trace_id}")
        self.assertEqual(status, 200, resolved)
        self.assertEqual(resolved["trace_id"], trace_id)
        self.assertIn(incident_id, resolved["incidents"])
        self.assertEqual(resolved["publications"], [])

        status, _ = self._get("/api/traces/t_nowhere_at_all")
        self.assertEqual(status, 404)

    def test_trace_resolver_finds_publications_too(self) -> None:
        # publish the incident's own trace-replay bundle (it replays exactly,
        # so it clears the publish handshake) — the resolver must then report
        # the SAME trace under both spaces
        result = import_incident(self.trace, self.scenario, self.manifests, self.condition)
        status, body = self._post(
            "/api/publications",
            {"bundle": result.bundle, "traces": {result.trace_id: self.trace},
             "question": "does the incident verdict reproduce?", "visibility": "public"},
        )
        self.assertEqual(status, 201, body)
        pid = str(body["publication_id"])

        status, imported = self._post("/api/incidents", self._pkg())
        self.assertIn(status, (200, 201), imported)

        status, resolved = self._get(f"/api/traces/{result.trace_id}")
        self.assertEqual(status, 200, resolved)
        self.assertIn(pid, resolved["publications"])
        self.assertIn(str(imported["incident_id"]), resolved["incidents"])

    def test_incidents_survive_restart(self) -> None:
        # cold load re-runs the shared import core over the persisted record
        from lab_server import IncidentStore

        status, body = self._post("/api/incidents", self._pkg())
        self.assertIn(status, (200, 201), body)
        reloaded = IncidentStore(root=self.store_root / "incidents")
        stored = reloaded.get(str(body["incident_id"]))
        self.assertEqual(stored.trace_id, str(self.trace["trace_id"]))

    def test_unknown_incident_is_404(self) -> None:
        status, _ = self._get("/api/incidents/i_does_not_exist")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
