"""Two things the schemas allowed and the code could not do.

**A non-governance EvidenceCase.** `evidence-case/v1` says `kind` is an open
string and the injection→provenance→gate→verdict chain is an OPTIONAL
`governance` block. The only builder in the repo was that chain: it required a
governed condition and a kernel, so a latency spike, a budget overflow or a
planner failure was unrepresentable in code while being perfectly representable
in the schema. That is Suite Platform acceptance criterion 8.4.

**`evaluator_outcome`.** One of `regression/v1`'s four rule kinds, declared from
the start, reported `skipped` forever. A suite could pin its own evaluator's
result and never get an answer.

Both are checked here against real runs of real built-in suites, not against
hand-built dicts — a builder that produces a schema-valid case from fixture
input and nothing usable from a live trial is the failure this is for.
"""

from __future__ import annotations

import unittest

from lab_contracts import validate_artifact
from lab_runner.cases import build_case, case_id_for, threshold_case
from lab_runner.invariants import (
    STATUS_ERROR,
    STATUS_FAILED,
    STATUS_PASSED,
    check_invariant,
)
from lab_suite import builtin_registry, run_suite
from lab_suite.builtin.budget import READ_TOOL as BUDGET_READ_TOOL

CREATED = "2026-08-08T00:00:00+00:00"
ENVIRONMENT = {"model": {"provider": "scripted", "id": "t", "inference_params": {}}}


def _run(suite_id: str):
    suite = builtin_registry().get(suite_id)
    return suite, run_suite(suite.manifest(), run_id=f"r_{suite_id}", suite=suite)


class TestAGenericCaseIsBuildableWithoutGovernance(unittest.TestCase):
    def setUp(self) -> None:
        self.suite, self.run = _run("budget")
        self.trial = self.run.trials[0]
        self.trace = self.run.traces[str(self.trial["trace_ref"])]

    def _case(self) -> dict[str, object]:
        return build_case(
            case_id=case_id_for(self.trial, "latency_spike"),
            kind="latency_spike", title="A slow trial",
            trial=self.trial, trace=self.trace, run_id=self.run.run_id,
            severity="medium", summary="the trial took longer than usual",
        )

    def test_it_validates_against_the_schema(self) -> None:
        self.assertEqual(validate_artifact(self._case(), "evidence-case"), [])

    def test_it_carries_no_governance_block(self) -> None:
        """The suite declares no governance capability. A case that grew one
        anyway would be asserting a gate decided something on a run with no
        enforcement."""
        self.assertNotIn("governance", self._case())

    def test_the_timeline_points_at_events_and_does_not_restate_them(self) -> None:
        """The schema's rule: an entry POINTS AT a trace event by sequence. A
        case carrying its own copy of the narrative can drift from the trace it
        describes, and then the artifact holds two accounts of one run."""
        case = self._case()
        recorded = [int(e["seq"]) for e in self.trace["events"]]
        self.assertEqual([int(t["seq"]) for t in case["timeline"]], recorded)
        for entry in case["timeline"]:
            self.assertNotIn("payload", entry)

    def test_exactly_one_step_is_highlighted(self) -> None:
        highlighted = [t for t in self._case()["timeline"] if t.get("highlight")]
        self.assertEqual(len(highlighted), 1)

    def test_metrics_are_copied_and_an_unmeasured_one_stays_absent(self) -> None:
        """A case about cost on a run that priced nothing must not show a
        cost."""
        case = self._case()
        self.assertIn("duration_ms", case["metrics"])
        self.assertNotIn("cost_usd", case["metrics"])

    def test_only_the_named_metrics_are_surfaced_when_asked(self) -> None:
        case = build_case(
            case_id="EC-1", kind="latency_spike", title="t",
            trial=self.trial, trace=self.trace, metrics=["duration_ms", "cost_usd"],
        )
        self.assertEqual(set(case["metrics"]), {"duration_ms"})

    def test_case_ids_do_not_collide_across_trials(self) -> None:
        """Trial ids all begin `sha256:`, so slicing the prefixed string spends
        most of the budget on nine identical characters."""
        ids = {case_id_for(t, "k") for t in self.run.trials}
        self.assertEqual(len(ids), len(self.run.trials))


class TestAThresholdCaseDistinguishesAbsentFromWithinBounds(unittest.TestCase):
    def setUp(self) -> None:
        _, self.run = _run("budget")
        self.trial = self.run.trials[0]
        self.trace = self.run.traces[str(self.trial["trace_ref"])]

    def test_no_case_when_the_metric_is_within_bounds(self) -> None:
        self.assertIsNone(threshold_case(
            case_id="EC-1", kind="budget_overflow", metric="reads", limit=99,
            trial=self.trial, trace=self.trace,
        ))

    def test_no_case_when_the_metric_was_never_measured(self) -> None:
        """Not the same answer as "within bounds", and neither is a finding. A
        case asserting an overrun nobody measured is fabricated evidence."""
        self.assertIsNone(threshold_case(
            case_id="EC-1", kind="budget_overflow", metric="cost_usd", limit=0.0,
            trial=self.trial, trace=self.trace,
        ))

    def test_a_case_when_the_metric_is_over(self) -> None:
        case = threshold_case(
            case_id="EC-1", kind="budget_overflow", metric="reads", limit=0,
            trial=self.trial, trace=self.trace,
        )
        assert case is not None
        self.assertEqual(validate_artifact(case, "evidence-case"), [])
        self.assertEqual(case["kind"], "budget_overflow")


class TestSuitesExtractTheirOwnCases(unittest.TestCase):
    """`BaseSuite.evidence_for` returned `[]` and no built-in overrode it, so
    the hook existed and nothing on the platform had ever produced a case
    through it."""

    def test_agentdojo_raises_a_case_for_every_breach(self) -> None:
        _, run = _run("agentdojo")
        breaches = [t for t in run.trials if t.get("metrics", {}).get("ASR")]
        self.assertTrue(breaches, "the ungoverned arm suppressed the attack entirely")
        self.assertEqual(len(run.evidence_cases), len(breaches))
        for case in run.evidence_cases:
            self.assertEqual(case["kind"], "prompt_injection")
            self.assertEqual(validate_artifact(case, "evidence-case"), [])

    def test_a_contained_trial_raises_no_case(self) -> None:
        """The governed arm blocks the exfiltration, so there is nothing to
        investigate — a case per trial regardless of outcome would be noise,
        not curation."""
        _, run = _run("agentdojo")
        cased = {str(c["trial_ref"]["trial_id"]) for c in run.evidence_cases}
        for trial in run.trials:
            if trial.get("status") != "completed":
                continue
            if not trial.get("metrics", {}).get("ASR"):
                self.assertNotIn(str(trial["trial_id"]), cased)

    def test_the_cases_reach_the_artifact(self) -> None:
        """`SuiteRun.artifact` passed `evidence_cases=[]` unconditionally, so
        even a suite that DID extract something would have dropped it."""
        _, run = _run("agentdojo")
        artifact = run.artifact("a1", CREATED, ENVIRONMENT)
        self.assertEqual(len(artifact["evidence_cases"]), len(run.evidence_cases))
        self.assertEqual(validate_artifact(artifact, "artifact"), [])

    def test_budget_raises_nothing_when_it_stays_in_budget(self) -> None:
        _, run = _run("budget")
        self.assertEqual(run.evidence_cases, [])


class TestEvaluatorOutcomeRuns(unittest.TestCase):
    def setUp(self) -> None:
        self.suite, self.run = _run("budget")
        self.scenarios = {str(s["name"]): s for s in self.run.resolved.scenarios}
        self.evaluators = {
            "read_count": {"id": "read_count", "kind": "suite_hook",
                           "hook": "metrics_for", "produces": "reads"},
            "did_read": {"id": "did_read", "kind": "predicate", "produces": "did_read",
                         "predicate": {"event": "tool_call", "tool": BUDGET_READ_TOOL}},
            "steps": {"id": "steps", "kind": "trial_metric", "produces": "steps",
                      "metric": "steps"},
        }

    def _check(self, rule: dict[str, object]):
        return check_invariant(
            {"id": "RG-x", "rule": rule}, self.run.trials, self.run.traces,
            self.scenarios, self.evaluators,
        )

    def test_the_builtin_suite_pins_its_own_evaluator_and_it_passes(self) -> None:
        """End to end through the longest path on the platform: the suite's
        `metrics_for` hook -> trial.metrics -> the invariant."""
        by_id = {
            str(r["id"]): result
            for r, result in zip(self.run.resolved.regressions, self.run.invariants)
        }
        self.assertEqual(by_id["RG-budget-read-count"].status, STATUS_PASSED)

    def test_a_hook_backed_evaluator_resolves(self) -> None:
        result = self._check(
            {"kind": "evaluator_outcome", "evaluator": "read_count", "expect": 1}
        )
        self.assertEqual(result.status, STATUS_PASSED)

    def test_a_trial_metric_evaluator_resolves(self) -> None:
        steps = self.run.trials[0]["metrics"]["steps"]
        self.assertEqual(
            self._check(
                {"kind": "evaluator_outcome", "evaluator": "steps", "expect": steps}
            ).status,
            STATUS_PASSED,
        )

    def test_a_predicate_evaluator_resolves(self) -> None:
        result = self._check(
            {"kind": "evaluator_outcome", "evaluator": "did_read", "expect": True}
        )
        self.assertEqual(result.status, STATUS_PASSED, result.detail)

    def test_a_wrong_expectation_fails_rather_than_erroring(self) -> None:
        result = self._check(
            {"kind": "evaluator_outcome", "evaluator": "read_count", "expect": 99}
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertTrue(result.failing_trial_ids)

    def test_comparison_is_canonical_not_pythons(self) -> None:
        """`1 == True` in Python. The schema says canonical equality, and an
        evaluator producing a COUNT of one must not satisfy an expectation of
        `true` — those are different claims about the run."""
        result = self._check(
            {"kind": "evaluator_outcome", "evaluator": "read_count", "expect": True}
        )
        self.assertEqual(result.status, STATUS_FAILED)

    def test_an_undeclared_evaluator_is_an_error_naming_what_exists(self) -> None:
        result = self._check(
            {"kind": "evaluator_outcome", "evaluator": "nope", "expect": True}
        )
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("read_count", result.detail)

    def test_no_evaluator_table_at_all_is_an_error_not_a_pass(self) -> None:
        result = check_invariant(
            {"id": "RG-x", "rule": {"kind": "evaluator_outcome",
                                    "evaluator": "read_count", "expect": 1}},
            self.run.trials, self.run.traces, self.scenarios,
        )
        self.assertEqual(result.status, STATUS_ERROR)

    def test_an_evaluator_over_an_unmeasured_value_errors(self) -> None:
        """The cardinal rule of this module: not-knowing is not knowing the
        invariant held."""
        result = check_invariant(
            {"id": "RG-x", "rule": {"kind": "evaluator_outcome",
                                    "evaluator": "cost", "expect": 0}},
            self.run.trials, self.run.traces, self.scenarios,
            {"cost": {"id": "cost", "kind": "trial_metric", "produces": "cost_usd",
                      "metric": "cost_usd"}},
        )
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("cost_usd", result.detail)


class TestAllFourRuleKindsRunHere(unittest.TestCase):
    def test_no_rule_kind_is_reported_as_skipped(self) -> None:
        from lab_runner.invariants import _ELSEWHERE, _RUNS_HERE

        self.assertEqual(_ELSEWHERE, {})
        for kind in ("metric_threshold", "predicate", "evaluator_outcome",
                     "verdict_sequence"):
            self.assertIn(kind, _RUNS_HERE)

    def test_a_verdict_sequence_pin_reads_the_recorded_verdicts(self) -> None:
        """It checks the ALREADY-RECORDED verdict sequence of each trial — a
        trace read like every other rule. budget's ungoverned trace records a
        single ALLOW, so a pin expecting exactly that passes and one expecting
        DENY fails; neither skips."""
        _, run = _run("budget")
        passing = check_invariant(
            {"id": "RG-v", "rule": {"kind": "verdict_sequence", "verdicts": ["ALLOW"]}},
            run.trials, run.traces, {},
        )
        self.assertEqual(passing.status, "passed")
        failing = check_invariant(
            {"id": "RG-v2", "rule": {"kind": "verdict_sequence", "verdicts": ["DENY"]}},
            run.trials, run.traces, {},
        )
        self.assertEqual(failing.status, "failed")


if __name__ == "__main__":
    unittest.main()
