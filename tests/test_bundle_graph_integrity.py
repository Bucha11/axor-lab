"""Bundle evidence-graph integrity (review round 3, Patch 7).

Hash verification proves each JSON object is intact; these tests prove the
ARROWS between objects hold. The attack the binding stops: reuse one real trace
as the "evidence" for many fabricated trials (inflating n), or cite a trace from
a different scenario/condition than the trial claims. build_bundle recomputes
content hashes, so every fraudulent bundle below passes hashing and must be
caught by the graph checks instead.
"""

from __future__ import annotations

import copy
import unittest

from tests import support
from lab_contracts import BundleIntegrityError, build_bundle, content_hash, verify_bundle
from lab_runner import run_experiment_suite

CREATED = "2026-07-19T12:00:00+00:00"


def _run() -> tuple[list, dict, dict]:
    scenario = support.banking_scenario()
    conditions = support.conditions()  # ungoverned + governed
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=3, run_id="r_graph",
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return result.trials, traces, {"scenario": scenario, "conditions": conditions}


def _bundle(trials: list, traces: dict, ctx: dict) -> dict:
    return build_bundle(
        bundle_id="b_graph", created=CREATED, scenarios=[ctx["scenario"]],
        conditions=ctx["conditions"], tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=trials, aggregates=[], traces=traces,
    )


class TestTrialTraceBinding(unittest.TestCase):
    def setUp(self) -> None:
        self.trials, self.traces, self.ctx = _run()

    def test_honest_bundle_verifies(self) -> None:
        verify_bundle(_bundle(self.trials, self.traces, self.ctx), self.traces)

    def test_trial_citing_trace_from_another_condition_is_rejected(self) -> None:
        # find a completed trial and point it at a trace from the OTHER condition
        trial = next(copy.deepcopy(t) for t in self.trials if t.get("status") == "completed")
        other = next(
            tr for tr in self.traces.values()
            if tr["trial"]["condition_id"] != trial["condition_id"]
        )
        trial["trace_ref"] = content_hash(other)
        bundle = _bundle([trial], {str(other["trace_id"]): other}, self.ctx)
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, {str(other["trace_id"]): other})
        self.assertIn("condition_id", str(ctx.exception))

    def test_one_trace_cannot_back_multiple_trials(self) -> None:
        completed = [copy.deepcopy(t) for t in self.trials if t.get("status") == "completed"]
        one = completed[0]
        # a second trial with a different id reuses the SAME trace_ref → inflated n
        clone = copy.deepcopy(one)
        clone["trial_id"] = one["trial_id"] + "_dup"
        clone["seed"] = "s999"
        one_trace = next(
            tr for tr in self.traces.values() if content_hash(tr) == one["trace_ref"]
        )
        traces = {str(one_trace["trace_id"]): one_trace}
        bundle = _bundle([one, clone], traces, self.ctx)
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, traces)
        self.assertIn("multiple trials", str(ctx.exception))

    def test_orphan_trace_is_rejected(self) -> None:
        # a trace present but cited by no completed trial
        completed = [t for t in self.trials if t.get("status") == "completed"]
        keep = completed[0]
        keep_trace = next(
            tr for tr in self.traces.values() if content_hash(tr) == keep["trace_ref"]
        )
        extra = next(tr for tr in self.traces.values() if tr is not keep_trace)
        traces = {str(keep_trace["trace_id"]): keep_trace, str(extra["trace_id"]): extra}
        bundle = _bundle([keep], traces, self.ctx)
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, traces)
        self.assertIn("orphan", str(ctx.exception))

    def test_duplicate_trial_id_is_rejected(self) -> None:
        completed = [copy.deepcopy(t) for t in self.trials if t.get("status") == "completed"]
        completed[1]["trial_id"] = completed[0]["trial_id"]  # collide ids
        bundle = _bundle(completed, self.traces, self.ctx)
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, self.traces)
        self.assertIn("duplicate trial_id", str(ctx.exception))

    def test_duplicate_trace_id_is_rejected(self) -> None:
        vals = list(self.traces.values())
        collided = copy.deepcopy(vals[1])
        collided["trace_id"] = vals[0]["trace_id"]  # two traces, one id
        traces = {"a": vals[0], "b": collided}
        bundle = _bundle(self.trials, traces, self.ctx)
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, traces)
        self.assertIn("duplicate trace_id", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
