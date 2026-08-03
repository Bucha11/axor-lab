"""Experimental design is first-class: live BYOK is not paired (review r4).

McNemar is a PAIRED test; it is valid only when the two conditions are real
matched pairs — a deterministic agent whose behavior is fixed by scenario+seed.
A live model draws each condition independently, so its 'pairs' are nominal and
McNemar would manufacture a spurious paired p-value. The runner now picks the
test from the effective comparison design and marks independent-samples results
exploratory; the stats engine validates its inputs.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from lab_analysis import (
    binary_aggregate,
    mcnemar_test,
    two_proportion_test,
    wilson_interval,
)
from lab_analysis.errors import InsufficientDataError
from lab_runner import ScriptedAgent, run_experiment_suite
from lab_runner.cli import _aggregates, _effective_design
from lab_runner.errors import RunnerError
from lab_runner.experiment_file import load_axl, resolve

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "banking-exfil-01.axl"


class _LiveAgent(ScriptedAgent):
    is_deterministic = False  # stand-in for a live-model backend


class TestStatsInputValidation(unittest.TestCase):
    def test_wilson_rejects_successes_over_n(self) -> None:
        with self.assertRaises(InsufficientDataError):
            wilson_interval(11, 10)

    def test_mcnemar_negative_counts_rejected(self) -> None:
        with self.assertRaises(InsufficientDataError):
            from lab_analysis import mcnemar_exact
            mcnemar_exact(-1, 3)

    def test_binary_aggregate_rejects_test_with_more_pairs_than_trials(self) -> None:
        # a test cannot have been computed over MORE pairs than the aggregate's n
        test = mcnemar_test([(True, False)] * 10, vs="ungoverned")
        self.assertEqual(test["paired_n"], 10)
        with self.assertRaises(InsufficientDataError):
            binary_aggregate("ASR", "governed", 0, 5, test=test)

    def test_paired_n_is_recorded_for_transparency(self) -> None:
        # paired_n rides on the test so a reader sees the actual test sample,
        # even when it is smaller than the marginal aggregate n
        test = mcnemar_test([(True, False)] * 12, vs="ungoverned")
        agg = binary_aggregate("ASR", "governed", 0, 12, test=test)
        self.assertEqual(agg["test"]["paired_n"], 12)


class TestTwoProportionTest(unittest.TestCase):
    def test_shape_and_naming(self) -> None:
        test = two_proportion_test(6, 10, 0, 10, vs="ungoverned")
        self.assertEqual(test["name"], "two_proportion")  # NOT mcnemar
        self.assertEqual(test["design"], "independent_samples")
        self.assertAlmostEqual(test["difference"], 0.6)
        self.assertIn("interval", test)
        self.assertLess(test["p"], 0.05)


class TestEffectiveDesign(unittest.TestCase):
    def _resolved(self):
        return resolve(load_axl(EXAMPLE))

    def test_scripted_is_matched_pairs(self) -> None:
        self.assertEqual(_effective_design(self._resolved(), ScriptedAgent()), "matched_pairs")

    def test_live_agent_is_independent_samples(self) -> None:
        self.assertEqual(_effective_design(self._resolved(), _LiveAgent()), "independent_samples")

    def test_declared_matched_pairs_with_live_agent_is_rejected(self) -> None:
        resolved = self._resolved()
        resolved.experiment["comparison_design"] = {"kind": "matched_pairs"}
        with self.assertRaises(RunnerError):
            _effective_design(resolved, _LiveAgent())


class TestAggregatesPickTheRightTest(unittest.TestCase):
    def _run(self, agent):
        resolved = resolve(load_axl(EXAMPLE))
        result = run_experiment_suite(
            list(resolved.scenarios), resolved.manifests, list(resolved.conditions),
            resolved.kernel_registry, repeats=resolved.repeats, run_id="r_cd", agent=agent,
        )
        return resolved, result

    def _asr_treated(self, aggregates):
        return next(
            a for a in aggregates
            if a["metric"] == "ASR" and a["condition_id"] == "governed"
        )

    def test_scripted_uses_mcnemar(self) -> None:
        resolved, result = self._run(ScriptedAgent())
        agg = self._asr_treated(_aggregates(resolved, result, ScriptedAgent()))
        self.assertEqual(agg["comparison_design"], "matched_pairs")
        if "test" in agg:  # present unless n<10
            self.assertEqual(agg["test"]["name"], "mcnemar")

    def test_live_uses_two_proportion_not_mcnemar(self) -> None:
        resolved, result = self._run(_LiveAgent())
        agg = self._asr_treated(_aggregates(resolved, result, _LiveAgent()))
        self.assertEqual(agg["comparison_design"], "independent_samples")
        if "test" in agg:
            self.assertEqual(agg["test"]["name"], "two_proportion")


if __name__ == "__main__":
    unittest.main()
