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
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server.errors import PublishRejected
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
