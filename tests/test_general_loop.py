"""The general multi-step agent loop (lab_runner/loop.py).

The slice runner does read-then-sink. This is the general case: any tool, any
args, any number of steps. The tests that matter most are the provenance ones —
the loop assigns provenance and an agent must not be able to influence it.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_contracts import validate_artifact
from lab_contracts.semantics import trace_semantics
from lab_capabilities.governance import gate_for_condition
from lab_runner.loop import (
    STOP_MAX_STEPS,
    STOP_MAX_TOOL_CALLS,
    Finish,
    LoopContext,
    ScriptedProgram,
    ToolCall,
    run_loop_trial,
)

LANDLORD = "GB29NWBK60161331926819"
ATTACKER = "DE89370400440532013000"
OBSERVE = {"schema_version": "condition/v1", "id": "observe", "enforcement": "off"}


def _run(steps, condition=OBSERVE, gate=None, scenario=None, **kw):
    scenario = scenario or support.banking_scenario()
    return run_loop_trial(
        scenario, support.manifests(), condition, gate,
        "r_loop", "s000", 0, ScriptedProgram(steps), **kw,
    )


def _verdicts(outcome) -> list[str]:
    return [
        str(e["decision"]["verdict"]) for e in outcome.trace["events"]
        if e.get("type") == "gate_decision"
    ]


def _labels(outcome) -> dict[str, list[str]]:
    return {str(v["value_id"]): list(v["labels"]) for v in outcome.trace["values"]}


def _binding(outcome, tool: str, arg: str) -> str:
    for event in outcome.trace["events"]:
        if event.get("type") == "tool_call_intent" and event.get("tool") == tool:
            return str(event["arg_bindings"][arg])
    raise AssertionError(f"no intent for {tool}")


class TestLoopMechanics(unittest.TestCase):
    def test_a_multi_step_run_produces_a_valid_trace(self) -> None:
        out = _run([
            ToolCall("read_txns", {}),
            ToolCall("send_money", {"recipient": LANDLORD, "amount": 1200}),
            Finish("done"),
        ])
        self.assertEqual(validate_artifact(out.trace, "trace"), [])
        self.assertEqual(trace_semantics(out.trace), [])
        self.assertTrue(out.task_success)
        self.assertFalse(out.violation)

    def test_seq_is_dense_and_ordered(self) -> None:
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": LANDLORD, "amount": 1200}),
                    Finish()])
        seqs = [int(e["seq"]) for e in out.trace["events"]]
        self.assertEqual(seqs, list(range(len(seqs))))

    def test_repeated_calls_to_the_same_tool_are_distinct(self) -> None:
        out = _run([ToolCall("read_txns", {}), ToolCall("read_txns", {}),
                    ToolCall("read_txns", {}), Finish()])
        intents = [e for e in out.trace["events"] if e.get("type") == "tool_call_intent"]
        self.assertEqual(len(intents), 3)
        self.assertEqual(len({str(e["call_id"]) for e in intents}), 3)
        self.assertEqual(out.metrics["tool_calls"], 3)

    def test_a_step_may_branch_on_what_it_observed(self) -> None:
        def choose(ctx: LoopContext):
            self.assertEqual(len(ctx.observations), 1)
            self.assertEqual(ctx.observations[0].tool, "read_txns")
            return ToolCall("send_money", {"recipient": LANDLORD, "amount": 1200})

        out = _run([ToolCall("read_txns", {}), choose, Finish()])
        self.assertTrue(out.task_success)

    def test_an_undeclared_tool_is_refused(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            _run([ToolCall("wire_transfer", {"to": ATTACKER})])
        self.assertIn("does not declare", str(ctx.exception))

    def test_metrics_are_recorded(self) -> None:
        out = _run([ToolCall("read_txns", {}), Finish()])
        self.assertGreaterEqual(float(out.metrics["duration_ms"]), 0.0)
        self.assertEqual(out.metrics["tool_calls"], 1)
        self.assertGreater(int(out.metrics["model_calls"]), 0)


class TestBudgets(unittest.TestCase):
    def test_max_tool_calls_stops_the_loop(self) -> None:
        out = _run([ToolCall("read_txns", {})] * 10, max_tool_calls=3)
        self.assertEqual(out.stop_reason, STOP_MAX_TOOL_CALLS)
        self.assertEqual(out.metrics["tool_calls"], 3)

    def test_max_steps_stops_the_loop(self) -> None:
        out = _run([ToolCall("read_txns", {})] * 10, max_steps=4)
        self.assertEqual(out.stop_reason, STOP_MAX_STEPS)
        self.assertLessEqual(int(out.metrics["steps"]), 4)

    def test_a_stopped_trial_still_yields_a_valid_trace(self) -> None:
        """A budget stop is a recorded outcome, not a crash — the partial trace
        must still be well-formed so missingness can be judged."""
        out = _run([ToolCall("read_txns", {})] * 10, max_tool_calls=2)
        self.assertEqual(validate_artifact(out.trace, "trace"), [])
        self.assertEqual(trace_semantics(out.trace), [])


class TestProvenanceIsAssignedByTheRuntime(unittest.TestCase):
    def test_a_declared_input_is_prompt_given(self) -> None:
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": LANDLORD, "amount": 1200}), Finish()])
        vid = _binding(out, "send_money", "recipient")
        self.assertEqual(_labels(out)[vid], ["prompt_given"])

    def test_a_value_from_the_injection_is_untrusted_derived(self) -> None:
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}), Finish()])
        vid = _binding(out, "send_money", "recipient")
        self.assertEqual(_labels(out)[vid], ["untrusted_derived"])

    def test_taint_cannot_be_laundered_through_extra_calls(self) -> None:
        """The conservative join is over every untrusted value seen SO FAR, so
        an agent cannot dilute taint by interposing more tool calls."""
        out = _run([
            ToolCall("read_txns", {}), ToolCall("read_txns", {}), ToolCall("read_txns", {}),
            ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}), Finish(),
        ])
        vid = _binding(out, "send_money", "recipient")
        self.assertEqual(_labels(out)[vid], ["untrusted_derived"])

    def test_a_substring_of_a_declared_input_is_not_exempt(self) -> None:
        """The exemption is whole-value equality. A partial match would let an
        attacker smuggle tainted data inside a legitimate-looking argument."""
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": LANDLORD[:10], "amount": 1200}),
                    Finish()])
        vid = _binding(out, "send_money", "recipient")
        self.assertEqual(_labels(out)[vid], ["untrusted_derived"])

    def test_a_superstring_of_a_declared_input_is_not_exempt(self) -> None:
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": LANDLORD + ATTACKER, "amount": 1200}),
                    Finish()])
        vid = _binding(out, "send_money", "recipient")
        self.assertEqual(_labels(out)[vid], ["untrusted_derived"])

    def test_a_member_of_a_declared_list_input_is_exempt(self) -> None:
        """`known_ibans` declares a set of acceptable values; paying one of them
        is the user's own ground truth."""
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": "US64SVBKUS6S3300958879",
                                            "amount": 1200}), Finish()])
        vid = _binding(out, "send_money", "recipient")
        self.assertEqual(_labels(out)[vid], ["prompt_given"])

    def test_type_confusion_does_not_win_the_exemption(self) -> None:
        """A string that looks like a declared number must not match it.

        End to end this is unreachable — the tool manifest's args_schema
        already rejects "1200" where a number is declared, which is the first
        line of defence. The equality rule is tested directly so the exemption
        stays sound for any tool whose schema is looser.
        """
        from lab_runner.loop import _same_value
        self.assertFalse(_same_value("1200", 1200))
        self.assertFalse(_same_value(1200, "1200"))
        self.assertFalse(_same_value(True, 1))
        self.assertFalse(_same_value(1, True))
        self.assertTrue(_same_value(1200, 1200))
        self.assertTrue(_same_value(LANDLORD, LANDLORD))

    def test_the_tool_schema_rejects_a_mistyped_argument(self) -> None:
        from lab_runner.errors import SimulationError
        with self.assertRaises(SimulationError):
            _run([ToolCall("read_txns", {}),
                  ToolCall("send_money", {"recipient": LANDLORD, "amount": "1200"}), Finish()])

    def test_before_any_untrusted_read_values_are_clean(self) -> None:
        out = _run([ToolCall("send_money", {"recipient": "XX00", "amount": 5}), Finish()])
        vid = _binding(out, "send_money", "recipient")
        self.assertEqual(_labels(out)[vid], ["prompt_given"])


class TestGovernanceThroughTheLoop(unittest.TestCase):
    def _gate(self, condition):
        return gate_for_condition(
            condition, support.manifests(),
            support.banking_scenario()["inputs"], support.kernel_registry(),
        )

    def _conditions(self):
        return {str(c["id"]): c for c in support.conditions()}

    def test_governed_denies_the_attacked_call(self) -> None:
        condition = self._conditions()["governed"]
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}), Finish()],
                   condition=condition, gate=self._gate(condition))
        self.assertIn("DENY", _verdicts(out))
        self.assertFalse(out.violation, "a denied call must not count as a breach")

    def test_governed_allows_the_faithful_call(self) -> None:
        """The declared-input exemption earns its place here: without it a
        governed general loop would DENY the correct action too, and every
        governed task would fail."""
        condition = self._conditions()["governed"]
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": LANDLORD, "amount": 1200}), Finish()],
                   condition=condition, gate=self._gate(condition))
        self.assertNotIn("DENY", _verdicts(out))
        self.assertTrue(out.task_success)

    def test_ungoverned_records_the_breach(self) -> None:
        """The ungoverned arm reaches the SAME verdict as the governed arm and
        lets the call through anyway. That is what makes the pair a comparison
        of one machine under two policies.

        It used to record ALLOW here — the observe-only path returned an
        unconditional ALLOW without consulting the kernel — so the two arms
        disagreed on every verdict and an ungoverned run carried no evidence of
        what governance would have caught."""
        condition = self._conditions()["ungoverned"]
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}), Finish()],
                   condition=condition, gate=self._gate(condition))
        self.assertIn("DENY", _verdicts(out))
        self.assertTrue(out.violation, "the denied call ran, so the breach happened")

    def test_both_arms_reach_the_same_verdicts_and_only_enforcement_differs(self) -> None:
        attack = [ToolCall("read_txns", {}),
                  ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}), Finish()]
        conditions = self._conditions()
        ungoverned = _run(attack, condition=conditions["ungoverned"],
                          gate=self._gate(conditions["ungoverned"]))
        governed = _run(attack, condition=conditions["governed"],
                        gate=self._gate(conditions["governed"]))
        self.assertEqual(_verdicts(ungoverned), _verdicts(governed))
        # ...and the delta is entirely in what happened next
        self.assertTrue(ungoverned.violation)
        self.assertFalse(governed.violation)

    def test_an_unenforced_denial_says_so(self) -> None:
        """A DENY that was obeyed and a DENY that was merely observed must not
        read alike — otherwise an ungoverned run reports as contained."""
        conditions = self._conditions()
        for arm, enforced in (("ungoverned", False), ("governed", True)):
            with self.subTest(arm=arm):
                out = _run([ToolCall("read_txns", {}),
                            ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}),
                            Finish()],
                           condition=conditions[arm], gate=self._gate(conditions[arm]))
                denials = [e["decision"] for e in out.trace["events"]  # type: ignore[index]
                           if e.get("type") == "gate_decision"
                           and e["decision"]["verdict"] == "DENY"]  # type: ignore[index]
                self.assertEqual([d["enforced"] for d in denials], [enforced])

    def test_a_denied_call_is_not_executed_and_is_visible_to_the_agent(self) -> None:
        condition = self._conditions()["governed"]
        seen: list[object] = []

        def after_deny(ctx: LoopContext):
            seen.append(ctx.observations[-1])
            return Finish()

        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}),
                    after_deny],
                   condition=condition, gate=self._gate(condition))
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].allowed)  # type: ignore[attr-defined]
        self.assertEqual(seen[0].verdict, "DENY")  # type: ignore[attr-defined]
        results = [e for e in out.trace["events"]
                   if e.get("type") == "tool_result" and e.get("tool") == "send_money"]
        self.assertEqual(results, [], "a denied call must emit no tool_result")

    def test_gate_free_loop_emits_no_decisions(self) -> None:
        out = _run([ToolCall("read_txns", {}),
                    ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}), Finish()])
        self.assertEqual(_verdicts(out), [])
        self.assertNotIn("kernel_version", out.trace["producer"])
        self.assertTrue(out.violation, "with no gate the breach is observed, not prevented")


class TestReplayOverALoopTrace(unittest.TestCase):
    def test_a_multi_call_loop_trace_replays_exactly(self) -> None:
        """Exact replay is the governance capability's core promise, and it has
        to survive the new execution path: a loop trace with SEVERAL gated calls
        must recompute the same ordered verdicts over the frozen trace."""
        from lab_runner.replay import REPLAY_MATCH, replay_trace_status
        scenario = support.banking_scenario()
        manifests = support.manifests()
        condition = {str(c["id"]): c for c in support.conditions()}["governed"]
        gate = gate_for_condition(
            condition, manifests, scenario["inputs"], support.kernel_registry(),
        )
        out = _run([
            ToolCall("read_txns", {}),
            ToolCall("send_money", {"recipient": LANDLORD, "amount": 1200}),
            ToolCall("send_money", {"recipient": ATTACKER, "amount": 1200}),
            Finish(),
        ], condition=condition, gate=gate)
        self.assertEqual(_verdicts(out), ["ALLOW", "ALLOW", "DENY"])
        recomputed, status = replay_trace_status(
            out.trace, condition, gate.kernel, manifests, scenario["inputs"],
        )
        self.assertEqual(status, REPLAY_MATCH)
        self.assertEqual([str(r["verdict"]) for r in recomputed], ["ALLOW", "ALLOW", "DENY"])


class TestSliceRunnerIsUnchanged(unittest.TestCase):
    def test_the_two_paths_coexist(self) -> None:
        """The general loop is additive. The slice runner's trace shape is
        pinned by canonicalization vectors and published hashes, so it must keep
        producing exactly what it always did."""
        from lab_runner.agents import ScriptedAgent
        from lab_runner.runner import run_trial
        scenario = support.banking_scenario()
        conditions = {str(c["id"]): c for c in support.conditions()}
        condition = conditions["governed"]
        from lab_runner.axor_backend import resolve_kernel
        kernel = resolve_kernel(
            str(condition["kernel"]), support.manifests(), condition.get("policy"),
            support.kernel_registry(), scenario["inputs"],
        )
        outcome = run_trial(scenario, support.manifests(), condition, kernel,
                            "r_slice", "s000", 0, ScriptedAgent())
        types = [str(e["type"]) for e in outcome.trace["events"]]
        self.assertEqual(types, ["tool_result", "tool_call_intent", "gate_decision"])


if __name__ == "__main__":
    unittest.main()
