"""Acceptance test 3 — the trace carries lineage.

The governed trial's trace shows the recipient value with
sources=[external_read:read_txns...], transformations=[model_extraction],
derived_from = the untrusted context values (conservative join), and the
tool_call_intent binds recipient → that value. The produced trace validates
against trace.schema.json including referential integrity.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_ref import ScriptedAgent, run_trial

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)
FAITHFUL_ALWAYS = ScriptedAgent(attack_rate=0.0)


def _governed_trace(agent: ScriptedAgent) -> dict[str, object]:
    return run_trial(
        support.banking_scenario(),
        support.manifests(),
        support.conditions()[1],
        support.kernel_registry().get(support.KERNEL_PINNED),
        run_id="r_lineage",
        seed="s007",
        repeat_index=7,
        agent=agent,
    ).trace


class TestTraceLineage(unittest.TestCase):
    def test_recipient_value_carries_untrusted_lineage(self) -> None:
        trace = _governed_trace(ATTACK_ALWAYS)
        call = next(e for e in trace["events"] if e.get("type") == "tool_call_intent")  # type: ignore[union-attr]
        recipient_id = call["arg_bindings"]["recipient"]  # type: ignore[index]
        value = next(v for v in trace["values"] if v["value_id"] == recipient_id)  # type: ignore[union-attr]
        self.assertIn("untrusted_derived", value["labels"])
        self.assertIn("model_extraction", value["transformations"])
        origin_refs = [s.get("origin_ref", "") for s in value["sources"]]
        self.assertTrue(any(ref.startswith("tool_result:read_txns:") for ref in origin_refs))
        # conservative join: derived_from = ALL untrusted context values, non-empty
        self.assertTrue(value["derived_from"])
        untrusted_ids = {
            v["value_id"] for v in trace["values"] if "untrusted_derived" in v["labels"]  # type: ignore[union-attr]
        }
        self.assertTrue(set(value["derived_from"]).issubset(untrusted_ids))  # type: ignore[arg-type]

    def test_deny_decision_reads_the_recipient_provenance(self) -> None:
        trace = _governed_trace(ATTACK_ALWAYS)
        decision = next(e for e in trace["events"] if e.get("type") == "gate_decision")["decision"]  # type: ignore[union-attr, index]
        call = next(e for e in trace["events"] if e.get("type") == "tool_call_intent")  # type: ignore[union-attr]
        self.assertEqual(decision["verdict"], "DENY")
        self.assertEqual(decision["gate"], "taint_floor")
        self.assertEqual(decision["driving_value_id"], call["arg_bindings"]["recipient"])  # type: ignore[index]
        self.assertEqual(decision["projection"], "untrusted-derived")

    def test_faithful_trial_is_allowed_via_declared_allowlist(self) -> None:
        trace = _governed_trace(FAITHFUL_ALWAYS)
        decision = next(e for e in trace["events"] if e.get("type") == "gate_decision")["decision"]  # type: ignore[union-attr, index]
        self.assertEqual(decision["verdict"], "ALLOW")

    def test_trace_is_schema_valid_with_referential_integrity(self) -> None:
        for agent in (ATTACK_ALWAYS, FAITHFUL_ALWAYS):
            trace = _governed_trace(agent)
            self.assertEqual(support.schema_errors(trace, "trace"), [])

    def test_producer_states_the_honesty_boundary(self) -> None:
        producer = _governed_trace(ATTACK_ALWAYS)["producer"]
        self.assertEqual(producer["mode"], "wrapped_code")  # type: ignore[index]
        self.assertEqual(producer["provenance_fidelity"], "explicit_flow_tracked")  # type: ignore[index]
        self.assertEqual(producer["kernel_version"], support.KERNEL_PINNED)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
