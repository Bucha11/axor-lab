"""The cost layer is a HARD ceiling, not just a printed estimate (review r11 P1).

cost.py promised "a shown estimate + a hard ceiling" but only ever multiplied
trials by a fixed token guess and printed it — the CLI then ran to completion.
Now CostBudget enforces a run-wide ceiling against ACTUAL usage between trials
(the run stops before the next provider call), and actual usage/spend is
recorded in the bundle environment.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_agent.cost import CostBudget, actual_usd
from lab_runner import run_experiment_suite


class TestCostBudget(unittest.TestCase):
    def test_unset_budget_never_binds(self) -> None:
        b = CostBudget()
        self.assertFalse(b.is_set())
        self.assertIsNone(b.exceeded({"input_tokens": 10**9, "output_tokens": 10**9}, "claude-opus-4-8"))

    def test_output_token_ceiling(self) -> None:
        b = CostBudget(max_output_tokens=100)
        self.assertIsNone(b.exceeded({"output_tokens": 99}, "claude-opus-4-8"))
        self.assertIsNotNone(b.exceeded({"output_tokens": 100}, "claude-opus-4-8"))  # >= stops AT the cap

    def test_input_token_ceiling(self) -> None:
        b = CostBudget(max_input_tokens=1000)
        self.assertIsNotNone(b.exceeded({"input_tokens": 1000}, "claude-opus-4-8"))

    def test_usd_ceiling_uses_actual_prices(self) -> None:
        b = CostBudget(max_usd=0.01)
        # 1M input tokens on opus is ~$15, well past a 1-cent ceiling
        reason = b.exceeded({"input_tokens": 1_000_000, "output_tokens": 0}, "claude-opus-4-8")
        self.assertIsNotNone(reason)
        self.assertIn("$", reason)

    def test_scripted_is_free(self) -> None:
        self.assertEqual(actual_usd(10_000, 10_000, "scripted"), 0.0)


class TestRunStopsAtCeiling(unittest.TestCase):
    def test_run_stops_early_when_budget_check_trips(self) -> None:
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return "ceiling reached" if calls["n"] >= 2 else None

        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=6, run_id="r_budget", budget_check=check,
        )
        self.assertEqual(result.stopped_reason, "ceiling reached")
        # stopped before the full 2 conditions x 6 repeats = 12 trials
        self.assertLess(len(result.trials), 12)

    def test_no_budget_check_runs_to_completion(self) -> None:
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=3, run_id="r_full",
        )
        self.assertIsNone(result.stopped_reason)
        self.assertEqual(len(result.trials), 2 * 3)


class TestUsageRecorded(unittest.TestCase):
    def test_environment_carries_actual_usage(self) -> None:
        from lab_runner.cli import _environment
        from lab_runner.experiment_file import ResolvedExperiment

        resolved = ResolvedExperiment(
            experiment={"id": "e", "agent_ref": "cassette", "repeats": 1},
            scenarios=(support.banking_scenario(),), manifests=support.manifests(),
            conditions=tuple(support.conditions()), agent=None,  # unused by _environment
            kernel_registry=support.kernel_registry(),
        )
        usage = {"input_tokens": 1800, "output_tokens": 240, "usd": 0.045}
        env = _environment(resolved, "claude-opus-4-8", usage)
        self.assertEqual(env["model"]["inference_params"]["usage"], usage)


if __name__ == "__main__":
    unittest.main()
