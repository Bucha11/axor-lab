"""Acceptance test 10 — regression surfaces change.

Re-running the pinned trace under a hypothetical kernel that flips the
verdict surfaces the change as "differs from pinned expected" — not a silent
pass, not a hard failure. The user labels it regression or approved baseline
update; the pin never mandates DENY forever.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_runner import ScriptedAgent, check_pins, pin, run_trial
from lab_runner.regression import STATUS_DIFFERS, STATUS_MATCHES

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)


class TestRegressionPinning(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = support.banking_scenario()
        self.manifests = support.manifests()
        self.governed = support.conditions()[1]
        self.registry = support.kernel_registry()
        kernel = self.registry.get(support.KERNEL_PINNED)
        self.trace = run_trial(
            self.scenario, self.manifests, self.governed, kernel,
            run_id="r_reg", seed="s007", repeat_index=7, agent=ATTACK_ALWAYS,
        ).trace
        self.traces = {"t": self.trace}
        self.pins = (pin(self.trace, expected_verdict="DENY"),)

    def test_same_kernel_matches_pinned_expected(self) -> None:
        results = check_pins(
            self.pins, self.traces, self.governed,
            self.registry.get(support.KERNEL_PINNED),
            self.manifests, self.scenario["inputs"],  # type: ignore[arg-type]
        )
        self.assertEqual(results[0]["status"], STATUS_MATCHES)

    def test_verdict_flip_is_surfaced_not_silent_and_not_fatal(self) -> None:
        results = check_pins(
            self.pins, self.traces, self.governed,
            self.registry.get(support.KERNEL_NO_TAINT_FLOOR),
            self.manifests, self.scenario["inputs"],  # type: ignore[arg-type]
        )
        result = results[0]
        self.assertEqual(result["status"], STATUS_DIFFERS)
        self.assertEqual(result["expected"], "DENY")
        self.assertEqual(result["actual"], "ALLOW")
        self.assertEqual(result["kernel"], support.KERNEL_NO_TAINT_FLOOR)
        # surfaced for the user to label — never auto-resolved
        self.assertEqual(result["resolution"], "user_labels_required")

    def test_pin_records_the_frozen_trace_identity(self) -> None:
        pinned = self.pins[0]
        self.assertEqual(pinned.trace_id, self.trace["trace_id"])
        self.assertTrue(pinned.trace_ref.startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
