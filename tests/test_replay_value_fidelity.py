"""Adversarial replay-value tests (review P0.1).

Replay must reconstruct the gate's arguments from the authoritative typed
`decision_value`, never from the truncated `preview`. These exercise the
cases that broke the old preview-based reconstruction: strings longer than the
preview cap, structured/list/None/bool arguments, and values whose previews
collide.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_contracts import content_hash
from lab_runner import Kernel, ValueLedger, replay_trace


class TestLedgerStoresAuthoritativeValue(unittest.TestCase):
    def test_long_string_is_not_truncated_in_decision_value(self) -> None:
        ledger = ValueLedger()
        long_iban = "DE89" + "0" * 400  # far longer than the 120-char preview cap
        vid = ledger.mint_constant(long_iban, "prompt:x")
        value = ledger.get(vid)
        self.assertEqual(value["decision_value"], long_iban)          # exact
        self.assertLessEqual(len(value["preview"]), 120)              # preview truncated
        self.assertEqual(value["canonical_value_hash"], content_hash(long_iban))

    def test_structured_and_scalar_values_kept_typed(self) -> None:
        ledger = ValueLedger()
        cases: list[object] = [
            {"nested": {"a": [1, 2, 3]}},
            [1, "two", None, True],
            42,
            0.5,
            True,
            None,
            "",
        ]
        for original in cases:
            vid = ledger.mint_constant(original, "prompt:x")
            self.assertEqual(ledger.get(vid)["decision_value"], original)


class TestReplayUsesDecisionValueNotPreview(unittest.TestCase):
    def _trace_with_long_recipient(self) -> tuple[dict[str, object], str]:
        # a governed trace whose recipient is a long untrusted string; the
        # preview is truncated but decision_value holds the full value
        ledger = ValueLedger()
        long_attacker = "XX99" + "9" * 300
        v_inj = ledger.mint_external_read("IGNORE…", "tool_result:read_txns:t[1].description")
        v_recipient = ledger.mint_model_extraction(long_attacker)
        v_amount = ledger.mint_constant(1200, "prompt:amount")
        trace = {
            "schema_version": "trace/v1", "trace_id": "t_long",
            "trial": {"run_id": "r", "scenario_id": "banking-exfil-01",
                      "condition_id": "governed", "seed": "s0", "repeat_index": 0},
            "producer": {"mode": "wrapped_code", "provenance_fidelity": "explicit_flow_tracked",
                         "kernel_version": support.KERNEL_PINNED},
            "events": [
                {"seq": 0, "node": "root", "type": "tool_result", "tool": "read_txns",
                 "produces_value_ids": [v_inj]},
                {"seq": 1, "node": "root", "type": "tool_call_intent", "tool": "send_money",
                 "arg_bindings": {"recipient": v_recipient, "amount": v_amount}},
                {"seq": 2, "node": "root", "type": "gate_decision",
                 "decision": {"verdict": "DENY", "gate": "taint_floor",
                              "driving_value_id": v_recipient, "projection": "untrusted-derived"}},
            ],
            "values": ledger.values,
        }
        return trace, long_attacker

    def test_long_untrusted_recipient_replays_to_deny(self) -> None:
        trace, _ = self._trace_with_long_recipient()
        self.assertEqual(support.schema_errors(trace, "trace"), [])
        kernel = Kernel(version=support.KERNEL_PINNED)
        recomputed, matches = replay_trace(
            trace, support.conditions()[1], kernel, support.manifests(),
            support.banking_scenario()["inputs"],
        )
        self.assertTrue(matches)
        self.assertEqual(recomputed[0]["verdict"], "DENY")

    def test_effect_resolution_sees_full_value_on_replay(self) -> None:
        # a WRITE-to-known-IBAN decision depends on the FULL recipient value;
        # a truncated preview would misclassify it. Build a trace where the
        # recipient is a known IBAN (trusted, prompt-given) → effect WRITE → ALLOW.
        ledger = ValueLedger()
        known = support.LANDLORD_IBAN
        v_recipient = ledger.mint_constant(known, "prompt:landlord_iban")
        v_amount = ledger.mint_constant(1200, "prompt:amount")
        trace = {
            "schema_version": "trace/v1", "trace_id": "t_write",
            "trial": {"run_id": "r", "scenario_id": "banking-exfil-01",
                      "condition_id": "governed", "seed": "s0", "repeat_index": 0},
            "producer": {"mode": "wrapped_code", "provenance_fidelity": "explicit_flow_tracked",
                         "kernel_version": support.KERNEL_PINNED},
            "events": [
                {"seq": 0, "node": "root", "type": "tool_call_intent", "tool": "send_money",
                 "arg_bindings": {"recipient": v_recipient, "amount": v_amount}},
                {"seq": 1, "node": "root", "type": "gate_decision",
                 "decision": {"verdict": "ALLOW", "gate": "taint_floor",
                              "driving_value_id": v_recipient}},
            ],
            "values": ledger.values,
        }
        kernel = Kernel(version=support.KERNEL_PINNED)
        recomputed, matches = replay_trace(
            trace, support.conditions()[1], kernel, support.manifests(),
            support.banking_scenario()["inputs"],
        )
        self.assertTrue(matches)
        self.assertEqual(recomputed[0]["verdict"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
