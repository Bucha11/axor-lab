"""Investigating, pinning and exporting YOUR OWN run — without publishing it.

Three CLI-only capabilities, closed:

* `axor-lab evidence` — the web could only render an EvidenceCase for a
  PUBLISHED bundle, so investigating your own run meant publishing it first.
  That is backwards: investigation is what decides whether a run is worth
  publishing, and most are not.
* `axor-lab pin` — web pinning ran only off an imported incident, so closing the
  experiment → regression loop required a production incident to exist.
* `axor-lab export-cp` — the Lab → Control Plane handoff had no web path at all,
  which put the bridge into the paid contour behind a terminal.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_server import make_runtime_server


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.server: ThreadingHTTPServer = make_runtime_server(
            host="127.0.0.1", port=0, control_token=None,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        status, run = self._call("/runs/local", {})
        self.assertEqual(status, 201, run)
        self.run_id = run["run_id"]

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

    def _denied_trace(self) -> str:
        _, index = self._call(f"/runs/{self.run_id}/traces")
        denied = [t for t in index["traces"] if t["denied"]]
        self.assertTrue(denied, "the example run should contain denials")
        return denied[0]["trace_id"]


class TraceIndexTest(_Base):
    def test_the_index_marks_which_traces_were_denied(self) -> None:
        # Without this the only way to find the interesting trace in a 60-trace
        # run is to open them one at a time.
        status, index = self._call(f"/runs/{self.run_id}/traces")
        self.assertEqual(status, 200)
        self.assertGreater(len(index["traces"]), 0)
        self.assertTrue(any(t["denied"] for t in index["traces"]))
        self.assertTrue(any(not t["denied"] for t in index["traces"]))
        for entry in index["traces"]:
            self.assertTrue(entry["scenario_id"])
            self.assertTrue(entry["condition_id"])
            self.assertEqual(entry["denied"], "DENY" in entry["verdicts"])


class RunEvidenceTest(_Base):
    def test_an_unpublished_run_yields_a_real_evidence_case(self) -> None:
        status, case = self._call(f"/runs/{self.run_id}/evidence/{self._denied_trace()}")
        self.assertEqual(status, 200, case)
        # the same construction the CLI and the published page use
        for key in ("trace_id", "chain", "modes", "fidelity"):
            self.assertIn(key, case)

    def test_an_unknown_trace_is_a_404(self) -> None:
        status, _ = self._call(f"/runs/{self.run_id}/evidence/t_nope")
        self.assertEqual(status, 404)


class RunPinTest(_Base):
    def test_it_pins_the_whole_verdict_sequence_not_just_the_last(self) -> None:
        # A multi-call trace whose real sequence is (ALLOW, ALLOW, DENY) compared
        # against a singleton DENY reports a regression on an unchanged trace.
        trace_id = self._denied_trace()
        status, body = self._call(f"/runs/{self.run_id}/pin", {"trace_id": trace_id})
        self.assertEqual(status, 201, body)
        pin = body["pin"]
        self.assertEqual(pin["trace_id"], trace_id)
        self.assertTrue(pin["trace_ref"].startswith("sha256:"))
        self.assertIn("DENY", pin["expected_sequence"])
        self.assertTrue(body["scenario_id"])

    def test_the_default_expectation_is_what_the_trace_actually_did(self) -> None:
        trace_id = self._denied_trace()
        _, explicit = self._call(
            f"/runs/{self.run_id}/pin", {"trace_id": trace_id, "expected": "DENY"},
        )
        _, implied = self._call(f"/runs/{self.run_id}/pin", {"trace_id": trace_id})
        self.assertEqual(explicit["pin"], implied["pin"])

    def test_an_expectation_contradicting_the_trace_is_refused(self) -> None:
        # Pinning "this should ALLOW" against a trace that recorded DENY would
        # bake a false expectation into the corpus.
        status, body = self._call(
            f"/runs/{self.run_id}/pin",
            {"trace_id": self._denied_trace(), "expected": "ALLOW"},
        )
        self.assertEqual(status, 422, body)

    def test_an_unknown_trace_is_a_404(self) -> None:
        status, _ = self._call(f"/runs/{self.run_id}/pin", {"trace_id": "t_nope"})
        self.assertEqual(status, 404)


class RunCpExportTest(_Base):
    def test_it_builds_a_deployable_config_from_the_run(self) -> None:
        status, export = self._call(f"/runs/{self.run_id}/cp-export", {})
        self.assertEqual(status, 200, export)
        for key in ("kernel", "policy", "config_hash"):
            self.assertIn(key, export["config"])
        self.assertTrue(export["production_todo"].strip())
        # whether the advantage is statistically EARNED, not merely observed —
        # the caller must be able to tell those apart
        self.assertIsInstance(export["earned_bridge"], bool)

    def test_pins_from_the_run_can_be_carried_into_the_export(self) -> None:
        _, pinned = self._call(
            f"/runs/{self.run_id}/pin", {"trace_id": self._denied_trace()},
        )
        status, export = self._call(
            f"/runs/{self.run_id}/cp-export", {"regressions": [pinned["pin"]]},
        )
        self.assertEqual(status, 200, export)
        self.assertEqual(len(export["config"]["regressions"]), 1)

    def test_a_bogus_condition_is_refused(self) -> None:
        status, _ = self._call(
            f"/runs/{self.run_id}/cp-export", {"condition_id": "no-such-condition"},
        )
        self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
