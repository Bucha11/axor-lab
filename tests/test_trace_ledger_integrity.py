"""Trace value-ledger unambiguity (review r13).

`trace_semantics` used to take the values as a SET of value_ids, so a duplicate
value_id was silently deduped; it never checked canonical_value_hash consistency
or event ordering. A conformant-by-schema trace could therefore carry two values
under one id (replay/EvidenceCase pick last-wins), a hash of one payload over a
different one, or events out of seq order — all load-bearing.
"""

from __future__ import annotations

import copy
import unittest

from lab_contracts import content_hash
from lab_contracts.semantics import trace_semantics
from lab_runner import ScriptedAgent, run_trial
from tests import support


def _real_trace() -> dict:
    return run_trial(
        support.banking_scenario(), support.manifests(), support.conditions()[1],
        support.kernel_registry().get(support.KERNEL_PINNED),
        run_id="r", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
    ).trace


class TestLedgerUnambiguity(unittest.TestCase):
    def test_a_clean_real_trace_passes(self) -> None:
        self.assertEqual(trace_semantics(_real_trace()), [])

    def test_duplicate_value_id_is_rejected(self) -> None:
        trace = _real_trace()
        first = copy.deepcopy(trace["values"][0])
        first["labels"] = ["untrusted_derived"]  # a DIFFERENT value under the same id
        trace["values"].append(first)
        errors = trace_semantics(trace)
        self.assertTrue(any("duplicate value_id" in e for e in errors), errors)

    def test_canonical_value_hash_must_match_the_decision_value(self) -> None:
        trace = _real_trace()
        victim = next(v for v in trace["values"] if "decision_value" in v)
        victim["canonical_value_hash"] = content_hash("something-else")
        errors = trace_semantics(trace)
        self.assertTrue(any("does not match" in e for e in errors), errors)

    def test_missing_canonical_value_hash_is_rejected(self) -> None:
        trace = _real_trace()
        del trace["values"][0]["canonical_value_hash"]
        errors = trace_semantics(trace)
        self.assertTrue(any("missing canonical_value_hash" in e for e in errors), errors)

    def test_omitting_decision_value_requires_the_sensitive_label(self) -> None:
        trace = _real_trace()
        victim = next(v for v in trace["values"] if "decision_value" in v)
        del victim["decision_value"]  # now no decision_value, and NOT sensitive
        errors = trace_semantics(trace)
        self.assertTrue(any("not labelled 'sensitive'" in e for e in errors), errors)

    def test_out_of_order_seq_within_a_node_is_rejected(self) -> None:
        trace = _real_trace()
        # swap the first two events so seq goes backwards in array order
        trace["events"][0], trace["events"][1] = trace["events"][1], trace["events"][0]
        errors = trace_semantics(trace)
        self.assertTrue(any("strictly increasing" in e for e in errors), errors)

    def test_duplicate_intent_call_id_is_rejected(self) -> None:
        trace = _real_trace()
        intent = next(e for e in trace["events"] if e.get("type") == "tool_call_intent")
        clone = copy.deepcopy(intent)  # a second intent reusing the same call_id
        clone["seq"] = 999
        trace["events"].append(clone)
        errors = trace_semantics(trace)
        self.assertTrue(any("duplicate tool_call_intent call_id" in e for e in errors), errors)

    def test_shared_intent_decision_call_id_is_allowed(self) -> None:
        # an intent and ITS decision legitimately share one call_id — that is the
        # pairing, and must NOT be flagged as a duplicate
        trace = _real_trace()
        intents = [e for e in trace["events"] if e.get("type") == "tool_call_intent"]
        decisions = [e for e in trace["events"] if e.get("type") == "gate_decision"]
        if intents and decisions and intents[0].get("call_id"):
            self.assertEqual(intents[0]["call_id"], decisions[0]["call_id"])
        self.assertEqual(trace_semantics(trace), [])


class TestFailClosedEvidenceRoundtrips(unittest.TestCase):
    """A fail-closed DENY (no provenance value) must be representable as valid
    evidence: null driving_value_id + a typed driving_unresolved, NOT a fake
    ledger id that would fail validation (review r14)."""

    def _fail_closed(self, unresolved) -> dict:
        trace = copy.deepcopy(_real_trace())
        for e in trace["events"]:
            if e.get("type") == "gate_decision":
                e["decision"]["driving_value_id"] = None
                e["decision"]["driving_unresolved"] = unresolved
        return trace

    def test_no_driving_args_deny_roundtrips_through_bundle(self) -> None:
        from lab_contracts import build_bundle, content_hash, validate_artifact, verify_bundle

        trace = self._fail_closed({"kind": "no_driving_args"})
        self.assertEqual(validate_artifact(trace, "trace"), [])
        self.assertEqual(trace_semantics(trace), [])
        # and it builds + verifies inside a bundle (the incident is publishable)
        trial = {
            "trial_id": content_hash(trace), "scenario_id": str(trace["trial"]["scenario_id"]),
            "condition_id": str(trace["trial"]["condition_id"]), "seed": str(trace["trial"]["seed"]),
            "repeat_index": int(trace["trial"]["repeat_index"]), "status": "completed",
            "trace_ref": content_hash(trace),
        }
        bundle = build_bundle(
            bundle_id="b_fc", created="2026-07-20T12:00:00+00:00",
            scenarios=[support.banking_scenario()], conditions=support.conditions(),
            tool_manifests=list(support.manifests().values()), environment=support.environment(),
            trials=[trial], aggregates=[], traces={str(trace["trace_id"]): trace},
        )
        verify_bundle(bundle, {str(trace["trace_id"]): trace})  # must NOT raise

    def test_unresolved_argument_deny_roundtrips(self) -> None:
        from lab_contracts import validate_artifact

        trace = self._fail_closed({"kind": "unresolved_argument", "arg": "recipient"})
        self.assertEqual(validate_artifact(trace, "trace"), [])
        self.assertEqual(trace_semantics(trace), [])

    def test_null_driving_without_a_reason_is_rejected(self) -> None:
        trace = copy.deepcopy(_real_trace())
        for e in trace["events"]:
            if e.get("type") == "gate_decision":
                e["decision"]["driving_value_id"] = None  # no driving_unresolved
        errors = trace_semantics(trace)
        self.assertTrue(any("without a driving_unresolved reason" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
