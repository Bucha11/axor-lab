"""Gateway conformity boundary (review r14).

The HTTP gateway is a public decision surface. Every accepted event must be
well-formed for its type; an unknown tool cannot be governed; a redacted
sensitive value must still pin its bytes; the assembled trace is validated as a
conformant trace/v1 at finalize (never served out-of-spec); malformed input is a
clean 4xx, never a 500 with a stack trace; and terminal (finalized) runs must
not permanently exhaust the run quota.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from tests import support
from lab_contracts import content_hash, validate_artifact
from lab_contracts.semantics import trace_semantics
from lab_endpoint import make_gateway

SAFE_IBAN = "DE00KNOWN0000000000"


class _Base(unittest.TestCase):
    max_runs = 1000

    def setUp(self) -> None:
        self.inputs = dict(support.banking_scenario()["inputs"])
        self.server = make_gateway(
            support.conditions()[1], support.manifests(), self.inputs,
            scenario_id="banking-exfil-01", max_runs=self.max_runs,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _post(self, path, obj, secret=None):
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _get(self, path, secret):
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {secret}"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _open(self):
        _, opened = self._post("/runs", {})
        return opened["run_id"], opened["run_secret"]


class TestUnknownTool(_Base):
    def test_intent_for_an_unknown_tool_is_a_clean_400_not_a_500(self) -> None:
        # a tool with no manifest cannot be gated — the old code hit KeyError
        # inside gate_intent and returned a 500 with a traceback
        run_id, secret = self._open()
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "no_such_tool", "arg_bindings": {},
        }, secret)
        self.assertEqual(status, 400, body)
        self.assertIn("unknown or missing tool", body["error"])

    def test_tool_result_for_an_unknown_tool_is_rejected(self) -> None:
        run_id, secret = self._open()
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_result", "tool": "no_such_tool",
            "values": [{"value_id": "v", "decision_value": "x", "labels": ["trusted"],
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]}],
        }, secret)
        self.assertEqual(status, 400, body)
        self.assertIn("unknown or missing tool", body["error"])


class TestMalformedShape(_Base):
    def test_non_object_arg_bindings_is_400(self) -> None:
        run_id, secret = self._open()
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money", "arg_bindings": ["not", "a", "map"],
        }, secret)
        self.assertEqual(status, 400, body)
        self.assertIn("arg_bindings", body["error"])

    def test_non_list_values_is_400(self) -> None:
        run_id, secret = self._open()
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_result", "tool": "read_txns", "values": {"value_id": "v"},
        }, secret)
        self.assertEqual(status, 400, body)
        self.assertIn("must be a list", body["error"])

    def test_non_object_value_is_400(self) -> None:
        run_id, secret = self._open()
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_result", "tool": "read_txns", "values": ["just-a-string"],
        }, secret)
        self.assertEqual(status, 400, body)

    def test_non_object_args_assertion_is_400(self) -> None:
        run_id, secret = self._open()
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {}, "args": "not-an-object",
        }, secret)
        self.assertEqual(status, 400, body)
        self.assertIn("args must be an object", body["error"])


class TestRedactedSensitiveValue(_Base):
    def test_redacted_sensitive_value_without_a_hash_is_rejected(self) -> None:
        # no decision_value AND no canonical_value_hash → nothing pins the bytes,
        # the assembled trace would fail trace_semantics; reject at event time
        run_id, secret = self._open()
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_result", "tool": "read_txns",
            "values": [{"value_id": "v_s", "labels": ["sensitive"],
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]}],
        }, secret)
        self.assertEqual(status, 400, body)
        self.assertIn("canonical_value_hash", body["error"])

    def test_redacted_sensitive_value_with_a_hash_is_accepted_and_finalizes(self) -> None:
        run_id, secret = self._open()
        status, _ = self._post(f"/runs/{run_id}/events", {
            "type": "tool_result", "tool": "read_txns",
            "values": [{"value_id": "v_s", "labels": ["sensitive"],
                        "canonical_value_hash": content_hash("the-secret"),
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]}],
        }, secret)
        self.assertEqual(status, 200)
        status, body = self._post(f"/runs/{run_id}/finalize", {}, secret)
        self.assertEqual(status, 200, body)


class TestFinalizeValidatesTheTrace(_Base):
    def test_finalize_serves_only_a_conformant_trace(self) -> None:
        run_id, secret = self._open()
        # emit a clean value + a gated intent, finalize, then read the trace and
        # independently confirm it is a conformant trace/v1
        self._post(f"/runs/{run_id}/events", {
            "type": "tool_result", "tool": "read_txns",
            "values": [{"value_id": "v_r", "decision_value": SAFE_IBAN, "labels": ["prompt_given"],
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]},
                       {"value_id": "v_a", "decision_value": 1200, "labels": ["prompt_given"],
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]}],
        }, secret)
        self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {"recipient": "v_r", "amount": "v_a"},
        }, secret)
        status, body = self._post(f"/runs/{run_id}/finalize", {}, secret)
        self.assertEqual(status, 200, body)
        status, trace = self._get(f"/runs/{run_id}/trace", secret)
        self.assertEqual(status, 200)
        self.assertEqual(validate_artifact(trace, "trace"), [])
        self.assertEqual(trace_semantics(trace), [])


class TestRunEviction(_Base):
    max_runs = 2

    def test_finalized_runs_are_evicted_to_admit_new_runs(self) -> None:
        a_id, a_secret = self._open()
        b_id, b_secret = self._open()
        # quota is full and both are ACTIVE → a third open is refused
        status, body = self._post("/runs", {})
        self.assertEqual(status, 429, body)
        self.assertIn("all runs active", body["error"])
        # finalize A → it is terminal and now evictable
        self.assertEqual(self._post(f"/runs/{a_id}/finalize", {}, a_secret)[0], 200)
        # a new open now succeeds by evicting the finalized run A
        status, opened = self._post("/runs", {})
        self.assertEqual(status, 201, opened)
        # A is gone (evicted) — its trace can no longer be read; B still lives
        self.assertEqual(self._get(f"/runs/{a_id}/trace", a_secret)[0], 404)
        self.assertEqual(self._get(f"/runs/{b_id}/trace", b_secret)[0], 409)  # live, not finalized


if __name__ == "__main__":
    unittest.main()
