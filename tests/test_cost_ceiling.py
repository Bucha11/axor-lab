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
from lab_agent.backends import FINAL, TOOL_CALL, CassetteBackend, ModelAction
from lab_agent.cost import CostBudget, actual_usd
from lab_agent.wrapped import WrappedModelAgent
from lab_runner import run_experiment_suite
from lab_runner.errors import CostCeilingReached


class TestCostBudget(unittest.TestCase):
    def test_unset_budget_never_binds(self) -> None:
        b = CostBudget()
        self.assertFalse(b.is_set())
        self.assertIsNone(b.exceeded({"input_tokens": 10**9, "output_tokens": 10**9}, "claude-opus-4-8"))

    def test_zero_or_negative_limits_are_rejected(self) -> None:
        for kwargs in ({"max_usd": 0}, {"max_usd": -1.0}, {"max_input_tokens": 0},
                       {"max_output_tokens": -5}):
            with self.assertRaises(ValueError):
                CostBudget(**kwargs)  # type: ignore[arg-type]

    def test_remaining_output_tokens_counts_down(self) -> None:
        b = CostBudget(max_output_tokens=100)
        self.assertEqual(b.remaining_output_tokens({"output_tokens": 30}), 70)
        self.assertEqual(b.remaining_output_tokens({"output_tokens": 200}), 0)  # never negative
        self.assertIsNone(CostBudget(max_input_tokens=10).remaining_output_tokens({}))

    def test_overshot_is_strictly_past_the_ceiling(self) -> None:
        b = CostBudget(max_output_tokens=100)
        self.assertFalse(b.is_overshot({"output_tokens": 100}, "claude-opus-4-8"))  # AT = reached
        self.assertTrue(b.is_overshot({"output_tokens": 101}, "claude-opus-4-8"))   # past = overshot

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


class TestWithinTrialGuard(unittest.TestCase):
    """The ceiling is checked BEFORE every provider call, so a single trial's
    multi-turn loop cannot overshoot the budget by up to _MAX_TURNS calls before
    the between-trials check ever runs (review r12)."""

    def _sink_manifest(self) -> dict:
        return support.manifests()["send_money"]

    def test_agent_stops_mid_loop_before_the_breaching_call(self) -> None:
        # 8 non-sink calls: CassetteBackend charges output tokens per call
        # (cursor*8), so a 20-token ceiling is reached partway through the loop
        records = [{"tool": "read_more", "args": {}} for _ in range(8)]
        backend = CassetteBackend.from_records(records)
        agent = WrappedModelAgent(
            backend=backend, budget=CostBudget(max_output_tokens=20), model="claude-opus-4-8"
        )
        with self.assertRaises(CostCeilingReached) as ctx:
            agent.decide_sink_call("task", "read result", {}, self._sink_manifest())
        self.assertIn("output tokens", ctx.exception.reason)
        self.assertLess(backend._cursor, 8)  # stopped BEFORE exhausting the turns

    def test_no_budget_does_not_stop_the_loop_early(self) -> None:
        # same transcript, no budget → the loop runs until it gives up (never
        # calls the sink) — proving the ceiling, not exhaustion, stopped it above
        from lab_agent.errors import ProtocolViolation

        records = [{"tool": "read_more", "args": {}} for _ in range(8)]
        agent = WrappedModelAgent(backend=CassetteBackend.from_records(records))
        with self.assertRaises(ProtocolViolation):
            agent.decide_sink_call("task", "read result", {}, self._sink_manifest())

    def test_suite_stops_when_a_trial_trips_the_ceiling(self) -> None:
        # a driving agent whose first trial exhausts the output ceiling stops the
        # WHOLE run — it is not captured as one failed trial and continued
        records = [{"tool": "read_more", "args": {}} for _ in range(8)]
        agent = WrappedModelAgent(
            backend=CassetteBackend.from_records(records),
            budget=CostBudget(max_output_tokens=20), model="claude-opus-4-8",
        )
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=6, run_id="r_overshoot", agent=agent,
        )
        self.assertIsNotNone(result.stopped_reason)
        self.assertIn("cost ceiling", result.stopped_reason)
        self.assertEqual(len(result.trials), 0)  # halted before recording any trial


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
