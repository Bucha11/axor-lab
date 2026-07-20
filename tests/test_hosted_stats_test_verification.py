"""Server recomputes and compares the statistical TEST (review round 7, P0).

The publish handshake recomputed n/estimate/interval but NOT the test object,
and mapped any unknown metric to task_success. So a caller could upload correct
marginals with a fabricated McNemar p-value (or a live run relabelled
matched_pairs), or launder an arbitrary metric name — and the server would mint a
'server-recomputed' claim. The server now recomputes the test from the evidence,
rejects an unknown metric, and rejects matched_pairs for a live-model environment.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server.errors import PublishRejected
from lab_server.recompute import check_aggregates
from lab_server.store import PublicationStore

CREATED = "2026-07-19T12:00:00+00:00"


def _run():
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=10, run_id="r_stv",
    )
    return scenario, result


def _bundle(aggregates, environment=None):
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=10, run_id="r_stv",
    )
    return build_bundle(
        bundle_id="b_stv", created=CREATED, scenarios=[scenario], conditions=support.conditions(),
        tool_manifests=list(support.manifests().values()),
        environment=environment or support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    ), result.traces


def _honest_aggregates(result):
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    return [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs),
                         comparison_design="matched_pairs"),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned"),
                         comparison_design="matched_pairs"),
    ]


class TestTestVerification(unittest.TestCase):
    def _publish(self, aggregates, environment=None):
        bundle, traces = _bundle(aggregates, environment)
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            return store.publish(bundle, traces, question="q", visibility="public")

    def test_honest_matched_pairs_publishes(self) -> None:
        _, result = _run()
        stored = self._publish(_honest_aggregates(result))
        self.assertEqual(stored.publication.get("statistics_integrity"), "recomputed_from_traces")

    def test_fabricated_mcnemar_p_value_is_rejected(self) -> None:
        _, result = _run()
        aggs = _honest_aggregates(result)
        # keep the honest marginals but forge the discordant counts + p-value
        aggs[1]["test"] = {"name": "mcnemar", "vs": "ungoverned",
                           "discordant": {"b": 90, "c": 0}, "paired_n": 10, "p": 1e-30}
        with self.assertRaises(PublishRejected) as ctx:
            self._publish(aggs)
        self.assertIn("recomputation", str(ctx.exception).lower())

    def test_unknown_metric_is_rejected(self) -> None:
        _, result = _run()
        n = len(result.pairs("ungoverned", "governed", metric="ASR"))
        succ = sum(1 for _, t in result.pairs("ungoverned", "governed", metric="ASR") if t)
        fake = [binary_aggregate("zero_production_incidents", "governed", succ, n)]
        with self.assertRaises(PublishRejected) as ctx:
            self._publish(fake)
        self.assertIn("recomputation", str(ctx.exception).lower())

    def test_matched_pairs_with_live_environment_is_rejected(self) -> None:
        _, result = _run()
        aggs = _honest_aggregates(result)
        live_env = copy.deepcopy(support.environment())
        live_env["model"] = {"provider": "anthropic", "id": "claude-x"}
        bundle, traces = _bundle(aggs, environment=live_env)
        problems = check_aggregates(bundle, traces)
        self.assertTrue(any("matched_pairs but the environment is a live model" in p
                            for p in problems))

    def test_empty_or_imported_provider_does_not_imply_deterministic(self) -> None:
        # an empty/unknown provider must NOT silently enable a paired test —
        # only an explicitly deterministic provider string does (review r14)
        _, result = _run()
        for provider in ("", "imported", "mystery-runner"):
            env = copy.deepcopy(support.environment())
            env["model"] = {"provider": provider, "id": "x"}
            bundle, traces = _bundle(_honest_aggregates(result), environment=env)
            problems = check_aggregates(bundle, traces)
            self.assertTrue(
                any("matched_pairs but the environment is a live model" in p for p in problems),
                f"provider {provider!r} should not auto-enable matched_pairs",
            )

    def test_matched_pairs_claim_is_marked_uploader_declared(self) -> None:
        # even for a declared-deterministic provider the server can't PROVE the
        # pairing — the claim text must say the design is uploader-declared (r14)
        _, result = _run()
        stored = self._publish(_honest_aggregates(result))
        stat = next(c for c in stored.publication["claims"]
                    if c["kind"] == "statistically_reproducible")
        self.assertIn("UPLOADER-DECLARED", stat["text"])
        self.assertIn("not attested", stat["text"])


if __name__ == "__main__":
    unittest.main()
