"""Acceptance test 8 — the bundle round-trips.

bundle/v1 → serialize → re-load → content hashes verify → replay reproduces
the same verdicts. A tampered trace fails verification loudly.
"""

from __future__ import annotations

import json
import unittest

from tests import support
from lab_ref import (
    BundleIntegrityError,
    build_bundle,
    replay_bundle,
    run_experiment,
    verify_bundle,
)

REPEATS = 10
CREATED = "2026-07-19T00:00:00Z"


def _run_and_bundle() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    scenario = support.banking_scenario()
    result = run_experiment(
        scenario, support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=REPEATS, run_id="r_rt",
    )
    bundle = build_bundle(
        bundle_id="b_rt", created=CREATED, scenarios=[scenario],
        conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials, aggregates=[],
        traces=result.traces,
    )
    return bundle, result.traces


class TestBundleRoundTrip(unittest.TestCase):
    def test_roundtrip_verifies_and_replays_identically(self) -> None:
        bundle, traces = _run_and_bundle()
        kernels = {k.version: k for k in support.kernel_registry().kernels}
        before = replay_bundle(bundle, traces, kernels)

        # round-trip through JSON, as publish/download would
        reloaded_bundle = json.loads(json.dumps(bundle))
        reloaded_traces = json.loads(json.dumps(traces))

        verify_bundle(reloaded_bundle, reloaded_traces)
        after = replay_bundle(reloaded_bundle, reloaded_traces, kernels)
        self.assertTrue(after.bit_identical)
        self.assertEqual(before.canonical(), after.canonical())

    def test_bundle_is_schema_valid(self) -> None:
        bundle, _ = _run_and_bundle()
        self.assertEqual(support.schema_errors(bundle, "bundle"), [])

    def test_tampered_trace_fails_verification(self) -> None:
        bundle, traces = _run_and_bundle()
        tampered = json.loads(json.dumps(traces))
        victim = next(iter(tampered.values()))
        for event in victim["events"]:
            if event.get("type") == "gate_decision":
                recorded = event["decision"]["verdict"]
                event["decision"]["verdict"] = "ALLOW" if recorded == "DENY" else "DENY"
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, tampered)
        self.assertIn("hash mismatch", str(ctx.exception).lower())

    def test_missing_trace_fails_verification(self) -> None:
        bundle, traces = _run_and_bundle()
        partial = dict(traces)
        partial.pop(next(iter(partial)))
        with self.assertRaises(BundleIntegrityError):
            verify_bundle(bundle, partial)

    def test_kernel_version_is_pinned_in_the_bundle(self) -> None:
        bundle, _ = _run_and_bundle()
        self.assertEqual(bundle["environment"]["kernel_version"], support.KERNEL_PINNED)  # type: ignore[index]
        for condition in bundle["conditions"]:  # type: ignore[union-attr]
            self.assertEqual(condition["kernel"], support.KERNEL_PINNED)
            self.assertTrue(str(condition["config_hash"]).startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
