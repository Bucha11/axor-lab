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
from lab_runner import ScriptedAgent
from lab_capabilities.governance import run_trial

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
        # the wrap engine gates the read too, so pick the SINK intent (send_money),
        # not the first intent (the read, which binds no recipient)
        call = next(e for e in trace["events"]  # type: ignore[union-attr]
                    if e.get("type") == "tool_call_intent" and e.get("tool") == "send_money")
        recipient_id = call["arg_bindings"]["recipient"]  # type: ignore[index]
        value = next(v for v in trace["values"] if v["value_id"] == recipient_id)  # type: ignore[union-attr]
        self.assertIn("untrusted_derived", value["labels"])
        self.assertIn("model_extraction", value["transformations"])
        # the recipient's OWN source names the taint token; its lineage back to
        # the read is the `derived_from` edge, and the values it derives from are
        # the read's external_read values (origin_ref tool_result:read_txns:*)
        self.assertTrue(value["derived_from"])
        by_id = {v["value_id"]: v for v in trace["values"]}  # type: ignore[union-attr]
        read_refs = [
            s.get("origin_ref", "")
            for did in value["derived_from"]
            for s in by_id[str(did)]["sources"]
        ]
        self.assertTrue(any(ref.startswith("tool_result:read_txns:") for ref in read_refs))
        untrusted_ids = {
            v["value_id"] for v in trace["values"] if "untrusted_derived" in v["labels"]  # type: ignore[union-attr]
        }
        self.assertTrue(set(value["derived_from"]).issubset(untrusted_ids))  # type: ignore[arg-type]

    def test_deny_decision_reads_the_recipient_provenance(self) -> None:
        trace = _governed_trace(ATTACK_ALWAYS)
        # the DENY is the SINK's verdict; the read is an ALLOW that precedes it
        decision = next(e["decision"] for e in trace["events"]  # type: ignore[union-attr, index]
                        if e.get("type") == "gate_decision" and e["decision"]["verdict"] == "DENY")
        call = next(e for e in trace["events"]  # type: ignore[union-attr]
                    if e.get("type") == "tool_call_intent" and e.get("tool") == "send_money")
        self.assertEqual(decision["verdict"], "DENY")
        self.assertEqual(decision["gate"], "taint_floor")
        self.assertEqual(decision["driving_value_id"], call["arg_bindings"]["recipient"])  # type: ignore[index]
        self.assertEqual(decision["projection"], "untrusted-derived")

    def test_faithful_trial_is_allowed_via_declared_allowlist(self) -> None:
        trace = _governed_trace(FAITHFUL_ALWAYS)
        # the SINK's verdict specifically (the read is also ALLOW)
        decision = next(
            e["decision"] for e in trace["events"]  # type: ignore[union-attr, index]
            if e.get("type") == "gate_decision"
            and next(i for i in trace["events"]  # type: ignore[union-attr]
                     if i.get("type") == "tool_call_intent"
                     and i.get("call_id") == e.get("call_id"))["tool"] == "send_money"
        )
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
