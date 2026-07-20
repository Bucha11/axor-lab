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


KERNEL = support.KERNEL_PINNED


def _multi_enforcing_bundle() -> dict[str, object]:
    """baseline (off) + two enforcing conditions; only `allowlist` improves ASR.
    The baseline id is 'baseline', NOT the literal 'ungoverned'."""
    conditions: list[dict[str, object]] = [
        {"schema_version": "condition/v1", "id": "baseline", "enforcement": "off",
         "kernel": KERNEL, "policy": {}},
        {"schema_version": "condition/v1", "id": "strict", "enforcement": "on",
         "kernel": KERNEL, "policy": {"profile": "strict"}},
        {"schema_version": "condition/v1", "id": "allowlist", "enforcement": "on",
         "kernel": KERNEL, "policy": {"profile": "strict", "allowlist": ["a@x"]}},
    ]
    for condition in conditions:
        condition["config_hash"] = condition_config_hash(KERNEL, condition["policy"])
    aggregates = [
        binary_aggregate("ASR", "baseline", 8, 10),
        binary_aggregate("ASR", "strict", 8, 10),      # no delta vs baseline
        binary_aggregate("ASR", "allowlist", 1, 10),   # real delta vs baseline
    ]
    return {
        "schema_version": "bundle/v1", "bundle_id": "b_multi",
        "conditions": conditions, "aggregates": aggregates, "tool_manifests": [],
    }


class TestMultiConditionExport(unittest.TestCase):
    def test_export_requires_condition_when_multiple_enforcing(self) -> None:
        with self.assertRaises(CPExportError) as ctx:
            export_cp(_multi_enforcing_bundle())
        self.assertIn("multiple enforcing", str(ctx.exception))

    def test_explicit_condition_is_exported_with_its_supporting_refs(self) -> None:
        export = export_cp(_multi_enforcing_bundle(), condition_id="allowlist")
        source: dict[str, object] = export.config["source"]  # type: ignore[assignment]
        self.assertEqual(source["condition_id"], "allowlist")
        self.assertEqual(source["baseline_condition_id"], "baseline")
        self.assertTrue(export.earned_bridge)
        self.assertEqual(
            source["supporting_aggregate_refs"], ["agg:ASR:baseline", "agg:ASR:allowlist"]
        )

    def test_exported_condition_without_delta_does_not_claim_the_bridge(self) -> None:
        # strict is enforcing but did NOT change the outcome — the bridge must
        # not fire just because SOME other condition did
        export = export_cp(_multi_enforcing_bundle(), condition_id="strict")
        self.assertEqual(export.config["source"]["condition_id"], "strict")  # type: ignore[index]
        self.assertFalse(export.earned_bridge)

    def test_unknown_condition_is_rejected(self) -> None:
        with self.assertRaises(CPExportError):
            export_cp(_multi_enforcing_bundle(), condition_id="nope")

    def test_earned_bridge_uses_enforcement_off_baseline_not_literal_id(self) -> None:
        # baseline id is 'baseline' (enforcement off); the old code hardcoded
        # 'ungoverned' and would have returned False
        self.assertTrue(earned_bridge(_multi_enforcing_bundle(), condition_id="allowlist"))


if __name__ == "__main__":
    unittest.main()
