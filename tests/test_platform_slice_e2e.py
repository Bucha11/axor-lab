"""The platform slice, end to end — Phase 2's exit criterion.

    Suite scenario (no attack model)
      -> Run with NO conditions
      -> Trials carrying metrics
      -> verified bundle
      -> artifact/v1
      -> a `latency < threshold` invariant that passes, and fails when it should

This is the workflow the RFC calls primary (§15) and the one the repo could not
execute at all before Phase 2. Governance appears nowhere in it.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests import support
from lab_contracts import (
    build_artifact,
    build_bundle,
    content_hash,
    reproducibility_of,
    validate_artifact,
    verify_bundle,
)
from lab_runner import run_experiment_suite
from lab_runner.invariants import STATUS_ERROR, STATUS_FAILED, STATUS_PASSED, check_invariant
from lab_runner.kernel import KernelRegistry

EXAMPLES = Path(__file__).resolve().parent.parent / "contracts" / "examples" / "slice-examples.json"
CREATED = "2026-08-02T00:00:00+00:00"


class TestPlatformSliceEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        examples = json.loads(EXAMPLES.read_text())
        scenario = copy.deepcopy(examples["scenario_budget_no_injection"][1])
        read = examples["tool_read_txns"][1]
        sink = next(m for m in support.manifests().values() if m.get("side_effecting"))
        self.manifests = {str(read["id"]): read, str(sink["id"]): sink}
        scenario["tools"] = [{"$ref": str(read["id"])}, {"$ref": str(sink["id"])}]
        scenario["inputs"] = {"landlord_iban": "GB29NWBK60161331926819"}
        scenario["task_success"] = {"event": "tool_call", "tool": str(sink["id"])}
        self.scenario = scenario
        self.suite = copy.deepcopy(examples["suite_budget"][1])

        self.result = run_experiment_suite(
            [scenario], self.manifests, [], KernelRegistry({}), repeats=5, run_id="r_e2e",
        )
        environment = {k: v for k, v in support.environment().items() if k != "kernel_version"}
        self.bundle = build_bundle(
            bundle_id="b_e2e", created=CREATED, scenarios=[scenario],
            conditions=self.result.conditions,
            tool_manifests=list(self.manifests.values()), environment=environment,
            trials=self.result.trials, aggregates=[], traces=self.result.traces,
        )
        self.traces = {str(t["trace_id"]): t for t in self.result.traces.values()}

    def test_the_run_produces_a_verified_bundle(self) -> None:
        self.assertEqual(validate_artifact(self.bundle, "bundle"), [])
        verify_bundle(self.bundle, self.traces)
        self.assertEqual(len(self.result.trials), 5)

    def test_every_trial_carries_metrics(self) -> None:
        for trial in self.bundle["trials"]:  # type: ignore[union-attr]
            self.assertIn("metrics", trial)
            self.assertIn("duration_ms", trial["metrics"])

    def test_it_packages_as_an_artifact(self) -> None:
        artifact = build_artifact(
            artifact_id="art_e2e", created=CREATED, bundle=self.bundle, suite=self.suite,
            agent_identity={"agent_ref": "scripted@0.6", "determinism": "deterministic"},
            evidence_cases=[], regressions=[],
            reproduce={"command": "axor-lab run budget.axl --out ./bundle",
                       "requires": [],
                       "reproducibility": reproducibility_of(self.bundle, True)},
        )
        self.assertEqual(validate_artifact(artifact, "artifact"), [])
        self.assertEqual(content_hash(artifact["bundle"]), content_hash(self.bundle))

    def test_an_observed_ungoverned_artifact_claims_exact_replay(self) -> None:
        """The ungoverned arm still ran THROUGH the kernel, so its recorded
        verdicts recompute bit-identically. An artifact with no traces at all
        still claims nothing."""
        self.assertEqual(reproducibility_of(self.bundle, True), "exact_replay")
        self.assertEqual(reproducibility_of(self.bundle, False), "not_reproducible")

    def test_latency_invariant_passes(self) -> None:
        regression = {
            "schema_version": "regression/v1", "id": "RG-42",
            "name": "Trial latency stays under 10s",
            "rule": {"kind": "metric_threshold", "metric": "duration_ms",
                     "aggregate": "p95", "op": "lt", "value": 10000},
            "expectation": "p95 trial latency must stay below 10s.",
        }
        result = check_invariant(regression, self.bundle["trials"])  # type: ignore[arg-type]
        self.assertEqual(result.status, STATUS_PASSED, result.detail)

    def test_latency_invariant_fails_on_a_regressed_run(self) -> None:
        """The same invariant, the same shape of run, a slower result."""
        regression = {
            "schema_version": "regression/v1", "id": "RG-42", "name": "under 10s",
            "rule": {"kind": "metric_threshold", "metric": "duration_ms",
                     "aggregate": "max", "op": "lt", "value": 10000},
        }
        trials = copy.deepcopy(list(self.bundle["trials"]))  # type: ignore[arg-type]
        trials[2]["metrics"]["duration_ms"] = 18400
        result = check_invariant(regression, trials)
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.observed, 18400.0)

    def test_the_invariant_history_round_trips_into_the_artifact(self) -> None:
        regression = {
            "schema_version": "regression/v1", "id": "RG-42", "name": "under 10s",
            "rule": {"kind": "metric_threshold", "metric": "duration_ms",
                     "op": "lt", "value": 10000},
        }
        result = check_invariant(regression, self.bundle["trials"])  # type: ignore[arg-type]
        regression["history"] = [result.as_history_entry("r_e2e", CREATED)]
        artifact = build_artifact(
            artifact_id="art_e2e", created=CREATED, bundle=self.bundle,
            regressions=[regression],
        )
        self.assertEqual(validate_artifact(artifact, "artifact"), [])
        self.assertEqual(
            artifact["regressions"][0]["history"][0]["status"], STATUS_PASSED,  # type: ignore[index]
        )

    def test_a_cost_invariant_errors_because_nothing_measured_cost(self) -> None:
        """The scripted agent spends nothing measurable, so cost_usd is absent.
        A budget invariant must report that it could not be evaluated rather
        than pass on a run whose cost was never observed."""
        regression = {
            "schema_version": "regression/v1", "id": "RG-budget", "name": "under $1",
            "rule": {"kind": "metric_threshold", "metric": "cost_usd",
                     "op": "lte", "value": 1.0},
        }
        result = check_invariant(regression, self.bundle["trials"])  # type: ignore[arg-type]
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("cost_usd", result.detail)


class TestGovernanceSliceStillWorks(unittest.TestCase):
    """The governance capability must be untouched by all of this."""

    def test_a_governed_run_still_gates_and_still_claims_exact_replay(self) -> None:
        scenario = support.banking_scenario()
        conditions = support.conditions()
        result = run_experiment_suite(
            [scenario], support.manifests(), conditions, support.kernel_registry(),
            repeats=4, run_id="r_gov",
        )
        bundle = build_bundle(
            bundle_id="b_gov", created=CREATED, scenarios=[scenario], conditions=conditions,
            tool_manifests=list(support.manifests().values()),
            environment=support.environment(), trials=result.trials,
            aggregates=[], traces=result.traces,
        )
        verify_bundle(bundle, {str(t["trace_id"]): t for t in result.traces.values()})
        self.assertEqual(reproducibility_of(bundle, True), "exact_replay")
        verdicts = [
            e for trace in result.traces.values()
            for e in trace["events"]  # type: ignore[union-attr]
            if e.get("type") == "gate_decision"
        ]
        self.assertTrue(verdicts, "a governed run must still emit gate decisions")
        self.assertIn(
            "config_provenance", bundle["environment"],  # type: ignore[operator]
        )


if __name__ == "__main__":
    unittest.main()
