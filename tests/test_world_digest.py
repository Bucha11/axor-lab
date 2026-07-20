"""One world_digest across runner, gateway and instrumented SDK (review r9, P1).

The r8 metadata-binding verifier expected inputs_digest = hash(inputs+fixtures)
(the runner's formula), but the two endpoint paths hashed inputs only. For a
scenario with non-empty fixtures — like the banking slice — a conformant
instrumented trace then FAILED verify_bundle. And a producer could drop
inputs_digest entirely to dodge the binding, because it was only checked when
present. Now every producer uses world_digest(inputs, fixtures) and the field is
required for wrapped_code / instrumented_endpoint traces.
"""

from __future__ import annotations

import copy
import unittest

from tests import support
from lab_contracts import build_bundle, content_hash, verify_bundle, world_digest
from lab_contracts.errors import BundleIntegrityError
from lab_endpoint import EmittedEvent, assemble_and_gate
from lab_runner import run_experiment_suite

CREATED = "2026-07-19T12:00:00+00:00"


class TestWorldDigest(unittest.TestCase):
    def test_world_digest_binds_inputs_and_fixtures(self) -> None:
        inputs, fixtures = {"a": 1}, {"read_txns": {"x": 2}}
        self.assertEqual(world_digest(inputs, fixtures),
                         content_hash({"inputs": inputs, "fixtures": fixtures}))
        # a scenario WITH fixtures differs from the old inputs-only digest
        self.assertNotEqual(world_digest(inputs, fixtures), content_hash({"inputs": inputs}))

    def test_runner_trace_uses_world_digest(self) -> None:
        scenario = support.banking_scenario()
        self.assertTrue(scenario.get("fixtures"))  # the banking slice HAS fixtures
        result = run_experiment_suite(
            [scenario], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=1, run_id="r_wd",
        )
        trace = next(iter(result.traces.values()))
        self.assertEqual(trace["inputs_digest"],
                         world_digest(scenario["inputs"], scenario.get("fixtures", {})))

    def test_instrumented_trace_with_fixtures_binds_in_bundle(self) -> None:
        # the exact regression: an instrumented trace for a fixture-bearing
        # scenario must verify inside a bundle
        scenario = support.banking_scenario()
        cond = support.conditions()[1]
        emitted = [
            EmittedEvent(type="tool_result", tool="read_txns", values=[{
                "value_id": "v_r", "decision_value": support.ATTACKER_IBAN,
                "preview": support.ATTACKER_IBAN, "labels": ["untrusted_derived"],
                "sources": [{"kind": "external_read", "origin_ref": "o"}]}]),
            EmittedEvent(type="tool_call_intent", tool="send_money",
                         arg_bindings={"recipient": "v_r"},
                         args={"recipient": support.ATTACKER_IBAN, "amount": 1200}),
        ]
        trace = assemble_and_gate(
            emitted, cond, support.manifests(), scenario["inputs"],
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="r_wd", scenario_id="banking-exfil-01",
            fixtures=scenario.get("fixtures", {}), trusted_runtime=True,
        )
        trial = {"trial_id": "t1", "scenario_id": "banking-exfil-01", "condition_id": str(cond["id"]),
                 "seed": "s000", "repeat_index": 0, "status": "completed",
                 "trace_ref": content_hash(trace)}
        bundle = build_bundle(
            bundle_id="b_wd", created=CREATED, scenarios=[scenario], conditions=[cond],
            tool_manifests=list(support.manifests().values()), environment=support.environment(),
            trials=[trial], aggregates=[], traces={str(trace["trace_id"]): trace},
        )
        verify_bundle(bundle, {str(trace["trace_id"]): trace})  # must not raise

    def test_missing_inputs_digest_is_rejected_for_instrumented(self) -> None:
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=1, run_id="r_wd2",
        )
        trial = next(t for t in result.trials if t.get("status") == "completed")
        trace = {content_hash(t): t for t in result.traces.values()}[str(trial["trace_ref"])]
        stripped = copy.deepcopy(trace)
        del stripped["inputs_digest"]  # a producer trying to dodge the binding
        stripped["producer"]["mode"] = "wrapped_code"
        new_ref = content_hash(stripped)
        trials = [{**t, "trace_ref": new_ref} if t is trial else t for t in result.trials]
        traces = {new_ref: stripped}
        bundle = build_bundle(
            bundle_id="b_wd3", created=CREATED, scenarios=[support.banking_scenario()],
            conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
            environment=support.environment(), trials=trials, aggregates=[], traces=traces,
        )
        with self.assertRaises(BundleIntegrityError) as ctx:
            verify_bundle(bundle, {str(stripped["trace_id"]): stripped})
        self.assertIn("requires an inputs_digest", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
