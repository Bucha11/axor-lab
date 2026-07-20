"""The gateway decides on the BOUND value, not a client-forged concrete arg
(review round 8, P0 — 'verify one value, allow another').

The gateway used to take `arg_bindings` and `args` independently: security
labels came from the bound ledger values, but the concrete args the gate
decided on came straight from the client. A client could bind a clean value
(prompt_given, safe IBAN) yet send a malicious concrete arg — the gate saw the
clean labels and ALLOWed, the tool would run the attacker value, and the trace
recorded only the clean binding, so replay reproduced the same laundered ALLOW.

The fix: the gate assembles args from `arg_bindings → decision_value` (exactly
as replay does); a client `args` is accepted only as an assertion and every
bound arg must match by canonical hash. A clean binding paired with a malicious
concrete arg is refused, never ALLOWed.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from tests import support
from lab_endpoint import make_gateway
from lab_runner import replay_bundle, default_registry

SAFE_IBAN = "DE00KNOWN0000000000"  # in the scenario's known_ibans allowlist


class TestGatewayArgsBinding(unittest.TestCase):
    def setUp(self) -> None:
        # known_ibans holds the operator allowlist the scenario ships
        self.inputs = dict(support.banking_scenario()["inputs"])
        self.server = make_gateway(
            support.conditions()[1], support.manifests(), self.inputs,
            scenario_id="banking-exfil-01",
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

    def _open(self):
        _, opened = self._post("/runs", {})
        return opened["run_id"], opened["run_secret"]

    def _emit_value(self, run_id, secret, vid, decision_value, labels, *, extra=None):
        value = {"value_id": vid, "decision_value": decision_value, "preview": str(decision_value),
                 "labels": labels, "sources": [{"kind": "external_read", "origin_ref": "o"}]}
        if extra:
            value.update(extra)
        return self._post(f"/runs/{run_id}/events",
                          {"type": "tool_result", "tool": "read_txns", "values": [value]}, secret)

    def test_clean_binding_with_malicious_concrete_arg_never_allows(self) -> None:
        run_id, secret = self._open()
        # a CLEAN prompt-given value holding a safe, allowlisted IBAN
        self._emit_value(run_id, secret, "v_clean", SAFE_IBAN, ["prompt_given"])
        # the classic laundering attempt: bind the clean value, but assert the
        # attacker IBAN as the concrete arg the tool will actually run
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {"recipient": "v_clean"},
            "args": {"recipient": support.ATTACKER_IBAN, "amount": 1200},
        }, secret)
        # refused as a protocol violation — NOT an ALLOW for the attacker value
        self.assertEqual(status, 409, body)
        self.assertIn("bound provenance", body["error"])

    def test_gate_decides_on_the_bound_value_when_assertion_matches(self) -> None:
        run_id, secret = self._open()
        # bind a clean allowlisted IBAN and assert it honestly → ALLOW
        self._emit_value(run_id, secret, "v_clean", SAFE_IBAN, ["prompt_given"])
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {"recipient": "v_clean"},
            "args": {"recipient": SAFE_IBAN, "amount": 1200},
        }, secret)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["decision"]["verdict"], "ALLOW")
        # the gateway returns the AUTHORITATIVE args a cooperating proxy must run
        # (the bound value), so an honest client executes the value that was gated
        self.assertEqual(body["authoritative_args"], {"recipient": SAFE_IBAN})

    def test_tainted_binding_is_denied_even_with_an_allowlisted_assertion(self) -> None:
        run_id, secret = self._open()
        # bind an UNTRUSTED attacker value, but try to assert an allowlisted one
        # to sneak past the taint floor — must fail the assertion, never ALLOW
        self._emit_value(run_id, secret, "v_bad", support.ATTACKER_IBAN, ["untrusted_derived"])
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {"recipient": "v_bad"},
            "args": {"recipient": SAFE_IBAN, "amount": 1200},
        }, secret)
        self.assertEqual(status, 409, body)

    def test_no_assertion_gate_uses_the_bound_value_and_denies_the_attacker(self) -> None:
        run_id, secret = self._open()
        # omit `args` entirely: the gate reconstructs the attacker value from the
        # binding and denies it — the concrete value is recoverable from ledger
        self._emit_value(run_id, secret, "v_bad", support.ATTACKER_IBAN, ["untrusted_derived"])
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {"recipient": "v_bad"},
        }, secret)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["decision"]["verdict"], "DENY")

    def test_decision_relevant_arg_must_be_bound(self) -> None:
        run_id, secret = self._open()
        # send_money's recipient is a driving arg; an intent that binds nothing
        # cannot provenance-check it → refused (fail closed), not ALLOWed
        status, body = self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {}, "args": {"recipient": support.ATTACKER_IBAN},
        }, secret)
        self.assertEqual(status, 409, body)
        self.assertIn("must be bound", body["error"])

    def test_gated_value_is_recoverable_from_ledger_and_replays_identically(self) -> None:
        run_id, secret = self._open()
        self._emit_value(run_id, secret, "v_bad", support.ATTACKER_IBAN, ["untrusted_derived"])
        self._post(f"/runs/{run_id}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {"recipient": "v_bad"},
            "args": {"recipient": support.ATTACKER_IBAN, "amount": 1200},
        }, secret)
        self._post(f"/runs/{run_id}/finalize", {}, secret)
        req = urllib.request.Request(self.base + f"/runs/{run_id}/trace",
                                     headers={"Authorization": f"Bearer {secret}"})
        with urllib.request.urlopen(req) as r:
            trace = json.loads(r.read())
        # the bound value carries the concrete attacker IBAN — the gated arg is
        # recoverable from the ledger, so the trace is not laundered
        v_bad = next(v for v in trace["values"] if v["value_id"] == "v_bad")
        self.assertEqual(v_bad["decision_value"], support.ATTACKER_IBAN)
        # and it replays to the same DENY from that ledger value alone
        condition = support.conditions()[1]
        kernels = {k.version: k for k in default_registry((str(condition["kernel"]),)).kernels}
        traces = {str(trace["trace_id"]): trace}
        bundle = {"conditions": [condition], "scenarios": [support.banking_scenario()],
                  "tool_manifests": list(support.manifests().values())}
        report = replay_bundle(bundle, traces, kernels)
        self.assertTrue(report.bit_identical)


if __name__ == "__main__":
    unittest.main()
