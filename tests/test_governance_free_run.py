"""The platform slice: a run with NO governance at all.

Suite Platform RFC §10 — governance is a capability an experiment opts into, not
a stage every run passes through. This is the workflow the repo could not
express before: bring an agent, run a suite, observe what happened.

The load-bearing invariant is that a gate-free run is PROVABLY gate-free rather
than a governed run that happened to allow everything: no kernel is resolved, no
gate_decision is emitted, no kernel_version is claimed, and `verify_bundle`
checks those three absences agree with each other.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests import support
from lab_contracts import build_bundle, validate_artifact, verify_bundle
from lab_contracts.bundle import config_provenance
from lab_contracts.errors import BundleIntegrityError
from lab_runner import run_experiment_suite
from lab_runner.kernel import KernelRegistry
from lab_runner.predicates import TraceView
from lab_runner.runner import run_experiment, unwrapped_condition

EXAMPLES = Path(__file__).resolve().parent.parent / "contracts" / "examples" / "slice-examples.json"


def _budget_scenario() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """A scenario with no attack model, driven through the runner's tool shape."""
    examples = json.loads(EXAMPLES.read_text())
    scenario = copy.deepcopy(examples["scenario_budget_no_injection"][1])
    read = examples["tool_read_txns"][1]
    sink = next(m for m in support.manifests().values() if m.get("side_effecting"))
    manifests = {str(read["id"]): read, str(sink["id"]): sink}
    scenario["tools"] = [{"$ref": str(read["id"])}, {"$ref": str(sink["id"])}]
    scenario["inputs"] = {"landlord_iban": "GB29NWBK60161331926819"}
    scenario["task_success"] = {"event": "tool_call", "tool": str(sink["id"])}
    return scenario, manifests


def _run(repeats: int = 3):
    scenario, manifests = _budget_scenario()
    result = run_experiment_suite(
        [scenario], manifests, [], KernelRegistry({}), repeats=repeats, run_id="r_obs",
    )
    return scenario, manifests, result


class TestGovernanceFreeExecution(unittest.TestCase):
    def test_run_completes_with_no_conditions(self) -> None:
        _, _, result = _run()
        self.assertEqual(len(result.trials), 3)
        self.assertEqual({str(t["status"]) for t in result.trials}, {"completed"})

    def test_the_synthesized_arm_is_reported_to_the_caller(self) -> None:
        """The runner supplies the observe-only condition, and the trials
        reference it by id — so the caller needs it back to build a bundle whose
        trials resolve. Leaving it internal made valid runs unbundleable."""
        _, _, result = _run()
        self.assertEqual(len(result.conditions), 1)
        self.assertEqual(str(result.conditions[0]["id"]), "unwrapped")
        self.assertEqual(
            {str(t["condition_id"]) for t in result.trials}, {"unwrapped"},
        )

    def test_no_kernel_is_named_anywhere(self) -> None:
        _, _, result = _run()
        self.assertNotIn("kernel", unwrapped_condition())
        for trace in result.traces.values():
            producer: dict[str, object] = trace["producer"]  # type: ignore[assignment]
            self.assertNotIn("kernel_version", producer)

    def test_no_gate_decision_is_emitted(self) -> None:
        _, _, result = _run()
        for trace in result.traces.values():
            decisions = [
                e for e in trace["events"]  # type: ignore[union-attr]
                if e.get("type") == "gate_decision"
            ]
            self.assertEqual(decisions, [])

    def test_no_kernel_registry_is_consulted(self) -> None:
        """A governance-free run must not depend on a kernel registry resolving
        anything — the registry here is empty and would raise if touched."""
        scenario, manifests = _budget_scenario()

        class Exploding(KernelRegistry):
            def resolve(self, *a: object, **k: object) -> object:  # noqa: ANN401
                raise AssertionError("a governance-free run resolved a kernel")

        result = run_experiment_suite(
            [scenario], manifests, [], Exploding({}), repeats=2, run_id="r_nok",
        )
        self.assertEqual(len(result.trials), 2)

    def test_run_experiment_also_defaults_to_unwrapped(self) -> None:
        scenario, manifests = _budget_scenario()
        result = run_experiment(scenario, manifests, [], KernelRegistry({}), 2, "r_exp")
        self.assertEqual(len(result.trials), 2)
        self.assertEqual(str(result.conditions[0]["id"]), "unwrapped")


class TestPredicatesWithoutAGate(unittest.TestCase):
    def test_task_success_is_evaluated(self) -> None:
        """The regression this guards: `executed_tool_calls` required an
        explicit ALLOW, so with no gate there were no executed calls and EVERY
        predicate silently evaluated False — a perfectly successful ungoverned
        run would report 0% task success."""
        _, _, result = _run()
        self.assertTrue(all(o.task_success for o in result.outcomes.values()))

    def test_violation_is_false_not_unevaluable(self) -> None:
        _, _, result = _run()
        self.assertTrue(all(not o.violation for o in result.outcomes.values()))

    def test_a_governed_trace_still_fails_closed_on_a_missing_decision(self) -> None:
        """The fail-open guard must survive: in a trace that DID have a gate, an
        intent with no decision is incomplete, not executed."""
        trace = {
            "schema_version": "trace/v1", "trace_id": "t", "values": [],
            "trial": {"run_id": "r", "scenario_id": "s", "condition_id": "c",
                      "seed": "s0", "repeat_index": 0},
            "producer": {"mode": "wrapped_code",
                         "provenance_fidelity": "explicit_flow_tracked",
                         "kernel_version": "reference_taint_floor_kernel"},
            "events": [{"seq": 0, "node": "root", "type": "tool_call_intent",
                        "tool": "send_money", "call_id": "c1", "arg_bindings": {}}],
        }
        self.assertTrue(TraceView(trace).gated())
        self.assertEqual(TraceView(trace).executed_tool_calls(), [])

    def test_the_same_trace_without_a_kernel_counts_the_call(self) -> None:
        trace = {
            "schema_version": "trace/v1", "trace_id": "t", "values": [],
            "trial": {"run_id": "r", "scenario_id": "s", "condition_id": "unwrapped",
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
            self.assertGreater(int(metrics["tool_calls"]), 0)  # type: ignore[arg-type]

    def test_unmeasurable_metrics_are_omitted_not_zeroed(self) -> None:
        """A scripted agent spends nothing and calls no provider. Reporting
        cost_usd: 0 would let a budget invariant pass on a run whose cost was
        never measured at all."""
        _, _, result = _run()
        metrics: dict[str, object] = result.trials[0]["metrics"]  # type: ignore[assignment]
        for absent in ("cost_usd", "tokens_in", "tokens_out"):
            self.assertNotIn(absent, metrics)


class TestBundleIntegrity(unittest.TestCase):
    def _bundle(self):
        scenario, manifests, result = _run()
        environment = {k: v for k, v in support.environment().items() if k != "kernel_version"}
        bundle = build_bundle(
            bundle_id="b_obs", created="2026-08-02T00:00:00+00:00",
            scenarios=[scenario], conditions=result.conditions,
            tool_manifests=list(manifests.values()), environment=environment,
            trials=result.trials, aggregates=[], traces=result.traces,
        )
        traces = {str(t["trace_id"]): t for t in result.traces.values()}
        return bundle, traces

    def test_bundle_is_schema_valid_and_verifies(self) -> None:
        bundle, traces = self._bundle()
        self.assertEqual(validate_artifact(bundle, "bundle"), [])
        verify_bundle(bundle, traces)

    def test_no_config_provenance_block_is_emitted(self) -> None:
        """There is no governor config, so there is no provenance about one. An
        empty `reconstructed_legacy` block would read as a governed run that
        lost its provenance."""
        bundle, _ = self._bundle()
        self.assertNotIn("config_provenance", bundle["environment"])  # type: ignore[operator]
        self.assertIsNone(config_provenance([], [], [], []))

    def test_a_verdict_with_no_kernel_is_refused(self) -> None:
        """The absence must be trustworthy: a trace carrying verdicts while
        claiming no kernel produced them is unreplayable and unfalsifiable.

        Built CONSISTENTLY — the decision is added before the bundle is
        assembled, so every content hash matches. Tampering after the fact only
        proves the hash check works; this proves the kernel-binding check does.
        """
        scenario, manifests, result = _run(repeats=1)
        trace = next(iter(result.traces.values()))
        trace["events"].append({  # type: ignore[union-attr]
            "seq": 99, "node": "root", "type": "gate_decision", "call_id": "c1",
            "decision": {"verdict": "DENY", "gate": "taint_floor",
                         "driving_value_id": None,
                         "driving_unresolved": {"kind": "no_driving_args"},
                         "projection": "untrusted-derived"},
        })
        from lab_contracts import content_hash
        new_ref = content_hash(trace)
        result.trials[0]["trace_ref"] = new_ref
        environment = {k: v for k, v in support.environment().items() if k != "kernel_version"}
        bundle = build_bundle(
            bundle_id="b_liar", created="2026-08-02T00:00:00+00:00",
            scenarios=[scenario], conditions=result.conditions,
            tool_manifests=list(manifests.values()), environment=environment,
            trials=result.trials, aggregates=[], traces={new_ref: trace},
        )
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, {str(trace["trace_id"]): trace})
        self.assertIn("no attributable kernel", str(ctx.exception))

    def test_half_declared_kernel_is_refused(self) -> None:
        """condition.kernel and producer.kernel_version must agree about
        presence — a gate-free run omits BOTH."""
        bundle, traces = self._bundle()
        bundle["conditions"][0]["kernel"] = "reference_taint_floor_kernel"  # type: ignore[index]
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, traces)
        self.assertIn("kernel presence disagrees", str(ctx.exception))

    def test_a_governed_trial_must_still_carry_its_config_hash(self) -> None:
        """Relaxing the schema must not let a GOVERNED trial drop its runtime
        config: the rule moved into verify_bundle, where it keys off the trial's
        actual condition instead of a sibling field."""
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
            bundle_id="b_gov", created="2026-08-02T00:00:00+00:00",
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
