"""Statistical evidence completeness (review r15).

A test's power is its OWN effective n, not a marginal aggregate n; the server
recomputes the WHOLE test object (interval included) and rejects fields it does
not produce; and a hosted statistical claim reports completed-over-planned and
flags condition-imbalanced missingness.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test, two_proportion_test
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server.recompute import check_aggregates
from lab_server.store import PublicationStore

CREATED = "2026-07-20T12:00:00+00:00"


def _bundle_and_traces(environment: dict | None = None):
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=10, run_id="r_sc",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_sc", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()),
        environment=environment or support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestTestEffectiveN(unittest.TestCase):
    def test_mcnemar_with_paired_n_below_threshold_is_inconclusive(self) -> None:
        test = mcnemar_test([(True, False)], vs="baseline")  # a single pair
        self.assertEqual(test["effective_n"], 1)
        self.assertEqual(test["status"], "inconclusive")

    def test_underpowered_test_is_not_attached_to_a_large_aggregate(self) -> None:
        # marginal n=100 but the paired test has just 1 pair — it must NOT ride
        # along on the big n and read as significant
        one_pair = mcnemar_test([(True, False)], vs="baseline")
        agg = binary_aggregate("ASR", "governed", 5, 100, test=one_pair)
        self.assertNotIn("test", agg)

    def test_powered_test_is_attached(self) -> None:
        pairs = [(True, False)] * 12
        agg = binary_aggregate("ASR", "governed", 0, 12, test=mcnemar_test(pairs, vs="b"))
        self.assertIn("test", agg)
        self.assertEqual(agg["test"]["status"], "conclusive")


class TestServerRecomputesWholeTest(unittest.TestCase):
    def test_unknown_test_fields_are_rejected(self) -> None:
        bundle, traces = _bundle_and_traces()
        tampered = copy.deepcopy(bundle)
        gov = next(a for a in tampered["aggregates"] if a["condition_id"] == "governed")
        gov["test"]["fabricated_field"] = 0.001  # a field the server never recomputes
        problems = check_aggregates(tampered, traces)
        self.assertTrue(any("unrecognized field" in p for p in problems), problems)

    def test_two_proportion_interval_is_server_recomputed(self) -> None:
        # an independent-samples bundle (live env) whose two_proportion interval is
        # fabricated: difference and p match, but the interval is bogus → rejected
        env = {"kernel_version": support.KERNEL_PINNED,
               "model": {"id": "m", "provider": "byok-live"}}
        bundle, traces = _bundle_and_traces(environment=env)
        # replace the governed aggregate with a two_proportion test + bad interval
        rows_base = sum(1 for a in bundle["aggregates"] if a["condition_id"] == "ungoverned")
        self.assertTrue(rows_base)
        base = next(a for a in bundle["aggregates"] if a["condition_id"] == "ungoverned")
        gov = next(a for a in bundle["aggregates"] if a["condition_id"] == "governed")
        tp = two_proportion_test(int(base["estimate"] * base["n"]), base["n"],
                                 int(gov["estimate"] * gov["n"]), gov["n"], vs="ungoverned")
        # both arms are independent-samples under a live env; only governed carries
        # the two_proportion test
        base["comparison_design"] = "independent_samples"
        gov["comparison_design"] = "independent_samples"
        gov["test"] = tp
        # honest first: recompute passes clean
        self.assertEqual(check_aggregates(bundle, traces), [])
        # now fabricate the interval — difference and p still match, interval does not
        gov["test"]["interval"] = {"method": "newcombe", "low": 0.9, "high": 1.0}
        problems = check_aggregates(bundle, traces)
        self.assertTrue(any("interval" in p for p in problems), problems)


class TestHostedClaimReportsMissingness(unittest.TestCase):
    def _store(self) -> PublicationStore:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return PublicationStore(root=Path(self.tmp.name))

    def test_publication_reports_completed_over_planned(self) -> None:
        store = self._store()
        bundle, traces = _bundle_and_traces()
        pub = store.publish(bundle, traces, question="q")
        stat = next(c for c in pub.publication["claims"]
                    if c["kind"] == "statistically_reproducible")
        self.assertIn("completed)", stat["text"])  # e.g. "(10/10 completed)"

    def test_publication_reports_condition_imbalanced_missingness(self) -> None:
        # exercise the claim builder directly with an imbalanced trial set: the
        # governed arm loses most of its trials (a real asymmetric missingness),
        # so the statistical claim must flag it. Using _mint isolates the claim
        # text from the recompute gate (covered elsewhere).
        store = self._store()
        bundle, traces = _bundle_and_traces()
        trials = list(bundle["trials"])
        flipped = 0
        for trial in trials:
            if trial["condition_id"] == "governed" and trial["status"] == "completed" and flipped < 8:
                trial["status"] = "excluded"
                trial["failure_reason"] = "provider_error"
                flipped += 1
        bundle["trials"] = trials
        publication = store._mint(bundle, traces, "q_imb", "CC-BY-4.0", "unlisted")
        texts = " ".join(c["text"] for c in publication["claims"])
        self.assertIn("condition-imbalanced", texts)


if __name__ == "__main__":
    unittest.main()
