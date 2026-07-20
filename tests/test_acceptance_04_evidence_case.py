"""Acceptance test 4 — the EvidenceCase renders three modes from one trace,
and the counterfactual mode is labeled as such (not an observed twin). The
governed twin appears only when a governed run was actually executed."""

from __future__ import annotations

import unittest

from tests import support
from lab_runner import ScriptedAgent, build_evidence_case, run_trial

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)


class TestEvidenceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = support.banking_scenario()
        self.manifests = support.manifests()
        self.ungoverned, self.governed = support.conditions()
        self.kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        self.ungoverned_trace = run_trial(
            self.scenario, self.manifests, self.ungoverned, self.kernel,
            run_id="r_ec", seed="s001", repeat_index=1, agent=ATTACK_ALWAYS,
        ).trace
        self.governed_trace = run_trial(
            self.scenario, self.manifests, self.governed, self.kernel,
            run_id="r_ec", seed="s001", repeat_index=1, agent=ATTACK_ALWAYS,
        ).trace

    def _case(self, twin: dict[str, object] | None) -> dict[str, object]:
        return build_evidence_case(
            self.ungoverned_trace, self.scenario, self.governed, self.kernel,
            self.manifests, governed_twin=twin,
        )

    def test_counterfactual_is_labeled_and_flips_to_deny(self) -> None:
        case = self._case(twin=None)
        counterfactual = case["modes"]["counterfactual_policy_replay"]  # type: ignore[index]
        self.assertEqual(counterfactual["kind"], "counterfactual")
        self.assertEqual(counterfactual["verdicts"], ["DENY"])
        self.assertEqual(counterfactual["claim_kind"], "exactly_replayable")
        self.assertIn("does not assert", counterfactual["caveat"])

    def test_observed_mode_shows_the_recorded_allow(self) -> None:
        case = self._case(twin=None)
        observed = case["modes"]["observed"]  # type: ignore[index]
        self.assertEqual(observed["kind"], "observed")
        self.assertEqual(observed["condition_id"], "ungoverned")
        self.assertEqual(observed["verdicts"], ["ALLOW"])

    def test_governed_twin_absent_when_no_governed_run_exists(self) -> None:
        case = self._case(twin=None)
        self.assertNotIn("observed_governed_twin", case["modes"])  # type: ignore[operator]

    def test_governed_twin_present_only_with_real_data(self) -> None:
        case = self._case(twin=self.governed_trace)
        twin = case["modes"]["observed_governed_twin"]  # type: ignore[index]
        self.assertEqual(twin["kind"], "observed")
        self.assertEqual(twin["verdicts"], ["DENY"])
        self.assertEqual(twin["trace_id"], self.governed_trace["trace_id"])

    def test_chain_walks_injection_to_verdict(self) -> None:
        case = self._case(twin=None)
        chain = case["chain"]  # type: ignore[index]
        self.assertEqual(chain["injection"]["text"], support.INJECTION_TEXT)  # type: ignore[index]
        lineage_ids = [v["value_id"] for v in chain["provenance"]]  # type: ignore[index]
        self.assertGreaterEqual(len(lineage_ids), 2)  # extracted value + its untrusted root
        self.assertEqual(chain["gated_call"]["tool"], "send_money")  # type: ignore[index]
        self.assertIn(chain["verdict"]["verdict"], ("ALLOW", "DENY"))  # type: ignore[index]

    def test_content_independence_note_present(self) -> None:
        case = self._case(twin=None)
        self.assertIn("content-independent", str(case["note"]))

    def test_heuristic_fidelity_renders_a_warning(self) -> None:
        degraded = support.deep(self.ungoverned_trace)
        degraded["producer"]["provenance_fidelity"] = "heuristic_attribution"  # type: ignore[index]
        case = build_evidence_case(
            degraded, self.scenario, self.governed, self.kernel, self.manifests
        )
        self.assertIn("fidelity_warning", case)
        self.assertEqual(case["fidelity"]["claimed"], "heuristic_attribution")  # type: ignore[index]
        self.assertEqual(case["fidelity"]["verified"], "heuristic_attribution")  # type: ignore[index]

    def test_self_reported_explicit_fidelity_is_not_presented_as_verified(self) -> None:
        # a producer can WRITE provenance_fidelity=explicit_flow_tracked, but the
        # bundle carries no attestation that a trusted runtime tracked the flow —
        # so the EvidenceCase must NOT render it as sound: verified downgrades to
        # self_reported and a warning still fires (review r13)
        claimed = support.deep(self.ungoverned_trace)
        claimed["producer"]["provenance_fidelity"] = "explicit_flow_tracked"  # type: ignore[index]
        case = build_evidence_case(
            claimed, self.scenario, self.governed, self.kernel, self.manifests
        )
        self.assertEqual(case["fidelity"]["claimed"], "explicit_flow_tracked")  # type: ignore[index]
        self.assertEqual(case["fidelity"]["verified"], "self_reported")  # type: ignore[index]
        self.assertIn("fidelity_warning", case)  # a forged claim is NOT silently sound
        self.assertIn("SELF-REPORTED", case["fidelity_warning"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
