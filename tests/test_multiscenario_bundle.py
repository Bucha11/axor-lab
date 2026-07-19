"""Multi-scenario benchmark bundle roundtrip (review round 2, Patch 1).

The P0 defect this guards: trace identity omitted scenario_id, so every
scenario that shared a (condition, seed) collided — the colliding traces
overwrote each other in the bundle manifest and on disk, and a
build → write → read → verify roundtrip silently lost trials. The
single-scenario CLI e2e could never surface it (one scenario ⇒ no collision).

This exercises the full benchmark path end to end and asserts the trial and
trace counts survive a real disk roundtrip.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_adapters import import_suite, manifests
from lab_contracts import build_bundle
from lab_runner import default_registry, run_experiment_suite
from lab_runner.bundle_io import read_bundle_dir, write_bundle_dir
from lab_runner.errors import RunnerError
from lab_runner.replay import replay_bundle

REPEATS = 6
CREATED = "2026-07-19T12:00:00+00:00"


class TestMultiScenarioBundleRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenarios = import_suite("banking")
        cls.mans = manifests()
        cls.conditions = support.conditions()
        cls.registry = default_registry(tuple(str(c["kernel"]) for c in cls.conditions))
        cls.expected = len(cls.scenarios) * len(cls.conditions) * REPEATS
        cls.result = run_experiment_suite(
            cls.scenarios, cls.mans, cls.conditions, cls.registry,
            repeats=REPEATS, run_id="r_multi",
        )

    def test_the_suite_is_actually_multi_scenario(self) -> None:
        self.assertGreaterEqual(len(self.scenarios), 3)

    def test_every_trial_completed_and_every_trace_is_distinct(self) -> None:
        completed = [t for t in self.result.trials if t["status"] == "completed"]
        self.assertEqual(len(completed), self.expected)
        # content-hash-keyed store: one entry per distinct trace
        self.assertEqual(len(self.result.traces), self.expected)
        trace_ids = {str(t["trace_id"]) for t in self.result.traces.values()}
        self.assertEqual(len(trace_ids), self.expected)  # no trace_id collisions

    def _bundle(self) -> dict[str, object]:
        return build_bundle(
            bundle_id="b_multi", created=CREATED, scenarios=self.scenarios,
            conditions=self.conditions, tool_manifests=list(self.mans.values()),
            environment=support.environment(), trials=self.result.trials,
            aggregates=[], traces=self.result.traces,
        )

    def test_bundle_manifest_has_one_hash_per_trace(self) -> None:
        bundle = self._bundle()
        hashes: dict[str, str] = bundle["content_hashes"]  # type: ignore[assignment]
        trace_hashes = [k for k in hashes if k.startswith("trace:")]
        self.assertEqual(len(trace_hashes), self.expected)

    def test_roundtrip_preserves_all_trials_and_traces_and_replays(self) -> None:
        bundle = self._bundle()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            write_bundle_dir(out, bundle, self.result.traces)

            # every trace made it to disk as its own file (no filename collisions)
            files = list((out / "traces").glob("*.json"))
            self.assertEqual(len(files), self.expected)

            # read + hash-verify (read_bundle_dir raises on any integrity gap)
            loaded_bundle, loaded_traces = read_bundle_dir(out)
            self.assertEqual(len(loaded_traces), self.expected)
            completed = [t for t in loaded_bundle["trials"] if t["status"] == "completed"]  # type: ignore[union-attr]
            self.assertEqual(len(completed), self.expected)

            # and the roundtripped bundle still replays bit-identically
            kernels = {k.version: k for k in self.registry.kernels}
            report = replay_bundle(loaded_bundle, loaded_traces, kernels)
            self.assertTrue(report.bit_identical)

    def test_write_refuses_nonempty_dir_then_overwrite_cleans_stale(self) -> None:
        bundle = self._bundle()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            write_bundle_dir(out, bundle, self.result.traces)
            # a second write without overwrite must refuse (never merge silently)
            with self.assertRaises(RunnerError):
                write_bundle_dir(out, bundle, self.result.traces)
            # overwrite with a strict subset of traces: stale files must be gone,
            # not left behind to be republished as someone else's artifact
            one_id, one_trace = next(iter(self.result.traces.items()))
            subset = {one_id: one_trace}
            subset_trials = [
                t for t in self.result.trials
                if t.get("trace_ref") == one_id
            ]
            small = build_bundle(
                bundle_id="b_small", created=CREATED, scenarios=self.scenarios,
                conditions=self.conditions, tool_manifests=list(self.mans.values()),
                environment=support.environment(), trials=subset_trials,
                aggregates=[], traces=subset,
            )
            write_bundle_dir(out, small, subset, overwrite=True)
            files = list((out / "traces").glob("*.json"))
            self.assertEqual(len(files), 1)  # no stale traces linger


if __name__ == "__main__":
    unittest.main()
