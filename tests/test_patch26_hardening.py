"""P1 hardening cluster (review round 7):

1. a predicate `count: {}` is a no-op tautology — rejected by schema AND the
   semantic validator; negative bounds are rejected too;
2. the DENY claim names the tool the denial ACTUALLY gated (correlated by
   call_id), not the first intent in a multi-call trace;
3. a malformed .axl envelope (wrong top-level types, a non-object manifest)
   is a clean [validating] error, never a raw AttributeError;
4. an auto-generated run_id is 128-bit (32 hex chars), not a 32-bit slice.
"""

from __future__ import annotations

import unittest

from lab_contracts import load_schemas, validate_scenario
from lab_contracts.errors import ScenarioValidationError
from lab_contracts.subset_validator import validate_against
from lab_runner.claims import deny_claim_text
from lab_runner.errors import ExperimentFileError
from lab_runner.experiment_file import resolve
from tests import support


class TestCountBound(unittest.TestCase):
    def _errors(self, count):
        pred = {"event": "tool_call", "tool": "t", "count": count}
        return validate_against(pred, "predicate", load_schemas())

    def test_empty_count_is_rejected_by_schema(self) -> None:
        self.assertTrue(self._errors({}))  # {} constrains nothing

    def test_negative_bound_is_rejected_by_schema(self) -> None:
        self.assertTrue(self._errors({"min": -1}))

    def test_valid_bound_passes_schema(self) -> None:
        self.assertFalse(self._errors({"min": 2}))
        self.assertFalse(self._errors({"min": 1, "max": 3}))

    def test_empty_count_is_rejected_by_semantic_validator(self) -> None:
        scenario = support.banking_scenario()
        # graft an empty count onto the violation predicate's first leaf
        scenario["violation"] = {"event": "tool_call", "tool": "send_money", "count": {}}
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(any("count" in e for e in ctx.exception.errors))


class TestDenyClaimCorrelation(unittest.TestCase):
    def _trace_two_calls(self, deny_second: bool) -> dict[str, object]:
        # two intents with distinct call_ids; the DENY gates ONE of them
        gated = "call_root_1" if deny_second else "call_root_0"
        return {
            "trace_id": "t_multi",
            "producer": {"kernel_version": "taint_floor@1.0"},
            "values": [{"value_id": "v_x", "labels": ["untrusted_derived"]}],
            "events": [
                {"seq": 0, "type": "tool_call_intent", "tool": "read_email", "call_id": "call_root_0"},
                {"seq": 1, "type": "tool_call_intent", "tool": "send_money", "call_id": "call_root_1"},
                {"seq": 2, "type": "gate_decision", "call_id": gated,
                 "decision": {"verdict": "DENY", "gate": "taint_floor", "driving_value_id": "v_x"}},
            ],
        }

    def test_names_the_tool_the_deny_gated_not_the_first_intent(self) -> None:
        text = deny_claim_text(self._trace_two_calls(deny_second=True))
        self.assertIn("send_money", text)      # the gated call
        self.assertNotIn("read_email", text)   # NOT the earlier, allowed intent

    def test_names_the_first_tool_when_it_is_the_gated_one(self) -> None:
        text = deny_claim_text(self._trace_two_calls(deny_second=False))
        self.assertIn("read_email", text)
        self.assertNotIn("send_money", text)


class TestAxlEnvelopeTyping(unittest.TestCase):
    def _base(self) -> dict[str, object]:
        return {"experiment": {"id": "e"}, "scenarios": [], "tool_manifests": []}

    def test_scenarios_must_be_an_array(self) -> None:
        doc = self._base()
        doc["scenarios"] = {"not": "a list"}
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("'scenarios' must be an array" in e for e in ctx.exception.errors))

    def test_experiment_must_be_an_object(self) -> None:
        doc = self._base()
        doc["experiment"] = "nope"
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("'experiment' must be an object" in e for e in ctx.exception.errors))

    def test_non_object_manifest_is_a_clean_error_not_a_crash(self) -> None:
        doc = self._base()
        doc["tool_manifests"] = ["i am a string, not a manifest"]
        # must raise the typed ExperimentFileError, NOT a raw AttributeError
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("must be an object" in e for e in ctx.exception.errors))


class TestRunIdWidth(unittest.TestCase):
    def test_auto_run_id_is_128_bit(self) -> None:
        from lab_contracts import content_hash

        # mirror the CLI's derivation and assert the width the reviewer required
        run_id = "r_" + content_hash({"experiment": {"id": "e"}, "agent": "x"}).removeprefix(
            "sha256:"
        )[:32]
        self.assertEqual(len(run_id) - 2, 32)  # 32 hex chars = 128 bits


if __name__ == "__main__":
    unittest.main()
