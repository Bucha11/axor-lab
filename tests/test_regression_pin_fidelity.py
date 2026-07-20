"""Regression pins keep the full verdict sequence and per-scenario inputs (r12).

Two CLI-level bugs made `axor-lab regress` unreliable:
  1. `pin` persisted only expected_verdict, so a multi-call trace's real
     sequence (ALLOW, ALLOW, DENY) was compared to a singleton (DENY) and cried
     regression on an unchanged trace/kernel.
  2. every pin replayed under the FIRST pin's scenario inputs, so a pin from a
     scenario with a different allowlist / effect-resolution input produced a
     false regression or a false pass.
The regression MODEL already supported both; these tests lock the fix in.
"""

from __future__ import annotations

import copy
import unittest

from tests import support
from lab_contracts import content_hash
from lab_runner import ScriptedAgent, pin, check_pins, run_trial

ATTACK = ScriptedAgent(attack_rate=1.0)


def _synthetic_multi_decision_trace() -> dict[str, object]:
    return {
        "trace_id": "t_multi",
        "events": [
            {"type": "gate_decision", "decision": {"verdict": "ALLOW"}},
            {"type": "gate_decision", "decision": {"verdict": "ALLOW"}},
            {"type": "gate_decision", "decision": {"verdict": "DENY"}},
        ],
    }


class TestPinSequenceFidelity(unittest.TestCase):
    def test_pin_records_the_whole_ordered_sequence(self) -> None:
        p = pin(_synthetic_multi_decision_trace(), "DENY")
        self.assertEqual(p.expected_sequence, ("ALLOW", "ALLOW", "DENY"))
        # a serialized pin file round-trips the sequence (what the CLI persists)
        serialized = {"trace_id": p.trace_id, "trace_ref": p.trace_ref,
                      "expected_verdict": p.expected_verdict,
                      "expected_sequence": list(p.expected_sequence)}
        self.assertEqual(serialized["expected_sequence"], ["ALLOW", "ALLOW", "DENY"])


class TestPerScenarioInputs(unittest.TestCase):
    def _governed(self):
        return support.conditions()[1], support.kernel_registry().get(support.KERNEL_PINNED)

    def _scenario(self, known_ibans):
        scen = copy.deepcopy(support.banking_scenario())
        scen["inputs"]["known_ibans"] = known_ibans  # type: ignore[index]
        return scen

    def test_pin_replays_against_its_own_scenario_inputs(self) -> None:
        cond, kernel = self._governed()
        manifests = support.manifests()
        # scenario B: the attacker IBAN is NOT allowlisted → egress → DENY
        scen_b = self._scenario([support.LANDLORD_IBAN])
        trace = run_trial(scen_b, manifests, cond, kernel, run_id="r", seed="s000",
                          repeat_index=0, agent=ATTACK).trace
        traces = {str(trace["trace_id"]): trace}
        p = pin(trace, "DENY")
        self.assertEqual(p.expected_sequence[-1], "DENY")

        # replay under B's OWN inputs → matches
        ok = check_pins((p,), traces, cond, kernel, manifests,
                        inputs_for=lambda t: scen_b["inputs"])
        self.assertEqual(ok[0]["status"], "matches_pinned_expected")

        # replay under a DIFFERENT scenario's inputs (attacker IBAN allowlisted →
        # effect resolves to WRITE, not egress → ALLOW) → a FALSE regression, the
        # exact bug when one shared inputs dict is used for every pin
        scen_a_inputs = {**scen_b["inputs"], "known_ibans": [support.ATTACKER_IBAN]}  # type: ignore[dict-item]
        wrong = check_pins((p,), traces, cond, kernel, manifests,
                           inputs_for=lambda t: scen_a_inputs)
        self.assertEqual(wrong[0]["status"], "differs_from_pinned_expected")
        self.assertEqual(wrong[0]["actual"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
