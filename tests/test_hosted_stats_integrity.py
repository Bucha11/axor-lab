"""Hosted statistical-claim integrity (review round 2, Patch 4).

The server used to mint a 'statistically reproducible … over N trials' claim
straight from the uploaded aggregate, checking only that the bundle's own hashes
were self-consistent. So a caller could fabricate estimate=0, n=1_000_000,
recompute their own content hashes, and the server would vouch for it. Now the
server recomputes every aggregate from the trials + traces + scenario predicates
and rejects any bundle whose uploaded numbers don't follow from its evidence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, content_hash
from lab_runner import run_experiment_suite
from lab_server.errors import PublishRejected
from lab_server.recompute import check_aggregates
from lab_server.store import PublicationStore

REPEATS = 8
CREATED = "2026-07-19T12:00:00+00:00"


def _honest_bundle() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=REPEATS, run_id="r_stat",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate(
            "ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
            test=mcnemar_test(pairs, vs="ungoverned"),
        ),
    ]
    bundle = build_bundle(
        bundle_id="b_stat", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    return bundle, result.traces


def _fabricated_bundle() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Same traces, but the aggregates claim a million trials that never ran."""
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=REPEATS, run_id="r_fake",
    )
    fake = [
        binary_aggregate("ASR", "ungoverned", 0, 1_000_000),
        binary_aggregate("ASR", "governed", 0, 1_000_000),
    ]
    # rebuild with fresh (valid) content hashes over the fabricated aggregates —
    # the exact attack: hash verification alone cannot catch this
    bundle = build_bundle(
        bundle_id="b_fake", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=fake, traces=result.traces,
    )
    return bundle, result.traces


class TestMatchedPairsParityAtMissingness(unittest.TestCase):
    """The server's recompute must AGREE with the runner's own aggregation when
    a trial is missing. The runner reports each marginal over the completed
    trials OF THAT CONDITION and pairs only the baseline∩treated intersection in
    the McNemar test. The server used to recompute the matched-pairs marginal
    over the all-conditions intersection, so one failed baseline trial shrank
    every condition's recomputed n and the server REJECTED an honest,
    runner-produced bundle at missingness (review r12)."""

    MISS_REPEATS = 12  # > 10 so the aggregate is conclusive and carries the test

    def _bundle_with_one_missing_treated_trial(self):
        scenario = support.banking_scenario()
        conditions = support.conditions()  # ungoverned (off) + governed (on)
        result = run_experiment_suite(
            [scenario], support.manifests(), conditions, support.kernel_registry(),
            repeats=self.MISS_REPEATS, run_id="r_miss",
        )
        # induce missingness: turn ONE completed governed trial into a failure
        # (a failed trial carries no trace/outcome — exactly as the runner records it)
        victim = next(
            t for t in result.trials
            if t["condition_id"] == "governed" and t["status"] == "completed"
        )
        victim_ref = str(victim["trace_ref"])
        trials: list[dict[str, object]] = []
        for t in result.trials:
            if t["trial_id"] == victim["trial_id"]:
                failed = {k: v for k, v in t.items() if k != "trace_ref"}
                failed["status"] = "failed"
                failed["failure_reason"] = "SimulatedError: dropped"
                trials.append(failed)
            else:
                trials.append(dict(t))
        traces = {
            tid: tr for tid, tr in result.traces.items() if content_hash(tr) != victim_ref
        }

        # aggregate EXACTLY as the runner does: marginal per condition + McNemar
        # over the pairwise intersection
        def marginal(cid: str) -> tuple[int, int]:
            outs = [
                result.outcomes[t["trial_id"]] for t in trials
                if t["condition_id"] == cid and t["status"] == "completed"
            ]
            return len(outs), sum(1 for o in outs if o.violation)

        by_key: dict[tuple[str, str, int], dict[str, bool]] = {}
        for t in trials:
            if t["status"] != "completed":
                continue
            o = result.outcomes[t["trial_id"]]
            key = (str(t["scenario_id"]), str(t["seed"]), int(t["repeat_index"]))
            by_key.setdefault(key, {})[str(t["condition_id"])] = o.violation
        pairs = [
            (row["ungoverned"], row["governed"]) for row in by_key.values()
            if "ungoverned" in row and "governed" in row
        ]

        n_ung, s_ung = marginal("ungoverned")
        n_gov, s_gov = marginal("governed")
        # the missingness is REAL and asymmetric: baseline keeps all trials,
        # treated lost one, so the marginal ns differ AND both exceed... no:
        # treated marginal == paired_n here (governed ⊆ ungoverned completions)
        self.assertEqual(n_ung, self.MISS_REPEATS)
        self.assertEqual(n_gov, self.MISS_REPEATS - 1)
        self.assertEqual(len(pairs), self.MISS_REPEATS - 1)

        aggregates = [
            binary_aggregate("ASR", "ungoverned", s_ung, n_ung,
                             comparison_design="matched_pairs"),
            binary_aggregate("ASR", "governed", s_gov, n_gov,
                             test=mcnemar_test(pairs, vs="ungoverned"),
                             comparison_design="matched_pairs"),
        ]
        bundle = build_bundle(
            bundle_id="b_miss", created=CREATED, scenarios=[scenario], conditions=conditions,
            tool_manifests=list(support.manifests().values()), environment=support.environment(),
            trials=trials, aggregates=aggregates, traces=traces,
        )
        return bundle, traces

    def test_runner_aggregates_pass_the_server_recompute_at_missingness(self) -> None:
        bundle, traces = self._bundle_with_one_missing_treated_trial()
        problems = check_aggregates(bundle, traces)
        self.assertEqual(problems, [], f"honest runner bundle rejected: {problems}")

    def test_it_actually_publishes(self) -> None:
        bundle, traces = self._bundle_with_one_missing_treated_trial()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            stored = store.publish(bundle, traces, question="parity at missingness?")
            self.assertEqual(
                stored.publication.get("statistics_integrity"), "recomputed_from_traces"
            )


class TestHostedStatisticsIntegrity(unittest.TestCase):
    def test_honest_bundle_publishes_and_is_marked_recomputed(self) -> None:
        bundle, traces = _honest_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            stored = store.publish(bundle, traces, question="does governance stop exfil?")
            self.assertEqual(
                stored.publication.get("statistics_integrity"), "recomputed_from_traces"
            )
            stat_claims = [
                c for c in stored.publication["claims"]  # type: ignore[union-attr]
                if c["kind"] == "statistically_reproducible"
            ]
            self.assertTrue(stat_claims)
            self.assertIn("server-recomputed", stat_claims[0]["text"])
            self.assertNotIn("live trials", stat_claims[0]["text"])

    def test_fabricated_aggregate_is_rejected(self) -> None:
        bundle, traces = _fabricated_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            with self.assertRaises(PublishRejected) as ctx:
                store.publish(bundle, traces, question="fabricated")
            self.assertIn("recomputation", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
