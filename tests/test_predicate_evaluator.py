"""Predicate evaluator correctness (review round 2, Patch 3).

Four concrete defects the reference evaluator had:
  1. an intent with no gate_decision was scored as executed (fail-open);
  2. `count` cardinality bounds were ignored (any match satisfied the predicate);
  3. an invalid regex escaped as a raw re.error the CLI never caught;
  4. a boolean `true` compared equal to the number 1 (Python's True == 1.0).
"""

from __future__ import annotations

import unittest

from lab_runner import ValueLedger, evaluate
from lab_runner.errors import UnsupportedPredicateError

KERNEL = "reference_taint_floor@0.6.3"


def _trace(events: list[dict[str, object]], values: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "trace/v1", "trace_id": "t_pred",
        "trial": {"run_id": "r", "scenario_id": "s", "condition_id": "governed",
                  "seed": "s0", "repeat_index": 0},
        "producer": {"mode": "wrapped_code", "provenance_fidelity": "explicit_flow_tracked",
                     "kernel_version": KERNEL},
        "events": events, "values": values,
    }


def _send(call_id: str, bindings: dict[str, str], seq: int) -> dict[str, object]:
    return {"seq": seq, "node": "root", "type": "tool_call_intent",
            "tool": "send_money", "call_id": call_id, "arg_bindings": bindings}


def _decision(call_id: str, verdict: str, seq: int) -> dict[str, object]:
    return {"seq": seq, "node": "root", "type": "gate_decision", "call_id": call_id,
            "decision": {"verdict": verdict, "gate": "taint_floor", "driving_value_id": "v0"}}


class TestCompletionIsNotFailOpen(unittest.TestCase):
    def setUp(self) -> None:
        led = ValueLedger()
        self.v_r = led.mint_constant("DE-LANDLORD", "prompt:r")
        self.v_a = led.mint_constant(1200, "prompt:a")
        self.values = led.values
        self.bind = {"recipient": self.v_r, "amount": self.v_a}
        self.predicate = {"event": "tool_call", "tool": "send_money"}

    def test_allowed_intent_counts_as_executed(self) -> None:
        trace = _trace([_send("c0", self.bind, 0), _decision("c0", "ALLOW", 1)], self.values)
        self.assertTrue(evaluate(self.predicate, trace, {}))

    def test_denied_intent_does_not_count(self) -> None:
        trace = _trace([_send("c0", self.bind, 0), _decision("c0", "DENY", 1)], self.values)
        self.assertFalse(evaluate(self.predicate, trace, {}))

    def test_intent_without_decision_does_not_count(self) -> None:
        # the fail-open: the old evaluator defaulted a decision-less intent to
        # ALLOW, so an incomplete trace could be scored as a violation/success
        trace = _trace([_send("c0", self.bind, 0)], self.values)
        self.assertFalse(evaluate(self.predicate, trace, {}))


class TestCountCardinality(unittest.TestCase):
    def _two_allowed_sends(self) -> dict[str, object]:
        led = ValueLedger()
        v_r = led.mint_constant("DE-LANDLORD", "prompt:r")
        v_a = led.mint_constant(1200, "prompt:a")
        bind = {"recipient": v_r, "amount": v_a}
        events = [
            _send("c0", bind, 0), _decision("c0", "ALLOW", 1),
            _send("c1", bind, 2), _decision("c1", "ALLOW", 3),
        ]
        return _trace(events, led.values)

    def test_default_is_at_least_one(self) -> None:
        self.assertTrue(evaluate({"event": "tool_call", "tool": "send_money"}, self._two_allowed_sends(), {}))

    def test_min_three_is_not_met_by_two(self) -> None:
        pred = {"event": "tool_call", "tool": "send_money", "count": {"min": 3}}
        self.assertFalse(evaluate(pred, self._two_allowed_sends(), {}))

    def test_min_two_is_met_by_two(self) -> None:
        pred = {"event": "tool_call", "tool": "send_money", "count": {"min": 2}}
        self.assertTrue(evaluate(pred, self._two_allowed_sends(), {}))

    def test_max_one_is_exceeded_by_two(self) -> None:
        pred = {"event": "tool_call", "tool": "send_money", "count": {"max": 1}}
        self.assertFalse(evaluate(pred, self._two_allowed_sends(), {}))


class TestRegexAndTypedEquality(unittest.TestCase):
    def _trace_with_recipient(self, value: object) -> dict[str, object]:
        led = ValueLedger()
        v_r = led.mint_constant(value, "prompt:r")
        v_a = led.mint_constant(1200, "prompt:a")
        bind = {"recipient": v_r, "amount": v_a}
        return _trace([_send("c0", bind, 0), _decision("c0", "ALLOW", 1)], led.values)

    def test_invalid_regex_is_a_predicate_error_not_a_crash(self) -> None:
        pred = {"event": "tool_call", "tool": "send_money",
                "where": {"args.recipient": {"matches": "("}}}
        with self.assertRaises(UnsupportedPredicateError):
            evaluate(pred, self._trace_with_recipient("anything"), {})

    def test_boolean_true_does_not_equal_number_one(self) -> None:
        pred = {"event": "tool_call", "tool": "send_money",
                "where": {"args.recipient": {"equal": 1}}}
        self.assertFalse(evaluate(pred, self._trace_with_recipient(True), {}))

    def test_number_one_equals_number_one(self) -> None:
        pred = {"event": "tool_call", "tool": "send_money",
                "where": {"args.recipient": {"equal": 1}}}
        self.assertTrue(evaluate(pred, self._trace_with_recipient(1), {}))


if __name__ == "__main__":
    unittest.main()
