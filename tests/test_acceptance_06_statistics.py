"""Acceptance test 6 — statistics are honest.

ASR uses Wilson; the paired test is McNemar over STORED discordant pairs; a
CI narrows from n=10 to n=100; n<10 shows inconclusive (significance
suppressed); missing trials show the denominator; unit 'round' is rejected.
"""

from __future__ import annotations

import unittest

from lab_ref import (
    binary_aggregate,
    is_inconclusive,
    mcnemar_exact,
    mcnemar_test,
    missingness,
    paired_bootstrap_ci,
    wilson_interval,
)
from lab_ref.stats import UnitOfAnalysisError


class TestWilson(unittest.TestCase):
    def test_zero_successes_stays_off_zero_upper(self) -> None:
        low, high = wilson_interval(0, 30)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.114, places=2)  # the slice's [0, 0.12]

    def test_interval_behaves_near_one(self) -> None:
        low, high = wilson_interval(30, 30)
        self.assertAlmostEqual(high, 1.0, places=9)
        self.assertGreater(low, 0.85)

    def test_interval_narrows_with_n(self) -> None:
        width_10 = (lambda lh: lh[1] - lh[0])(wilson_interval(5, 10))
        width_100 = (lambda lh: lh[1] - lh[0])(wilson_interval(50, 100))
        self.assertLess(width_100, width_10)


class TestMcNemar(unittest.TestCase):
    def test_slice_discordant_pairs_are_significant(self) -> None:
        # b=0 (ungoverned breach, governed breach-too? no: governed fail), c=18
        self.assertLess(mcnemar_exact(0, 18), 0.01)

    def test_no_discordance_is_not_significant(self) -> None:
        self.assertEqual(mcnemar_exact(0, 0), 1.0)

    def test_test_payload_is_computed_from_stored_pairs(self) -> None:
        pairs = [(True, False)] * 18 + [(False, False)] * 12
        test = mcnemar_test(pairs, vs="ungoverned")
        self.assertEqual(test["discordant"], {"b": 18, "c": 0})
        self.assertLess(test["p"], 0.01)  # type: ignore[operator]

    def test_marginals_alone_cannot_reproduce_the_test(self) -> None:
        # same marginals (baseline 15/20, treated 5/20), different pairings,
        # different p — which is WHY the bundle stores the pairing, not two
        # marginal proportions
        pairing_a = [(True, False)] * 10 + [(True, True)] * 5 + [(False, False)] * 5
        pairing_b = (
            [(True, False)] * 12 + [(False, True)] * 2
            + [(True, True)] * 3 + [(False, False)] * 3
        )
        p_a = mcnemar_test(pairing_a, vs="x")["p"]
        p_b = mcnemar_test(pairing_b, vs="x")["p"]
        self.assertNotEqual(p_a, p_b)


class TestBootstrap(unittest.TestCase):
    def test_ci_narrows_with_n(self) -> None:
        small = [0.1, 0.9, 0.3, 0.7, 0.5, 0.2, 0.8, 0.4, 0.6, 0.5]
        large = small * 10
        low_s, high_s = paired_bootstrap_ci(small, seed=1)
        low_l, high_l = paired_bootstrap_ci(large, seed=1)
        self.assertLess(high_l - low_l, high_s - low_s)

    def test_deterministic_under_seed(self) -> None:
        values = [0.2, 0.4, 0.6, 0.8, 1.0, 0.1, 0.3, 0.5, 0.7, 0.9]
        self.assertEqual(paired_bootstrap_ci(values, seed=7), paired_bootstrap_ci(values, seed=7))


class TestAggregateHonesty(unittest.TestCase):
    def test_unit_round_is_rejected(self) -> None:
        with self.assertRaises(UnitOfAnalysisError):
            binary_aggregate("cooperation", "governed", 80, 100, unit_of_analysis="round")

    def test_small_n_is_inconclusive_and_suppresses_significance(self) -> None:
        test = mcnemar_test([(True, False)] * 5, vs="ungoverned")
        aggregate = binary_aggregate("ASR", "governed", 0, 5, test=test)
        self.assertTrue(is_inconclusive(aggregate))
        self.assertNotIn("test", aggregate)

    def test_adequate_n_carries_the_test(self) -> None:
        test = mcnemar_test([(True, False)] * 18 + [(False, False)] * 12, vs="ungoverned")
        aggregate = binary_aggregate("ASR", "governed", 0, 30, test=test)
        self.assertFalse(is_inconclusive(aggregate))
        self.assertEqual(aggregate["test"]["name"], "mcnemar")  # type: ignore[index]
        self.assertEqual(aggregate["unit_of_analysis"], "trial")


class TestMissingness(unittest.TestCase):
    @staticmethod
    def _trial(scenario: str, status: str, reason: str | None = None) -> dict[str, object]:
        trial: dict[str, object] = {
            "trial_id": f"t_{scenario}_{status}", "scenario_id": scenario,
            "condition_id": "governed", "seed": "s0", "repeat_index": 0, "status": status,
        }
        if reason:
            trial["failure_reason"] = reason
        return trial

    def test_denominator_and_reasons_reported(self) -> None:
        trials = [self._trial("a", "completed")] * 228 + [
            self._trial("a", "excluded", "provider 429")
        ] * 12
        summary = missingness(trials)
        self.assertEqual(summary.n_total, 240)
        self.assertEqual(summary.n_completed, 228)
        self.assertIn("n=228/240", summary.display())
        self.assertIn("provider 429", summary.display())

    def test_concentrated_missingness_is_flagged_potentially_biased(self) -> None:
        trials = (
            [self._trial("easy", "completed")] * 50
            + [self._trial("hardest", "completed")] * 40
            + [self._trial("hardest", "failed", "timeout")] * 10
        )
        summary = missingness(trials)
        self.assertTrue(summary.potentially_biased)
        self.assertIn("potentially biased", summary.display())

    def test_spread_missingness_is_not_flagged(self) -> None:
        trials = (
            [self._trial("a", "completed")] * 48 + [self._trial("a", "failed", "timeout")] * 2
            + [self._trial("b", "completed")] * 48 + [self._trial("b", "failed", "timeout")] * 2
        )
        self.assertFalse(missingness(trials).potentially_biased)


if __name__ == "__main__":
    unittest.main()
