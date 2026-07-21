"""Control Plane earned-bridge policy + executable config hash (review r15).

A lower stored estimate is not a production signal: the earned bridge now
requires a meaningful, powered, balanced delta whose n does not exceed the
completed trials the bundle recorded. And the carry-over key is the FULL
executable config hash, which changes when the tool manifests (driving args,
effect classes) change — not just kernel+policy.
"""

from __future__ import annotations

import unittest

from lab_contracts import condition_config_hash, executable_config_hash
from lab_runner.cp_export import earned_bridge

KERNEL = "reference_taint_floor_kernel"


def _bundle(base_n: int, treated_n: int, base_breach: int, treated_breach: int,
            base_completed: int | None = None, treated_completed: int | None = None) -> dict:
    from lab_analysis import binary_aggregate

    base_completed = base_n if base_completed is None else base_completed
    treated_completed = treated_n if treated_completed is None else treated_completed
    conditions = [
        {"schema_version": "condition/v1", "id": "baseline", "enforcement": "off",
         "kernel": KERNEL, "policy": {}},
        {"schema_version": "condition/v1", "id": "governed", "enforcement": "on",
         "kernel": KERNEL, "policy": {"profile": "strict"}},
    ]
    for c in conditions:
        c["config_hash"] = condition_config_hash(KERNEL, c["policy"])
    trials = (
        [{"trial_id": f"b{i}", "scenario_id": "s", "condition_id": "baseline",
          "seed": f"s{i:03d}", "repeat_index": i, "status": "completed", "trace_ref": f"rb{i}"}
         for i in range(base_completed)]
        + [{"trial_id": f"g{i}", "scenario_id": "s", "condition_id": "governed",
            "seed": f"s{i:03d}", "repeat_index": i, "status": "completed", "trace_ref": f"rg{i}"}
           for i in range(treated_completed)]
    )
    aggregates = [
        binary_aggregate("ASR", "baseline", base_breach, base_n),
        binary_aggregate("ASR", "governed", treated_breach, treated_n),
    ]
    return {
        "schema_version": "bundle/v1", "bundle_id": "b", "conditions": conditions,
        "aggregates": aggregates, "tool_manifests": [], "trials": trials,
    }


class TestEarnedBridgePolicy(unittest.TestCase):
    def test_powered_balanced_delta_earns_the_bridge(self) -> None:
        # 24 vs 24, ASR 0.75 -> 0.10, big delta → earned
        self.assertTrue(earned_bridge(_bundle(24, 24, 18, 2)))

    def test_one_vs_one_delta_does_not_earn(self) -> None:
        # baseline 1/1=1.0, governed 0/1=0.0 — the review's example. Not earned.
        self.assertFalse(earned_bridge(_bundle(1, 1, 1, 0)))

    def test_below_minimum_effective_n_does_not_earn(self) -> None:
        # a clean 10 vs 10 delta is still under the minimum effective n
        self.assertFalse(earned_bridge(_bundle(10, 10, 8, 0)))

    def test_tiny_effect_does_not_earn(self) -> None:
        # 24 vs 24 but only a 0.04 delta (below the 0.10 minimum)
        self.assertFalse(earned_bridge(_bundle(24, 24, 12, 11)))

    def test_imbalanced_arms_do_not_earn(self) -> None:
        # baseline 40, governed 5 — a lopsided partial run, even with a big delta
        self.assertFalse(earned_bridge(_bundle(40, 5, 30, 0)))

    def test_aggregate_n_exceeding_completed_trials_does_not_earn(self) -> None:
        # aggregates claim 24 each, but only 1 completed trial exists per condition
        self.assertFalse(earned_bridge(
            _bundle(24, 24, 18, 2, base_completed=1, treated_completed=1)
        ))


class TestExecutableConfigHash(unittest.TestCase):
    def _manifest(self, driving: list[str]) -> dict:
        return {"id": "send_money", "effect": {"default_class": "EXPORT", "driving_args": driving}}

    def test_hash_changes_when_driving_args_change(self) -> None:
        a = executable_config_hash(KERNEL, {}, [self._manifest(["recipient"])])
        b = executable_config_hash(KERNEL, {}, [self._manifest(["recipient", "body"])])
        self.assertNotEqual(a, b)

    def test_hash_differs_from_plain_config_hash(self) -> None:
        manifests = [self._manifest(["recipient"])]
        self.assertNotEqual(
            executable_config_hash(KERNEL, {}, manifests),
            condition_config_hash(KERNEL, {}),
        )

    def test_hash_is_manifest_order_independent(self) -> None:
        m1 = {"id": "a", "effect": {}}
        m2 = {"id": "b", "effect": {}}
        self.assertEqual(
            executable_config_hash(KERNEL, {}, [m1, m2]),
            executable_config_hash(KERNEL, {}, [m2, m1]),
        )


class TestPinWithoutDecision(unittest.TestCase):
    def test_cp_rejects_pin_with_no_recorded_decision(self) -> None:
        from lab_contracts import content_hash
        from lab_runner.cp_export import CPExportError, export_cp

        # a trace with events but NO gate_decision — nothing to regress against
        trace = {
            "schema_version": "trace/v1", "trace_id": "t_nodec",
            "trial": {"run_id": "r", "scenario_id": "s", "condition_id": "governed",
                      "seed": "s000", "repeat_index": 0},
            "producer": {"mode": "wrapped_code", "provenance_fidelity": "heuristic_attribution",
                         "kernel_version": KERNEL, "runtime": "x"},
            "events": [{"seq": 0, "node": "root", "type": "tool_result", "tool": "read_txns",
                        "produces_value_ids": []}],
            "values": [],
        }
        ref = content_hash(trace)
        bundle = _bundle(24, 24, 18, 2)
        bundle["trials"].append({
            "trial_id": "t_nodec", "scenario_id": "s", "condition_id": "governed",
            "seed": "s999", "repeat_index": 99, "status": "completed", "trace_ref": ref,
        })
        pin = {"trace_id": "t_nodec", "expected_verdict": "DENY"}
        with self.assertRaises(CPExportError) as ctx:
            export_cp(bundle, regressions=[pin], traces={"t_nodec": trace}, condition_id="governed")
        self.assertIn("no recorded gate decision", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
