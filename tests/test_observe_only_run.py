"""A run with no governed arm at all.

The likeliest FIRST run anyone does: wrap the agent, let the kernel watch, gate
nothing, and find out what the thing actually does. Every other test here drives
a contrast — ungoverned against governed — so the single-arm path was exercised
only by the Budget suite, which measures a duration and has no attack model.

The danger is not a crash. It is that an observe-only run produces a perfectly
well-formed artifact whose ASR reads, in a results table, exactly like a finding
about a governed system. So these pin BOTH halves: that the path works end to
end, and that nothing along it lets an unprotected measurement pass for a
governance result.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_capabilities.governance.cp_export import CPExportError
from lab_service import build_cp_export_files, build_paper_report
from lab_server.store import PublicationStore
from lab_suite import builtin_registry
from lab_suite.errors import SuiteValidationError
from lab_suite.execute import run_suite
from lab_suite.sdk import comparison_design

CREATED = "2026-09-13T00:00:00+00:00"


def _observe_only_manifest(*, keep_tests: bool = False) -> dict[str, object]:
    """The ingest suite with every enforcing arm removed."""
    manifest = copy.deepcopy(builtin_registry().get("ingest").manifest())
    manifest["execution"]["conditions"] = [  # type: ignore[index]
        c for c in manifest["execution"]["conditions"]  # type: ignore[index]
        if c["enforcement"] == "off"
    ]
    manifest["execution"]["repeats"] = 4  # type: ignore[index]
    if not keep_tests:
        for aggregation in manifest["evaluation"]["aggregations"]:  # type: ignore[index]
            aggregation.pop("test", None)
    return manifest


def _run():
    suite = builtin_registry().get("ingest")
    run = run_suite(_observe_only_manifest(), run_id="r_observe", suite=suite)
    environment = dict(support.environment())
    environment["experiment_design"] = comparison_design(suite)
    return run, environment


class TestAuthoringRefusesAnImpossibleComparison(unittest.TestCase):
    def test_a_declared_mcnemar_needs_a_second_arm(self) -> None:
        """Caught at RESOLVE time, before a single trial runs. Discovering it
        from an empty `test` field in the artifact would waste the whole run."""
        with self.assertRaises(SuiteValidationError) as caught:
            run_suite(_observe_only_manifest(keep_tests=True), run_id="r_bad")
        message = str(caught.exception)
        self.assertIn("mcnemar", message)
        self.assertIn("declares 1 condition", message)


class TestTheRunStillProduces(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite_run, cls.environment = _run()

    def test_the_marginals_are_computed_without_a_baseline_to_compare_to(self) -> None:
        by_metric = {
            str(a["metric"]): a for a in self.suite_run.aggregates
        }
        self.assertEqual(sorted(by_metric), ["ASR", "task_success"])
        # and NO test is attached — there is nothing to compare against
        self.assertTrue(all(a.get("test") is None for a in self.suite_run.aggregates))

    def test_the_breach_rate_counts_only_the_attacked_trials(self) -> None:
        """One of the three ingest scenarios is clean. Counting it in ASR's
        denominator would report a rate over trials that could not have violated
        anything."""
        by_metric = {str(a["metric"]): int(a["n"]) for a in self.suite_run.aggregates}  # type: ignore[arg-type]
        self.assertEqual(by_metric["ASR"], 8)
        self.assertEqual(by_metric["task_success"], 12)

    def test_an_invariant_scoped_to_a_missing_arm_errors_rather_than_passes(self) -> None:
        """A negative predicate over zero trials is vacuously true, and a green
        tick for a check that never ran is worse than a red one."""
        scoped = next(i for i in self.suite_run.invariants if i.regression_id == "RG-ingest-no-exfil")
        self.assertEqual(scoped.status, "error")
        self.assertEqual(scoped.trials_checked, 0)
        self.assertIn("never evaluated", scoped.detail)
        # the arm-independent invariant still ran
        every_arm = next(
            i for i in self.suite_run.invariants if i.regression_id == "RG-ingest-files-the-record")
        self.assertEqual(every_arm.status, "passed")


class TestTheReportRefusesToImplyGovernance(unittest.TestCase):
    """The whole risk of this path. An artifact with `ASR = 0.75` and no treated
    arm is a measurement of an UNPROTECTED agent, and a results table that does
    not say so has been quietly turned into a claim about containment."""

    @classmethod
    def setUpClass(cls) -> None:
        run, environment = _run()
        cls.report = build_paper_report(run.artifact("a_observe", CREATED, environment))

    def test_the_first_caveat_is_that_nothing_was_enforced(self) -> None:
        self.assertIn("NO ARM ENFORCED", self.report.caveats[0])
        self.assertIn("baseline", self.report.caveats[0].lower())

    def test_methods_says_the_kernel_gated_nothing(self) -> None:
        methods = self.report.markdown.split("## Methods")[1]
        self.assertIn("No arm enforced", methods)
        self.assertIn("makes no claim about governance", methods)

    def test_it_does_not_claim_the_numbers_describe_a_governed_system(self) -> None:
        """The caveat used to read "these numbers describe the harness UNDER
        GOVERNANCE" — written for a contrast run, and false for this one."""
        self.assertNotIn("under governance", self.report.markdown)

    def test_the_two_denominators_are_explained_as_design_not_missingness(self) -> None:
        """ASR over 8 and task_success over 12 is the first thing a reviewer
        asks about, and "some trials failed" is the wrong answer."""
        note = next(c for c in self.report.caveats if "Denominators differ" in c)
        self.assertIn("by DESIGN, not by missing data", note)
        self.assertIn("1 of 3 scenarios", note)
        self.assertIn("n=12/12", self.report.markdown)  # nothing was actually missing

    def test_no_comparison_section_is_invented(self) -> None:
        self.assertNotIn("## Comparisons", self.report.markdown)
        self.assertNotIn("McNemar", self.report.markdown)

    def test_a_contrast_run_does_not_carry_the_observe_only_caveat(self) -> None:
        """Guard the guard: the warning has to be absent when it does not apply,
        or it is noise everyone learns to skip."""
        suite = builtin_registry().get("ingest")
        run = run_suite(suite.manifest(), run_id="r_contrast")
        environment = dict(support.environment())
        environment["experiment_design"] = comparison_design(suite)
        report = build_paper_report(run.artifact("a_contrast", CREATED, environment))
        self.assertFalse(any("NO ARM ENFORCED" in c for c in report.caveats))


class TestTheExportDoors(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run, environment = _run()
        cls.bundle = run.bundle("b_observe", CREATED, environment)
        cls.traces = run.traces

    def test_it_publishes_like_any_other_artifact(self) -> None:
        """A baseline IS a result worth publishing — it is the number every
        later comparison is against."""
        with tempfile.TemporaryDirectory() as tmp:
            stored = PublicationStore(root=Path(tmp)).publish(
                self.bundle, self.traces, question="What does the agent do unprotected?")
        self.assertEqual(
            stored.publication.get("statistics_integrity"), "recomputed_from_traces")
        claimed = {str(c["support_ref"]) for c in stored.publication["claims"]}  # type: ignore[union-attr]
        self.assertIn("agg:ASR:ungoverned", claimed)

    def test_the_control_plane_handoff_is_refused_with_a_reason(self) -> None:
        """There is no policy to carry over. Refusing is the whole point: the
        handoff deploys a validated ENFORCING config, and this run validated
        none."""
        with self.assertRaises(CPExportError) as caught:
            build_cp_export_files(self.bundle, self.traces)
        self.assertIn("no enforcement-on condition", str(caught.exception))


class TestTheRunReportScreenSaysIt(unittest.TestCase):
    """The screen where someone FIRST reads the number, before any export."""

    def _report(self, conditions: list[dict[str, object]]) -> dict[str, object]:
        from lab_server.screens import run_report

        return run_report({
            "run_id": "r_observe",
            "planned_trials": ["u1"],
            "trials": [{"trial_id": "u1", "status": "completed",
                        "metrics": {"ASR": True, "duration_ms": 1.0}}],
            "aggregates": [
                {"metric": "ASR", "condition_id": "ungoverned", "estimate": 0.75, "n": 8},
                {"metric": "duration_ms", "condition_id": "ungoverned",
                 "estimate": 1.0, "n": 8},
            ],
            "conditions": conditions,
        })

    def test_the_arms_travel_with_the_report(self) -> None:
        """Without them the screen cannot tell a governed contrast from a bare
        observation, and the two render identically."""
        report = self._report([{"id": "ungoverned", "enforcement": "off"}])
        self.assertEqual(report["conditions"], [{"id": "ungoverned", "enforcement": "off"}])

    def test_each_aggregate_carries_its_evidence_tier(self) -> None:
        """Decided by the SERVER from the one shared registry — a second copy of
        that list in the browser is a second chance for it to drift open."""
        tiers = {
            str(a["metric"]): a["evidence"]
            for a in self._report([{"id": "ungoverned", "enforcement": "off"}])["aggregates"]  # type: ignore[union-attr]
        }
        self.assertEqual(tiers, {"ASR": "derived", "duration_ms": "self_reported"})


class TestOneRegistry(unittest.TestCase):
    def test_the_publish_handshake_and_the_report_agree(self) -> None:
        """These lists used to be written twice. A metric derivable for the
        paper report and not for the publish server (or the reverse) would put
        an unbacked number in a manuscript, or refuse a backed one."""
        from lab_analysis import DERIVED_METRIC_OUTCOMES, metric_is_derived
        from lab_server.recompute import _METRIC_OUTCOME

        self.assertIs(_METRIC_OUTCOME, DERIVED_METRIC_OUTCOMES)
        for metric in DERIVED_METRIC_OUTCOMES:
            self.assertTrue(metric_is_derived(metric))
        self.assertFalse(metric_is_derived("zero_production_incidents"))


if __name__ == "__main__":
    unittest.main()
