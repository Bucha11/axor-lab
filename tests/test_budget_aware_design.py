"""Budget-aware experiment design (review r14).

A USD-only budget must reserve the output tokens the next call will bill, not
just its input; the token projection must include the tool schemas that ship
with every call; the trial plan must be block-balanced so a cost stop leaves
matched pairs rather than one lopsided arm; missingness must be condition-aware;
and a cost-stopped run must not wear the `[completed]` label.
"""

from __future__ import annotations

import unittest

from lab_analysis import missingness
from lab_agent.cost import CostBudget
from lab_runner import ScriptedAgent, run_experiment_suite
from lab_runner.cli import _terminal_label
from tests import support


class TestUsdBudgetReservesOutput(unittest.TestCase):
    def test_usd_only_budget_reserves_output_tokens_in_pre_spend(self) -> None:
        # opus: $15/Mtok in, $75/Mtok out. A tiny input alone is well under the
        # ceiling, but the reserved output for the call tips it over — a USD-only
        # budget that reserved ZERO output would wrongly allow the call.
        b = CostBudget(max_usd=0.02)  # 2 cents, no output ceiling
        usage = {"input_tokens": 0, "output_tokens": 0}
        # input-only projection: 100 tokens -> $0.0015 (< $0.02). But the default
        # 512-token output reserve adds ~$0.0384 -> ~$0.04 > $0.02 -> refuse.
        reason = b.pre_spend_exceeded(usage, projected_input_tokens=100, model="claude-opus-4-8")
        self.assertIsNotNone(reason)
        self.assertIn("$", reason)

    def test_explicit_output_reserve_of_zero_is_honored(self) -> None:
        # if a caller genuinely reserves no output, only input counts (regression
        # guard that the reserve is a real parameter, not hard-coded)
        b = CostBudget(max_usd=0.02)
        self.assertIsNone(b.pre_spend_exceeded(
            {"input_tokens": 0, "output_tokens": 0}, 100, "claude-opus-4-8",
            projected_output_tokens=0,
        ))


class TestToolSchemaInProjection(unittest.TestCase):
    def test_tool_schema_is_counted_in_the_input_projection(self) -> None:
        # the wrapped agent's pre-spend projection must include the tool schema
        # bytes, so a tool-heavy call is not undercounted. Set the input ceiling
        # ABOVE the message-only projection but BELOW message+schema: the call can
        # only be stopped if the schema is in the projection.
        import json as _json

        from lab_agent.backends import CassetteBackend
        from lab_agent.wrapped import WrappedModelAgent, _task_prompt, _tool_schemas
        from lab_runner.errors import CostCeilingReached

        sink = support.manifests()["send_money"]
        messages = [{"role": "user", "content": _task_prompt("x", "x")}]
        msg_only = sum(len(str(m)) for m in messages) // 4
        schemas = _tool_schemas(sink)
        with_schema = (sum(len(str(m)) for m in messages)
                       + sum(len(_json.dumps(t)) for t in schemas)) // 4
        self.assertGreater(with_schema, msg_only)  # the schema is non-trivial
        # ceiling strictly between the two projections
        ceiling = (msg_only + with_schema) // 2
        agent = WrappedModelAgent(
            backend=CassetteBackend.from_records([{"tool": "send_money", "args": {"recipient": "a", "amount": 1}}]),
            budget=CostBudget(max_input_tokens=ceiling), model="claude-opus-4-8",
        )
        with self.assertRaises(CostCeilingReached) as ctx:
            agent.decide_sink_call("x", "x", {}, sink)
        self.assertIn("input", ctx.exception.reason)


class TestBlockBalancedOrder(unittest.TestCase):
    def test_conditions_interleave_within_a_block(self) -> None:
        # scenario × 2 conditions × repeats: the FIRST two executed trials must be
        # the two conditions of the same repeat (block-balanced), not two repeats
        # of one condition (condition-major) — so a cost stop keeps matched pairs.
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=3, run_id="r_block", agent=ScriptedAgent(),
        )
        completed = [t for t in result.trials if t["status"] == "completed"]
        self.assertGreaterEqual(len(completed), 2)
        self.assertNotEqual(completed[0]["condition_id"], completed[1]["condition_id"])
        # and they are the SAME (scenario, repeat) block
        self.assertEqual(completed[0]["repeat_index"], completed[1]["repeat_index"])


class TestConditionAwareMissingness(unittest.TestCase):
    def _trial(self, cid: str, status: str, rep: int) -> dict[str, object]:
        return {"trial_id": f"{cid}-{rep}", "scenario_id": "s", "condition_id": cid,
                "seed": f"s{rep:03d}", "repeat_index": rep, "status": status,
                **({"failure_reason": "cost_ceiling: x"} if status != "completed" else {})}

    def test_missingness_concentrated_on_one_condition_is_flagged(self) -> None:
        # baseline complete, governed all excluded → the paired comparison loses
        # its treated arm; missingness must flag the imbalance, not just the count
        trials = [self._trial("baseline", "completed", r) for r in range(4)]
        trials += [self._trial("governed", "excluded", r) for r in range(4)]
        summary = missingness(trials)
        self.assertTrue(summary.condition_imbalanced)
        self.assertIn("condition-imbalanced", summary.display())
        self.assertIn("by condition:", summary.display())
        by = {cid: (done, tot) for cid, done, tot in summary.by_condition}
        self.assertEqual(by["baseline"], (4, 4))
        self.assertEqual(by["governed"], (0, 4))

    def test_balanced_missingness_is_not_condition_flagged(self) -> None:
        trials = [self._trial("baseline", "completed", r) for r in range(3)]
        trials += [self._trial("governed", "completed", r) for r in range(3)]
        trials += [self._trial("baseline", "excluded", 3), self._trial("governed", "excluded", 3)]
        self.assertFalse(missingness(trials).condition_imbalanced)


class TestTerminalLabel(unittest.TestCase):
    def test_clean_run_is_completed(self) -> None:
        self.assertEqual(_terminal_label(None, 12), "[completed]")

    def test_cost_stopped_with_some_completed_is_partial(self) -> None:
        self.assertEqual(_terminal_label("spend $1 reached ceiling", 5), "[completed_partial]")

    def test_cost_stopped_with_nothing_completed_is_stopped(self) -> None:
        self.assertEqual(_terminal_label("spend $1 reached ceiling", 0), "[stopped_cost_ceiling]")


if __name__ == "__main__":
    unittest.main()
