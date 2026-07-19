"""B2 — Control Plane export (control-plane-handoff.md).

The validated policy + config_hash + manifests + regressions carry over
byte-identically (config_hash is the carry-over key); the production-todo lists
exactly the four NOT-reused categories; the earned bridge surfaces only after a
result where governance changed the outcome.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, condition_config_hash
from lab_runner import run_experiment_suite
from lab_runner.cp_export import PRODUCTION_TODO, CPExportError, earned_bridge, export_cp

CREATED = "2026-07-19T12:00:00+00:00"


def _bundle(governance_helps: bool = True) -> dict[str, object]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=10, run_id="r_cp",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    ungoverned_breaches = sum(1 for b, _ in pairs if b) if governance_helps else 0
    aggregates = [
        binary_aggregate("ASR", "ungoverned", ungoverned_breaches, len(pairs)),
        binary_aggregate("ASR", "governed", 0, len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    return build_bundle(
        bundle_id="b_cp", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )


class TestCPExport(unittest.TestCase):
    def test_config_hash_is_the_carry_over_key_byte_identical(self) -> None:
        bundle = _bundle()
        governed = next(c for c in bundle["conditions"] if c["enforcement"] == "on")  # type: ignore[union-attr]
        export = export_cp(bundle)
        self.assertEqual(export.config["config_hash"], governed["config_hash"])
        # and it is provably recomputable from the exported policy+kernel
        self.assertEqual(
            export.config["config_hash"],
            condition_config_hash(str(export.config["kernel"]), export.config["policy"]),  # type: ignore[arg-type]
        )

    def test_policy_and_manifests_carry_over(self) -> None:
        bundle = _bundle()
        export = export_cp(bundle)
        self.assertEqual(export.config["policy"]["profile"], "strict")  # type: ignore[index]
        exported_ids = {m["id"] for m in export.config["tool_manifests"]}  # type: ignore[union-attr]
        self.assertEqual(exported_ids, {"read_txns", "send_money"})

    def test_regressions_carry_over(self) -> None:
        bundle = _bundle()
        pins = [{"trace_id": "t_x", "expected_verdict": "DENY"}]
        export = export_cp(bundle, regressions=pins)
        self.assertEqual(export.config["regressions"], pins)

    def test_production_todo_lists_the_four_not_reused_categories(self) -> None:
        export = export_cp(_bundle())
        keys = {key for key, _ in PRODUCTION_TODO}
        self.assertEqual(keys, {"tool_bindings", "credentials", "topology", "operations"})
        for key in keys:
            self.assertIn(key, export.production_todo)
        self.assertIn("what Lab does NOT carry over", export.production_todo)

    def test_earned_bridge_true_when_governance_changed_the_outcome(self) -> None:
        self.assertTrue(earned_bridge(_bundle(governance_helps=True)))

    def test_earned_bridge_false_when_no_delta(self) -> None:
        self.assertFalse(earned_bridge(_bundle(governance_helps=False)))

    def test_export_refuses_a_tampered_config_hash(self) -> None:
        bundle = _bundle()
        for condition in bundle["conditions"]:  # type: ignore[union-attr]
            if condition["enforcement"] == "on":
                condition["config_hash"] = "sha256:deadbeef"
        with self.assertRaises(CPExportError):
            export_cp(bundle)


if __name__ == "__main__":
    unittest.main()
