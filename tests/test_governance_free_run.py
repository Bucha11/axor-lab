"""Ungoverned: observed by the kernel, not enforced by it.

Suite Platform RFC §10 makes governance optional. What is optional is
ENFORCEMENT, not observation — condition/v1 says so directly: "off =
observe-only (proxy records, enforces nothing — ungoverned). Observation is
always on regardless."

An earlier version of this file tested the opposite premise: that a
governance-free run resolves no kernel at all. That was wrong, and expensive —
a kernel-free arm has no value ledger (so no EvidenceCase), no recorded
verdicts (so no exact replay), and describes an agent that was never wrapped,
which means turning governance on later is a re-integration rather than
flipping a flag.

There is no kernel-free arm any more. Locally the reference kernel observes,
which is stdlib and always resolvable; a connected runtime carries its own.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests import support
from lab_contracts import build_bundle, validate_artifact, verify_bundle
from lab_contracts.errors import BundleIntegrityError
from lab_runner import run_experiment_suite
from lab_runner.kernel import KernelRegistry
from lab_runner.predicates import TraceView
from lab_runner.runner import (
    REFERENCE_KERNEL_VERSION,
    observe_only_condition,
    run_experiment,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "contracts" / "examples" / "slice-examples.json"


_budget_scenario = support.budget_scenario


def _run(repeats: int = 3):
    scenario, manifests = _budget_scenario()
    result = run_experiment_suite(
        [scenario], manifests, [], KernelRegistry({}), repeats=repeats, run_id="r_obs",
    )
    return scenario, manifests, result


class TestTheDefaultArmIsUngoverned(unittest.TestCase):
    def test_a_conditionless_run_completes(self) -> None:
        _, _, result = _run()
        self.assertEqual(len(result.trials), 3)
        self.assertEqual({str(t["status"]) for t in result.trials}, {"completed"})

    def test_the_synthesized_arm_is_ungoverned_and_carries_a_kernel(self) -> None:
        _, _, result = _run()
        self.assertEqual(len(result.conditions), 1)
        arm = result.conditions[0]
        self.assertEqual(str(arm["id"]), "ungoverned")
        self.assertEqual(str(arm["enforcement"]), "off")
        self.assertIn("kernel", arm)

    def test_the_arm_is_reported_so_the_caller_can_bundle_it(self) -> None:
        """The trials reference the arm by id, so a caller that never sees it
        cannot build a bundle whose trials resolve."""
        _, _, result = _run()
        self.assertEqual(
            {str(t["condition_id"]) for t in result.trials}, {"ungoverned"},
        )

    def test_a_synthesized_arm_names_the_reference_kernel_not_the_installed_one(self) -> None:
        """It has to be resolvable on ANY machine. Pinning `axor-core@X` would
        make the default arm unrunnable wherever that exact build is absent —
        resolve_kernel refuses to substitute under a real-kernel label."""
        self.assertEqual(observe_only_condition()["kernel"], REFERENCE_KERNEL_VERSION)

    def test_an_empty_registry_does_not_break_the_default(self) -> None:
        """The caller passed KernelRegistry({}), which knows nothing. A
        synthesized arm brings a registry that can resolve it rather than
        failing on the caller's."""
        _, _, result = _run()
        self.assertTrue(result.trials)

    def test_run_experiment_defaults_the_same_way(self) -> None:
        scenario, manifests = _budget_scenario()
        result = run_experiment(scenario, manifests, [], KernelRegistry({}), 2, "r_exp")
        self.assertEqual(len(result.trials), 2)
        self.assertEqual(str(result.conditions[0]["id"]), "ungoverned")


class TestObservationIsOnEnforcementIsOff(unittest.TestCase):
    def test_the_kernel_records_verdicts(self) -> None:
        """This is what an ungoverned run buys that a kernel-free one did not:
        a decision per call, replayable."""
        _, _, result = _run()
        for trace in result.traces.values():
            verdicts = [
                str(e["decision"]["verdict"]) for e in trace["events"]  # type: ignore[index,union-attr]
                if e.get("type") == "gate_decision"
            ]
            self.assertTrue(verdicts)
            self.assertNotIn("DENY", verdicts, "enforcement is off — nothing may be denied")

    def test_the_value_ledger_is_populated(self) -> None:
        """No ledger, no EvidenceCase."""
        _, _, result = _run()
        for trace in result.traces.values():
            self.assertTrue(trace["values"])

    def test_the_producer_names_the_kernel_that_observed(self) -> None:
        _, _, result = _run()
        for trace in result.traces.values():
            producer: dict[str, object] = trace["producer"]  # type: ignore[assignment]
            self.assertEqual(producer["kernel_version"], REFERENCE_KERNEL_VERSION)

    def test_predicates_evaluate_over_the_observed_run(self) -> None:
        _, _, result = _run()
        self.assertTrue(all(o.task_success for o in result.outcomes.values()))
        self.assertTrue(all(not o.violation for o in result.outcomes.values()))


class TestGatedTracesStillFailClosed(unittest.TestCase):
    def test_an_intent_with_no_decision_is_not_executed(self) -> None:
        """In a trace that HAD a gate, a decision-less intent is incomplete."""
        trace = {
            "schema_version": "trace/v1", "trace_id": "t", "values": [],
            "trial": {"run_id": "r", "scenario_id": "s", "condition_id": "c",
                      "seed": "s0", "repeat_index": 0},
            "producer": {"mode": "wrapped_code",
                         "provenance_fidelity": "explicit_flow_tracked",
                         "kernel_version": REFERENCE_KERNEL_VERSION},
            "events": [{"seq": 0, "node": "root", "type": "tool_call_intent",
                        "tool": "send_money", "call_id": "c1", "arg_bindings": {}}],
        }
        self.assertTrue(TraceView(trace).gated())
        self.assertEqual(TraceView(trace).executed_tool_calls(), [])

    def test_an_ungated_trace_counts_the_call(self) -> None:
        """Imported traces from a producer with no kernel still evaluate — the
        rule keys off whether a gate was EXPECTED, not whether one is present."""
        trace = {
            "schema_version": "trace/v1", "trace_id": "t", "values": [],
            "trial": {"run_id": "r", "scenario_id": "s", "condition_id": "c",
                      "seed": "s0", "repeat_index": 0},
            "producer": {"mode": "wrapped_code",
                         "provenance_fidelity": "explicit_flow_tracked"},
            "events": [{"seq": 0, "node": "root", "type": "tool_call_intent",
                        "tool": "send_money", "call_id": "c1", "arg_bindings": {}}],
        }
        self.assertFalse(TraceView(trace).gated())
        self.assertEqual(len(TraceView(trace).executed_tool_calls()), 1)


class TestTrialMetrics(unittest.TestCase):
    def test_metrics_are_recorded(self) -> None:
        _, _, result = _run()
        for trial in result.trials:
            metrics: dict[str, object] = trial["metrics"]  # type: ignore[assignment]
            self.assertGreaterEqual(float(metrics["duration_ms"]), 0.0)  # type: ignore[arg-type]
            self.assertGreater(int(metrics["steps"]), 0)  # type: ignore[arg-type]

    def test_unmeasurable_metrics_are_omitted_not_zeroed(self) -> None:
        _, _, result = _run()
        metrics: dict[str, object] = result.trials[0]["metrics"]  # type: ignore[assignment]
        for absent in ("cost_usd", "tokens_in", "tokens_out"):
            self.assertNotIn(absent, metrics)


class TestBundleIntegrity(unittest.TestCase):
    def _bundle(self):
        scenario, manifests, result = _run()
        environment = {k: v for k, v in support.environment().items() if k != "kernel_version"}
        bundle = build_bundle(
            bundle_id="b_obs", created="2026-08-03T00:00:00+00:00",
            scenarios=[scenario], conditions=result.conditions,
            tool_manifests=list(manifests.values()), environment=environment,
            trials=result.trials, aggregates=[], traces=result.traces,
        )
        return bundle, {str(t["trace_id"]): t for t in result.traces.values()}

    def test_the_bundle_is_valid_and_verifies(self) -> None:
        bundle, traces = self._bundle()
        self.assertEqual(validate_artifact(bundle, "bundle"), [])
        verify_bundle(bundle, traces)

    def test_half_declared_kernel_is_refused(self) -> None:
        """condition.kernel and producer.kernel_version must agree."""
        bundle, traces = self._bundle()
        del bundle["conditions"][0]["kernel"]  # type: ignore[index]
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, traces)
        self.assertIn("kernel presence disagrees", str(ctx.exception))

    def test_a_governed_trial_must_carry_its_config_hash(self) -> None:
        scenario = support.banking_scenario()
        conditions = support.conditions()
        result = run_experiment_suite(
            [scenario], support.manifests(), conditions, support.kernel_registry(),
            repeats=2, run_id="r_gov",
        )
        for trial in result.trials:
            trial.pop("runtime_config_hash", None)
            trial.pop("runtime_provenance", None)
            trial.pop("config_compiler_version", None)
        bundle = build_bundle(
            bundle_id="b_gov", created="2026-08-03T00:00:00+00:00",
            scenarios=[scenario], conditions=conditions,
            tool_manifests=list(support.manifests().values()),
            environment=support.environment(), trials=result.trials,
            aggregates=[], traces=result.traces,
        )
        traces = {str(t["trace_id"]): t for t in result.traces.values()}
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, traces)
        self.assertIn("runtime_config_hash", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
