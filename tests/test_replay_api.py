"""`POST /replay` — reproduce someone's governance verdicts, no agent needed.

The landing page has always offered this as the lowest-barrier path, and agent
ingest offered "upload traces → reproduce verdicts bit-identical". Neither was
reachable: replay lived only in the CLI, so the browser could parse a trace file
and count its events, and that was all.

What these tests pin is the honesty of the answer, not just its presence: a
tampered trace must read as divergence and name ONLY the trace that diverged, and
a bundle pinned to an absent kernel must read as "could not replay" rather than
"verdicts differ".
"""

from __future__ import annotations

import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_server import make_runtime_server
from lab_server.replay_api import MAX_REPLAY_TRACES, ReplayRefused, replay_upload


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
        status, package = self._call(f"/runs/{run['run_id']}/bundle")
        self.assertEqual(status, 200, package)
        self.bundle = package["bundle"]
        self.traces = package["traces"]

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


class ReplayEndpointTest(_Base):
    def test_a_clean_bundle_replays_bit_identically(self) -> None:
        status, report = self._call("/replay", {"bundle": self.bundle, "traces": self.traces})
        self.assertEqual(status, 200)
        self.assertEqual(report["outcome"], "reproduced")
        self.assertTrue(report["bit_identical"])
        self.assertEqual(report["traces"], len(self.traces))
        self.assertEqual({s["status"] for s in report["statuses"]}, {"match"})
        self.assertGreater(report["deny"], 0)
        self.assertGreater(report["allow"], 0)

    def test_it_accepts_both_trace_shapes(self) -> None:
        # a bundle route returns {id: trace}; a bundle directory reads as a list
        as_list = list(self.traces.values())
        status, report = self._call("/replay", {"bundle": self.bundle, "traces": as_list})
        self.assertEqual(status, 200)
        self.assertTrue(report["bit_identical"])

    def test_a_tampered_trace_names_only_itself(self) -> None:
        # Divergence has to be attributable. A flipped verdict in one trace that
        # reported "everything mismatches" would be useless for finding it.
        traces = copy.deepcopy(self.traces)
        target = sorted(traces)[0]
        for event in traces[target]["events"]:
            if event.get("type") == "gate_decision":
                event["decision"]["verdict"] = (
                    "ALLOW" if event["decision"]["verdict"] == "DENY" else "DENY"
                )
                break

        status, report = self._call("/replay", {"bundle": self.bundle, "traces": traces})
        self.assertEqual(status, 200)
        self.assertEqual(report["outcome"], "diverged")
        self.assertFalse(report["bit_identical"])
        diverged = [s for s in report["statuses"] if s["status"] != "match"]
        self.assertEqual(len(diverged), 1)
        self.assertEqual(diverged[0]["trace_id"], target)

    def test_the_claim_says_what_replay_does_not_prove(self) -> None:
        # The result travels with its own scope. Replay reproduces governance,
        # not behaviour — a reader who takes it for the latter has been misled.
        _, report = self._call("/replay", {"bundle": self.bundle, "traces": self.traces})
        self.assertIn("Behaviour is not reproduced", report["claim"])

    def test_an_empty_upload_is_a_400(self) -> None:
        status, _ = self._call("/replay", {"bundle": self.bundle, "traces": []})
        self.assertEqual(status, 400)

    def test_a_duplicate_trace_id_is_refused_as_corrupt(self) -> None:
        one = next(iter(self.traces.values()))
        status, body = self._call("/replay", {"bundle": self.bundle, "traces": [one, one]})
        self.assertEqual(status, 400)
        self.assertIn("duplicate", body["error"])

    def test_a_non_bundle_is_a_422(self) -> None:
        status, _ = self._call("/replay", {"bundle": {"nope": 1}, "traces": self.traces})
        self.assertEqual(status, 422)


class ReplayUnitTest(unittest.TestCase):
    def test_an_unknown_kernel_is_not_reported_as_divergence(self) -> None:
        """A replay that could not be attempted is not a failed replay.

        The per-trace status already says `unsupported_kernel`, but a caller
        reading only `bit_identical: False` would conclude the verdicts differ —
        a false claim about someone's evidence. The named outcome separates them.
        """
        from lab_server.local_run import load_example, run_local

        executed = run_local(load_example())
        bundle = copy.deepcopy(executed["bundle"])
        for condition in bundle["conditions"]:
            condition["kernel"] = "axor-core@0.0.1-not-installed"

        report = replay_upload({"bundle": bundle, "traces": executed["traces"]})
        self.assertEqual(report["outcome"], "not_attempted")
        self.assertFalse(report["bit_identical"])
        self.assertEqual({s["status"] for s in report["statuses"]}, {"unsupported_kernel"})

    def test_the_trace_ceiling_is_enforced(self) -> None:
        traces = [{"trace_id": f"t{i}"} for i in range(MAX_REPLAY_TRACES + 1)]
        with self.assertRaises(ReplayRefused) as caught:
            replay_upload({"bundle": {}, "traces": traces})
        self.assertEqual(caught.exception.status, 413)


if __name__ == "__main__":
    unittest.main()
