"""verify_bundle binds load-bearing trace metadata to the bundle (review r8).

The trace schema calls producer.kernel_version and inputs_digest load-bearing —
they name the exact world a trace was produced in — but the graph verifier only
checked trial coordinates. So a trace could claim one producer kernel or inputs
digest while sitting in a bundle whose condition/scenario say another, and replay
(which reads the bundle's kernel + inputs) would still reproduce it, leaving the
provenance description unbacked. verify_bundle now rejects those mismatches.
"""

from __future__ import annotations

import copy
import unittest

from tests import support
from lab_contracts import build_bundle, content_hash, verify_bundle
from lab_contracts.errors import BundleIntegrityError
from lab_runner import run_experiment_suite

CREATED = "2026-07-19T12:00:00+00:00"


class TestTraceMetadataBinding(unittest.TestCase):
    def _result(self):
        return run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=2, run_id="r_tmb",
        )

    def _bundle(self, trials, traces, environment=None):
        return build_bundle(
            bundle_id="b_tmb", created=CREATED, scenarios=[support.banking_scenario()],
            conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
            environment=environment or support.environment(), trials=trials, aggregates=[],
            traces=traces,
        )

    def test_honest_bundle_binds_cleanly(self) -> None:
        result = self._result()
        traces = {str(t["trace_id"]): t for t in result.traces.values()}
        verify_bundle(self._bundle(result.trials, result.traces), traces)  # no raise

    def _one_completed(self, result):
        by_ref = {content_hash(t): t for t in result.traces.values()}
        trial = next(t for t in result.trials if t.get("status") == "completed")
        return trial, by_ref[str(trial["trace_ref"])]

    def test_forged_producer_kernel_version_is_rejected(self) -> None:
        result = self._result()
        trial, trace = self._one_completed(result)
        forged = copy.deepcopy(trace)
        forged["producer"]["kernel_version"] = "forged-kernel@9.9"
        new_ref = content_hash(forged)
        trials = [{**t, "trace_ref": new_ref} if t is trial else t for t in result.trials]
        traces = dict(result.traces)
        del traces[str(trial["trace_ref"])]
        traces[new_ref] = forged
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(self._bundle(trials, traces),
                          {str(t["trace_id"]): t for t in traces.values()})
        self.assertIn("kernel_version", str(ctx.exception))

    def test_forged_inputs_digest_is_rejected(self) -> None:
        result = self._result()
        trial, trace = self._one_completed(result)
        forged = copy.deepcopy(trace)
        forged["inputs_digest"] = content_hash({"inputs": {"tampered": True}})
        new_ref = content_hash(forged)
        trials = [{**t, "trace_ref": new_ref} if t is trial else t for t in result.trials]
        traces = dict(result.traces)
        del traces[str(trial["trace_ref"])]
        traces[new_ref] = forged
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(self._bundle(trials, traces),
                          {str(t["trace_id"]): t for t in traces.values()})
        self.assertIn("inputs_digest", str(ctx.exception))

    def test_environment_kernel_must_be_a_condition_kernel(self) -> None:
        result = self._result()
        traces = {str(t["trace_id"]): t for t in result.traces.values()}
        bad_env = {**support.environment(), "kernel_version": "some-other-kernel@1.0"}
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(self._bundle(result.trials, result.traces, environment=bad_env), traces)
        self.assertIn("environment.kernel_version", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
