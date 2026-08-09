"""Executable invariants (regression/v1) — Suite Platform RFC §9.

The exit criterion for the generic regression engine: a `latency < threshold`
invariant that PASSES and FAILS correctly, and — the part that matters more —
that ERRORS rather than passing when it could not be evaluated at all.
"""

from __future__ import annotations

import unittest

from lab_runner.invariants import (
    STATUS_ERROR,
    STATUS_FAILED,
    STATUS_PASSED,
    check_invariant,
    check_invariants,
)


def _trial(trial_id: str, duration_ms: float | None = None, *, status: str = "completed",
           scenario_id: str = "s1", condition_id: str = "observe",
           **metrics: object) -> dict[str, object]:
    trial: dict[str, object] = {
        "trial_id": trial_id, "scenario_id": scenario_id, "condition_id": condition_id,
        "seed": "s0", "repeat_index": 0, "status": status, "trace_ref": f"ref_{trial_id}",
    }
    collected: dict[str, object] = dict(metrics)
    if duration_ms is not None:
        collected["duration_ms"] = duration_ms
    if collected:
        trial["metrics"] = collected
    return trial


def _latency_rule(value: float = 10000, op: str = "lt", aggregate: str = "value") -> dict[str, object]:
    return {
        "schema_version": "regression/v1", "id": "RG-42",
        "name": "Trial latency stays under 10s",
        "rule": {"kind": "metric_threshold", "metric": "duration_ms",
                 "aggregate": aggregate, "op": op, "value": value},
    }


class TestMetricThreshold(unittest.TestCase):
    def test_passes_when_every_trial_is_under_the_threshold(self) -> None:
        result = check_invariant(_latency_rule(), [_trial("t1", 900), _trial("t2", 1200)])
        self.assertEqual(result.status, STATUS_PASSED)
        self.assertEqual(result.trials_checked, 2)
        self.assertEqual(result.trials_failed, 0)

    def test_fails_when_one_trial_exceeds_it(self) -> None:
        result = check_invariant(
            _latency_rule(), [_trial("t1", 900), _trial("t2", 18400), _trial("t3", 1200)],
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.trials_failed, 1)
        self.assertEqual(result.failing_trial_ids, ("t2",))
        self.assertEqual(result.observed, 18400.0)

    def test_an_unmeasured_metric_errors_rather_than_passing(self) -> None:
        """The invariant that matters most. A trial with no duration_ms has not
        demonstrated that it was fast — it has demonstrated nothing. Passing
        here would make a budget or latency invariant silently vacuous."""
        result = check_invariant(_latency_rule(), [_trial("t1", 900), _trial("t2")])
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("not measured", result.detail)

    def test_a_budget_invariant_over_an_unmeasured_cost_errors(self) -> None:
        """The concrete case the runner creates: a scripted agent omits
        cost_usd, so `budget <= limit` must error, not pass."""
        rule = {
            "schema_version": "regression/v1", "id": "RG-budget", "name": "Under $1",
            "rule": {"kind": "metric_threshold", "metric": "cost_usd", "op": "lte", "value": 1.0},
        }
        result = check_invariant(rule, [_trial("t1", 900)])
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("cost_usd", result.detail)

    def test_a_boolean_metric_is_not_a_number(self) -> None:
        """bool is an int subclass in Python; `task_success: True` must not be
        silently compared as 1 against a numeric threshold."""
        trial = _trial("t1")
        trial["metrics"] = {"duration_ms": True}
        result = check_invariant(_latency_rule(), [trial])
        self.assertEqual(result.status, STATUS_ERROR)

    def test_failed_trials_are_not_evidence_of_a_regression(self) -> None:
        """A failed trial is a missingness fact. Counting it as a violation
        would turn every crash into a regression."""
        result = check_invariant(
            _latency_rule(), [_trial("t1", 900), _trial("t2", status="failed")],
        )
        self.assertEqual(result.status, STATUS_PASSED)
        self.assertEqual(result.trials_checked, 1)

    def test_no_trial_in_scope_errors(self) -> None:
        result = check_invariant(_latency_rule(), [_trial("t1", status="failed")])
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("never evaluated", result.detail)


class TestAggregates(unittest.TestCase):
    def test_p95_passes_and_fails_around_the_threshold(self) -> None:
        # nearest-rank p95 of 20 samples is the 19th, so ONE outlier in 20 sits
        # above p95 and does not move it — that is the point of a percentile
        one_slow = [_trial(f"t{i}", 1000) for i in range(19)] + [_trial("slow", 18400)]
        self.assertEqual(check_invariant(_latency_rule(aggregate="p95"), one_slow).status,
                         STATUS_PASSED)
        # two slow trials push the 19th value itself over the threshold
        two_slow = [_trial(f"t{i}", 1000) for i in range(18)] + [
            _trial("slow1", 18400), _trial("slow2", 19000)]
        result = check_invariant(_latency_rule(aggregate="p95"), two_slow)
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.observed, 18400.0)

    def test_percentile_is_a_value_some_trial_actually_had(self) -> None:
        """Nearest-rank, not interpolated: a failing p95 must be traceable to a
        real trial rather than to a number no run ever produced."""
        trials = [_trial("a", 100), _trial("b", 200), _trial("c", 300)]
        result = check_invariant(_latency_rule(value=0, op="gt", aggregate="p95"), trials)
        self.assertIn(result.observed, (100.0, 200.0, 300.0))

    def test_mean_and_max(self) -> None:
        trials = [_trial("a", 100), _trial("b", 300)]
        self.assertEqual(check_invariant(_latency_rule(value=200, op="lt", aggregate="mean"),
                                         trials).status, STATUS_FAILED)
        self.assertEqual(check_invariant(_latency_rule(value=400, op="lt", aggregate="max"),
                                         trials).status, STATUS_PASSED)

    def test_unknown_aggregate_errors(self) -> None:
        result = check_invariant(_latency_rule(aggregate="wibble"), [_trial("t1", 900)])
        self.assertEqual(result.status, STATUS_ERROR)


class TestScope(unittest.TestCase):
    def test_scope_limits_which_trials_are_checked(self) -> None:
        rule = _latency_rule()
        rule["scope"] = {"scenario_ids": ["s1"]}
        trials = [_trial("t1", 900, scenario_id="s1"), _trial("t2", 99000, scenario_id="s2")]
        result = check_invariant(rule, trials)
        self.assertEqual(result.status, STATUS_PASSED)
        self.assertEqual(result.trials_checked, 1)

    def test_any_quantifier(self) -> None:
        rule = _latency_rule()
        rule["scope"] = {"quantifier": "any"}
        trials = [_trial("t1", 900), _trial("t2", 99000)]
        self.assertEqual(check_invariant(rule, trials).status, STATUS_PASSED)
        rule2 = _latency_rule(value=100)
        rule2["scope"] = {"quantifier": "any"}
        self.assertEqual(check_invariant(rule2, trials).status, STATUS_FAILED)


class TestPredicateRule(unittest.TestCase):
    def _trace(self, tool: str = "send_money") -> dict[str, object]:
        return {
            "schema_version": "trace/v1", "trace_id": "t", "values": [],
            "trial": {"run_id": "r", "scenario_id": "s1", "condition_id": "observe",
                      "seed": "s0", "repeat_index": 0},
            "producer": {"mode": "wrapped_code",
                         "provenance_fidelity": "explicit_flow_tracked"},
            "events": [{"seq": 0, "node": "root", "type": "tool_call_intent",
                        "tool": tool, "call_id": "c1", "arg_bindings": {}}],
        }

    def _rule(self, tool: str, expect: bool = True) -> dict[str, object]:
        return {
            "schema_version": "regression/v1", "id": "RG-1", "name": "no forbidden calls",
            "rule": {"kind": "predicate", "expect": expect,
                     "predicate": {"event": "tool_call", "tool": tool}},
        }

    def test_predicate_passes(self) -> None:
        trials = [_trial("t1")]
        traces = {"ref_t1": self._trace()}
        result = check_invariant(self._rule("send_money"), trials, traces, {})
        self.assertEqual(result.status, STATUS_PASSED)

    def test_forbidden_tool_calls_equals_zero(self) -> None:
        """The RFC's own example, expressed as expect=False."""
        trials = [_trial("t1")]
        traces = {"ref_t1": self._trace(tool="wire_transfer")}
        self.assertEqual(
            check_invariant(self._rule("wire_transfer", expect=False), trials, traces, {}).status,
            STATUS_FAILED,
        )
        self.assertEqual(
            check_invariant(self._rule("send_money", expect=False), trials, traces, {}).status,
            STATUS_PASSED,
        )

    def test_a_missing_trace_errors(self) -> None:
        result = check_invariant(self._rule("send_money"), [_trial("t1")], {}, {})
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("not in the run", result.detail)


class TestVerdictSequence(unittest.TestCase):
    def _trace(self, *verdicts: str) -> dict[str, object]:
        return {"events": [{"type": "gate_decision", "decision": {"verdict": v}}
                           for v in verdicts]}

    def test_a_matching_recorded_sequence_passes(self) -> None:
        rule = {"schema_version": "regression/v1", "id": "RG-9", "name": "v",
                "rule": {"kind": "verdict_sequence", "verdicts": ["ALLOW", "DENY"]}}
        result = check_invariant(
            rule, [_trial("t1")], traces={"ref_t1": self._trace("ALLOW", "DENY")})
        self.assertEqual(result.status, STATUS_PASSED)

    def test_a_differing_recorded_sequence_fails(self) -> None:
        rule = {"schema_version": "regression/v1", "id": "RG-9", "name": "v",
                "rule": {"kind": "verdict_sequence", "verdicts": ["ALLOW", "DENY"]}}
        result = check_invariant(
            rule, [_trial("t1")], traces={"ref_t1": self._trace("ALLOW", "ALLOW")})
        self.assertEqual(result.status, STATUS_FAILED)

    def test_a_trial_with_no_trace_errors_never_passes(self) -> None:
        rule = {"schema_version": "regression/v1", "id": "RG-9", "name": "v",
                "rule": {"kind": "verdict_sequence", "verdicts": ["DENY"]}}
        result = check_invariant(rule, [_trial("t1")], traces={})
        self.assertEqual(result.status, STATUS_ERROR)


class TestMalformedRuleErrorsAreActionable(unittest.TestCase):
    """A discriminated union (rule keys on `kind`) must not fail with a bare
    'oneOf matched 0 branches' — the message should name the field that is wrong
    in the branch the author meant."""

    def _errors(self, rule: dict) -> list[str]:
        from lab_contracts import validate_artifact
        return validate_artifact(
            {"schema_version": "regression/v1", "id": "x", "name": "n", "rule": rule},
            "regression")

    def test_a_missing_field_names_the_field(self) -> None:
        errs = self._errors({"kind": "metric_threshold", "metric": "m"})
        self.assertTrue(any("op" in e for e in errs), errs)
        self.assertFalse(any("oneOf matched" in e for e in errs), errs)

    def test_two_kinds_in_one_rule_names_the_extra(self) -> None:
        errs = self._errors({"kind": "metric_threshold", "metric": "m", "op": "lt",
                             "value": 1, "predicate": {}})
        self.assertTrue(any("predicate" in e for e in errs), errs)

    def test_an_unknown_kind_stays_generic(self) -> None:
        # no branch discriminates on kind="bogus", so the generic message is honest
        errs = self._errors({"kind": "bogus"})
        self.assertTrue(errs)


class TestUnrunnableKinds(unittest.TestCase):
    def test_unknown_kind_errors(self) -> None:
        rule = {"schema_version": "regression/v1", "id": "RG-9", "name": "v",
                "rule": {"kind": "telepathy"}}
        self.assertEqual(check_invariant(rule, [_trial("t1", 900)]).status, STATUS_ERROR)


class TestHistoryEntry(unittest.TestCase):
    def test_history_entry_is_schema_valid(self) -> None:
        from lab_contracts import validate_artifact
        result = check_invariant(_latency_rule(), [_trial("t1", 18400)])
        regression = _latency_rule()
        regression["history"] = [result.as_history_entry("r_1", "2026-08-02T00:00:00+00:00")]
        self.assertEqual(validate_artifact(regression, "regression"), [])

    def test_many_invariants_at_once(self) -> None:
        results = check_invariants([_latency_rule(), _latency_rule(value=100)],
                                   [_trial("t1", 900)])
        self.assertEqual([r.status for r in results], [STATUS_PASSED, STATUS_FAILED])


if __name__ == "__main__":
    unittest.main()
