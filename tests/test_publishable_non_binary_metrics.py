"""A suite that measures something other than a rate must be publishable.

It was not. The publish handshake recomputed every aggregate from the traces
and REJECTED any metric it could not derive — so `mean(duration_ms)` came back
"unknown metric" and two of the four built-in suites could not be published at
all. Rejection looks like the safe default and is not: it makes an honest figure
unpublishable while teaching nobody anything about what backs a number.

publication/v1 already had the word for this. `statistics_integrity` carries
`self_reported` — "taken from the upload on faith (never used for a
statistically_reproducible claim)". So there are TWO TIERS, and the difference
is what may be CLAIMED:

  derived   — a boolean predicate the server re-evaluates against each trace.
              Backs a `statistically_reproducible` claim.
  reported  — latency, tokens, spend: the runner's own measurements, present in
              no trace. The server re-applies the declared ESTIMATOR to the
              reported per-trial values, which proves the arithmetic and
              nothing about the observations. No claim.

These pin both halves: that the second tier publishes, and that it stays
visibly weaker than the first rather than quietly borrowing its authority.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import numeric_aggregate
from lab_contracts import build_bundle
from lab_server.errors import PublishRejected
from lab_server.store import PublicationStore
from lab_suite import builtin_registry
from lab_suite.execute import run_suite
from lab_suite.sdk import comparison_design

CREATED = "2026-09-13T00:00:00+00:00"


def _run(suite_id: str):
    """A real execution of a built-in — the per-trial numbers have to be the
    runner's own, or the re-application check would be testing a fixture."""
    suite = builtin_registry().get(suite_id)
    run = run_suite(suite.manifest(), run_id=f"r_{suite_id}")
    environment = dict(support.environment())
    environment["experiment_design"] = comparison_design(suite)
    return run, environment


def _bundle(run, environment, aggregates=None) -> dict[str, object]:
    """Rebuild with FRESH content hashes over whatever aggregates are passed.

    Editing an aggregate in place would be caught by the hash check, which is a
    different (and already tested) defence — the point here is a bundle that is
    internally perfect and still does not follow from its own trials.
    """
    return build_bundle(
        bundle_id="b_reported", created=CREATED,
        scenarios=list(run.resolved.scenarios), conditions=run.conditions,
        tool_manifests=list(run.resolved.manifests.values()),
        environment=environment, trials=run.trials,
        aggregates=run.aggregates if aggregates is None else aggregates,
        traces=run.traces,
    )


def _publish(bundle, traces, question="what does it answer?"):
    with tempfile.TemporaryDirectory() as tmp:
        return PublicationStore(root=Path(tmp)).publish(
            bundle, traces, question=question)


class TestTheEstimator(unittest.TestCase):
    """One shared aggregator, so the runner and the server compute a mean the
    same way. They used to have separate implementations, and a check that
    compares two implementations of the same arithmetic is a check that will
    eventually reject an honest run for a rounding difference."""

    def test_each_estimator_reports_its_own_name(self) -> None:
        for estimator, expected in (
            ("mean", 2.0), ("sum", 6.0), ("min", 1.0), ("max", 3.0),
        ):
            with self.subTest(estimator=estimator):
                agg = numeric_aggregate("latency", "c", [1.0, 2.0, 3.0], estimator)
                self.assertEqual(agg["estimator"], estimator)
                self.assertAlmostEqual(float(agg["estimate"]), expected)
                self.assertEqual(agg["n"], 3)

    def test_the_interval_is_the_observed_range_and_says_so(self) -> None:
        """Not a confidence interval. Calling the range an interval without
        naming the method would let a reader read sampling uncertainty into a
        number that carries none."""
        agg = numeric_aggregate("latency", "c", [1.0, 5.0], "mean")
        self.assertEqual(agg["interval"], {"method": "none", "low": 1.0, "high": 5.0})

    def test_a_rate_names_its_estimator_too(self) -> None:
        """The derived tier is not exempt: the server decides which tier an
        aggregate is in from the METRIC, but a reader reads the estimator."""
        from lab_analysis import binary_aggregate

        self.assertEqual(binary_aggregate("ASR", "c", 1, 4)["estimator"], "rate")


class TestEveryBuiltinPublishes(unittest.TestCase):
    def test_all_four_suites_reach_a_publication(self) -> None:
        """The regression that started this: a suite whose only metric was a
        duration could not be published, so the Suite Platform shipped an
        export path two of its own built-ins could not walk."""
        for suite_id in builtin_registry().ids():
            with self.subTest(suite=suite_id):
                run, environment = _run(suite_id)
                stored = _publish(_bundle(run, environment), run.traces)
                self.assertIn(
                    stored.publication.get("statistics_integrity"),
                    ("recomputed_from_traces", "self_reported"),
                )


class TestTheTwoTiersStaySeparate(unittest.TestCase):
    def setUp(self) -> None:
        self.run, self.environment = _run("budget")
        self.metrics = {str(a["metric"]) for a in self.run.aggregates}

    def test_the_run_actually_mixes_the_tiers(self) -> None:
        """Guard the guard: if the budget suite stopped reporting a duration,
        the tests below would pass by measuring nothing."""
        self.assertIn("task_success", self.metrics)   # derived from the traces
        self.assertIn("duration_ms", self.metrics)    # the runner's own clock

    def test_only_the_derived_metric_earns_a_claim(self) -> None:
        stored = _publish(_bundle(self.run, self.environment), self.run.traces)
        claimed = {
            str(c["support_ref"]) for c in stored.publication["claims"]  # type: ignore[union-attr]
            if c["kind"] == "statistically_reproducible"
        }
        self.assertIn("agg:task_success:ungoverned", claimed)
        # carried in the bundle, readable, and claimed by nobody
        self.assertNotIn("agg:duration_ms:ungoverned", claimed)
        self.assertNotIn("agg:tool_calls:ungoverned", claimed)
        published = {
            (str(a["metric"]), str(a["condition_id"]))
            for a in stored.bundle["aggregates"]  # type: ignore[union-attr]
        }
        self.assertIn(("duration_ms", "ungoverned"), published)

    def test_a_bundle_with_no_derivable_metric_is_marked_self_reported(self) -> None:
        """The axis has to move. Marking an all-reported bundle
        `recomputed_from_traces` would attest a derivation the server could not
        perform; marking it None would hide that statistics were published."""
        run, environment = _run("blank")
        self.assertEqual(
            {str(a["metric"]) for a in run.aggregates}, {"duration_ms"},
        )
        stored = _publish(_bundle(run, environment), run.traces)
        self.assertEqual(stored.publication.get("statistics_integrity"), "self_reported")
        self.assertEqual(stored.publication["claims"], [])

    def test_the_acceptance_receipt_does_not_say_recomputed(self) -> None:
        """The receipt lists the checks that ACTUALLY RAN. Saying
        "statistics_recomputed" over a bundle whose every aggregate was
        re-applied rather than derived is the same overstatement one layer
        down, where a reader is least likely to look."""
        run, environment = _run("blank")
        stored = _publish(_bundle(run, environment), run.traces)
        verified = stored.acceptance["semantic_report"]["verified"]  # type: ignore[index]
        self.assertIn("statistics_estimator_reapplied", verified)
        self.assertNotIn("statistics_recomputed", verified)


class TestTheWeakerCheckStillBites(unittest.TestCase):
    """Self-reported is not unchecked. The step the server CAN see — from the
    per-trial numbers to the headline — is re-applied, so the estimate has to
    follow from the values printed beside it."""

    def setUp(self) -> None:
        self.run, self.environment = _run("budget")

    def _mutated(self, mutate) -> dict[str, object]:
        aggregates = copy.deepcopy(self.run.aggregates)
        mutate(next(a for a in aggregates if a["metric"] == "duration_ms"))
        return _bundle(self.run, self.environment, aggregates)

    def test_an_estimate_that_does_not_follow_is_rejected(self) -> None:
        bundle = self._mutated(lambda a: a.update({"estimate": 0.001}))
        with self.assertRaises(PublishRejected) as caught:
            _publish(bundle, self.run.traces)
        self.assertIn("mean of the reported values", str(caught.exception))

    def test_an_inflated_n_is_rejected(self) -> None:
        """The original fabrication, one tier down: a real mean over a claimed
        million trials. Counting the reported values catches it."""
        bundle = self._mutated(lambda a: a.update({"n": 1_000_000}))
        with self.assertRaises(PublishRejected) as caught:
            _publish(bundle, self.run.traces)
        self.assertIn("reported trial values", str(caught.exception))

    def test_a_comparison_test_over_a_reported_metric_is_refused(self) -> None:
        """A p-value the server cannot recompute must not ride along. It would
        be the one field in the publication carrying more authority than the
        tier it sits in, and no reader would catch it."""
        bundle = self._mutated(lambda a: a.update({
            "test": {"name": "mcnemar", "vs": "ungoverned", "p_value": 0.001,
                     "status": "significant"},
        }))
        with self.assertRaises(PublishRejected) as caught:
            _publish(bundle, self.run.traces)
        self.assertIn("cannot recompute a comparison test", str(caught.exception))

    def test_an_aggregate_naming_no_estimator_is_taken_as_reported(self) -> None:
        """Accepted, not guessed. Trying every function until one matched would
        let the uploader pick whichever the number happened to fit — and it
        earns no claim either way, which is what makes accepting it safe."""
        bundle = self._mutated(lambda a: a.pop("estimator"))
        stored = _publish(bundle, self.run.traces)
        claimed = {
            str(c["support_ref"]) for c in stored.publication["claims"]  # type: ignore[union-attr]
        }
        self.assertNotIn("agg:duration_ms:ungoverned", claimed)

    def test_a_rate_over_an_undeclared_metric_is_still_refused(self) -> None:
        """The weaker tier is not a way around the closed registry. `rate` is
        the DERIVED tier's estimator, so an arbitrary label carrying a
        proportion the server computed from nothing must be refused exactly as
        before — otherwise "zero_production_incidents: 0.0" publishes by simply
        not being a metric anyone recognises."""
        from lab_analysis import binary_aggregate

        aggregates = copy.deepcopy(self.run.aggregates)
        aggregates.append(binary_aggregate("zero_production_incidents", "ungoverned", 0, 500))
        bundle = _bundle(self.run, self.environment, aggregates)
        with self.assertRaises(PublishRejected) as caught:
            _publish(bundle, self.run.traces)
        self.assertIn("not one the server can re-apply", str(caught.exception))

    def test_an_aggregate_over_a_metric_nothing_measured_is_refused(self) -> None:
        """Weaker than self-reported: not reported at all. A number no trial
        produced summarizes nothing the bundle contains, and the estimator
        check cannot catch it because there is nothing to re-apply."""
        aggregates = copy.deepcopy(self.run.aggregates)
        aggregates.append({
            "metric": "cost_usd", "condition_id": "ungoverned", "estimate": 0.02,
            "interval": {"method": "none", "low": 0.02, "high": 0.02},
            "n": 5, "unit_of_analysis": "trial",
        })
        bundle = _bundle(self.run, self.environment, aggregates)
        with self.assertRaises(PublishRejected) as caught:
            _publish(bundle, self.run.traces)
        self.assertIn("no completed trial reports a numeric", str(caught.exception))

    def test_a_boolean_per_trial_value_is_never_averaged(self) -> None:
        """`True` is not 1.0 here. A boolean metric belongs to the derived tier,
        and silently averaging one would publish a rate the server never checked
        against a single trace."""
        from lab_server.recompute import _reported_values

        bundle = _bundle(self.run, self.environment)
        bundle["trials"][0]["metrics"]["flagged"] = True  # type: ignore[index]
        self.assertEqual(_reported_values(bundle, "flagged", "ungoverned"), [])


if __name__ == "__main__":
    unittest.main()
