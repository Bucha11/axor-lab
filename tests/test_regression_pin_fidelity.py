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
from lab_runner import ScriptedAgent
from lab_capabilities.governance import (
    check_pins,
    difference_reason,
    pin,
    run_trial,
)

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
        # the real kernel BAKES an `$inputs.x` allowlist into its config at
        # RESOLVE time (the reference kernel re-expanded it from `inputs` at
        # replay). So per-scenario inputs are threaded via `kernel_for`, which
        # resolves each trace's own kernel — a fixed `inputs_for` alone can no
        # longer change an already-compiled governor.
        from lab_capabilities.governance import governor_config
        from lab_capabilities.governance.axor_backend import AxorKernel

        manifests = support.manifests()
        # an input-backed allowlist, so the verdict genuinely depends on the
        # scenario's known_ibans
        policy = {"profile": "strict", "trust_model": "content-ledger",
                  "allowlist": ["$inputs.known_ibans"]}
        cond = {**support.conditions()[1], "policy": policy}

        def kernel_for(inputs):
            return AxorKernel(version=support.KERNEL_PINNED,
                              config=governor_config(manifests, policy, inputs))

        # scenario B: the attacker IBAN is NOT allowlisted → untrusted egress → DENY
        scen_b = self._scenario([support.LANDLORD_IBAN])
        kernel_b = kernel_for(scen_b["inputs"])
        trace = run_trial(scen_b, manifests, cond, kernel_b, run_id="r", seed="s000",
                          repeat_index=0, agent=ATTACK).trace
        traces = {str(trace["trace_id"]): trace}
        p = pin(trace, "DENY")
        self.assertEqual(p.expected_sequence[-1], "DENY")

        # replay under B's OWN kernel (its inputs) → matches
        ok = check_pins((p,), traces, cond, kernel_b, manifests,
                        kernel_for=lambda t: kernel_b)
        self.assertEqual(ok[0]["status"], "matches_pinned_expected")

        # replay under a DIFFERENT scenario's kernel (attacker IBAN allowlisted →
        # value-policy supersession → ALLOW) → a FALSE regression, the exact bug
        # when one shared inputs/kernel is used for every pin
        scen_a_inputs = {**scen_b["inputs"], "known_ibans": [support.ATTACKER_IBAN]}  # type: ignore[dict-item]
        kernel_a = kernel_for(scen_a_inputs)
        wrong = check_pins((p,), traces, cond, kernel_b, manifests,
                           kernel_for=lambda t: kernel_a)
        self.assertEqual(wrong[0]["status"], "differs_from_pinned_expected")
        self.assertEqual(wrong[0]["actual"], "ALLOW")


class TestPinVerdictConsistency(unittest.TestCase):
    """A pin cannot assert a headline verdict the frozen trace never produced
    (review r13)."""

    def test_pin_rejects_a_verdict_that_contradicts_the_final_recorded_one(self) -> None:
        deny_trace = _synthetic_multi_decision_trace()  # sequence ends in DENY
        with self.assertRaises(ValueError) as ctx:
            pin(deny_trace, "ALLOW")
        self.assertIn("final recorded verdict", str(ctx.exception))
        # the consistent pin is fine
        self.assertEqual(pin(deny_trace, "DENY").expected_sequence[-1], "DENY")


class TestRegressionHonorsReplayStatus(unittest.TestCase):
    """A structurally MALFORMED trace whose recomputed verdict sequence happens
    to equal the pin must NOT be reported as a match (review r13)."""

    def _governed(self):
        return support.conditions()[1], support.kernel_registry().get(support.KERNEL_PINNED)

    def test_malformed_trace_is_not_a_match_even_if_the_sequence_coincides(self) -> None:
        cond, kernel = self._governed()
        manifests = support.manifests()
        scen = copy.deepcopy(support.banking_scenario())
        scen["inputs"]["known_ibans"] = [support.LANDLORD_IBAN]  # type: ignore[index]
        trace = run_trial(scen, manifests, cond, kernel, run_id="r", seed="s000",
                          repeat_index=0, agent=ATTACK).trace
        # read ALLOW then sink DENY (the wrap engine gates the read too)
        self.assertEqual(pin(trace, "DENY").expected_sequence, ("ALLOW", "DENY"))

        # corrupt it: append a leftover tool_call_intent with no matching
        # decision → replay flags MALFORMED_TRACE, yet the recomputed verdict
        # sequence is still [DENY], which would falsely satisfy a [DENY] pin
        malformed = copy.deepcopy(trace)
        malformed["events"].append({  # type: ignore[union-attr]
            "seq": 99, "node": "root", "type": "tool_call_intent",
            "tool": "send_money", "call_id": "call_orphan", "arg_bindings": {},
        })
        p = pin(malformed, "DENY")  # re-pin so the content hash matches (not TAMPERED)
        traces = {str(malformed["trace_id"]): malformed}
        res = check_pins((p,), traces, cond, kernel, manifests,
                         inputs_for=lambda t: scen["inputs"])
        self.assertEqual(res[0]["status"], "pinned_trace_malformed")
        self.assertNotEqual(res[0]["status"], "matches_pinned_expected")
        # …and it says WHY, rather than leaving the caller to infer it from a
        # status word whose headline verdicts look unremarkable
        self.assertEqual(res[0]["replay_status"], "malformed_trace")
        self.assertIn("cannot be replayed", str(difference_reason(res[0])))


if __name__ == "__main__":
    unittest.main()


class TestDifferenceReasonIsStated(unittest.TestCase):
    """`status` said a pin differs; nothing said WHAT differs.

    A pin matches only when the replay was exact AND the whole ordered verdict
    sequence equals the pin. So it can fail on either count, while `expected`
    and `actual` — single headline verdicts — stay identical. `axor-lab regress`
    printed exactly that on a real bundle:

        ...: expected DENY, got DENY under axor-core@0.10.2
             -> differs_from_pinned_expected

    which reads as the tool contradicting itself. Both causes were already
    computed; neither reached the result dict (`replay_status` was dropped) or
    the CLI (which printed only the headline verdicts).
    """

    def _pinned_bundle(self):
        from lab_capabilities.governance import governor_config
        from lab_capabilities.governance.axor_backend import AxorKernel

        manifests = support.manifests()
        policy = {"profile": "strict", "trust_model": "content-ledger",
                  "allowlist": ["$inputs.known_ibans"]}
        cond = {**support.conditions()[1], "policy": policy}
        scenario = copy.deepcopy(support.banking_scenario())
        scenario["inputs"]["known_ibans"] = [support.LANDLORD_IBAN]  # type: ignore[index]
        kernel = AxorKernel(
            version=support.KERNEL_PINNED,
            config=governor_config(manifests, policy, scenario["inputs"]),
        )
        trace = run_trial(scenario, manifests, cond, kernel, run_id="r",
                          seed="s000", repeat_index=0, agent=ATTACK).trace
        return {str(trace["trace_id"]): trace}, pin(trace, "DENY"), cond, kernel, manifests

    def test_a_clean_match_has_no_reason(self) -> None:
        traces, p, cond, kernel, manifests = self._pinned_bundle()
        result = check_pins((p,), traces, cond, kernel, manifests,
                            kernel_for=lambda t: kernel)[0]
        self.assertEqual(result["status"], "matches_pinned_expected")
        self.assertEqual(result["replay_status"], "match")
        self.assertIsNone(difference_reason(result))

    def test_a_missing_pin_says_it_is_missing(self) -> None:
        _, p, cond, kernel, manifests = self._pinned_bundle()
        result = check_pins((p,), {}, cond, kernel, manifests)[0]
        self.assertEqual(result["status"], "pinned_trace_missing")
        self.assertIn("not in this bundle", str(difference_reason(result)))

    def test_a_tampered_pin_says_the_hash_moved(self) -> None:
        traces, p, cond, kernel, manifests = self._pinned_bundle()
        edited = copy.deepcopy(next(iter(traces.values())))
        edited["producer"] = {"tampered": True}
        result = check_pins((p,), {str(edited["trace_id"]): edited}, cond,
                            kernel, manifests)[0]
        self.assertEqual(result["status"], "pinned_trace_tampered")
        self.assertIn("content hash", str(difference_reason(result)))

    def test_identical_headline_verdicts_still_state_a_cause(self) -> None:
        """The reported bug: same headline verdict, real difference underneath."""
        for result in (
            {  # the sequence moved; only its LAST verdict is the headline one
                "status": "differs_from_pinned_expected",
                "expected": "DENY", "actual": "DENY",
                "expected_sequence": ["ALLOW", "DENY"],
                "actual_sequence": ["DENY", "DENY"],
                "replay_status": "match",
            },
            {  # the sequence is equal; the replay was not a reproduction
                "status": "differs_from_pinned_expected",
                "expected": "DENY", "actual": "DENY",
                "expected_sequence": ["ALLOW", "DENY"],
                "actual_sequence": ["ALLOW", "DENY"],
                "replay_status": "mismatch",
            },
        ):
            with self.subTest(replay_status=result["replay_status"]):
                reason = difference_reason(result)
                self.assertIsNotNone(reason)
                self.assertTrue(str(reason).strip())
                # it must not be the empty-shaped fallback
                self.assertNotIn("unexplained", str(reason))

    def test_the_sequence_cause_names_both_sequences(self) -> None:
        reason = str(difference_reason({
            "status": "differs_from_pinned_expected",
            "expected": "DENY", "actual": "DENY",
            "expected_sequence": ["ALLOW", "DENY"],
            "actual_sequence": ["DENY", "DENY"],
            "replay_status": "match",
        }))
        self.assertIn("[ALLOW, DENY]", reason)
        self.assertIn("[DENY, DENY]", reason)

    def test_an_inexact_replay_is_named_even_when_the_sequence_is_equal(self) -> None:
        reason = str(difference_reason({
            "status": "differs_from_pinned_expected",
            "expected": "DENY", "actual": "DENY",
            "expected_sequence": ["DENY"], "actual_sequence": ["DENY"],
            "replay_status": "redacted_input_unavailable",
        }))
        self.assertIn("redacted", reason)
        self.assertIn("not an exact reproduction", reason)

    def test_both_causes_are_stated_when_both_apply(self) -> None:
        reason = str(difference_reason({
            "status": "differs_from_pinned_expected",
            "expected": "DENY", "actual": "ALLOW",
            "expected_sequence": ["DENY"], "actual_sequence": ["ALLOW"],
            "replay_status": "redacted_input_unavailable",
        }))
        self.assertIn("verdict sequence changed", reason)
        self.assertIn("redacted", reason)
