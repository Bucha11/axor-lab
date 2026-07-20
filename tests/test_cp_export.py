"""B2 — Control Plane export (control-plane-handoff.md).

The validated policy + config_hash + manifests + regressions carry over
byte-identically (config_hash is the carry-over key); the production-todo lists
exactly the four NOT-reused categories; the earned bridge surfaces only after a
result where governance changed the outcome.
"""

from __future__ import annotations

import json
import unittest

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, condition_config_hash, content_hash
from lab_runner import run_experiment_suite
from lab_runner.cp_export import PRODUCTION_TODO, CPExportError, earned_bridge, export_cp

CREATED = "2026-07-19T12:00:00+00:00"


def _bundle_and_traces(governance_helps: bool = True):
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
    bundle = build_bundle(
        bundle_id="b_cp", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    return bundle, result.traces


def _bundle(governance_helps: bool = True) -> dict[str, object]:
    return _bundle_and_traces(governance_helps)[0]


def _denied_trace(traces: dict) -> dict:
    return next(
        t for t in traces.values()
        if any(
            e.get("type") == "gate_decision" and e["decision"]["verdict"] == "DENY"
            for e in t["events"]
        )
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

    def test_validated_pin_carries_over_with_full_shape(self) -> None:
        bundle, traces = _bundle_and_traces()
        trace = _denied_trace(traces)
        pin = {"trace_id": str(trace["trace_id"]), "trace_ref": content_hash(trace),
               "expected_verdict": "DENY", "expected_sequence": ["DENY"]}
        export = export_cp(bundle, regressions=[pin], traces=traces)
        carried = export.config["regressions"]
        self.assertEqual(len(carried), 1)  # type: ignore[arg-type]
        got = carried[0]  # type: ignore[index]
        self.assertEqual(got["trace_id"], str(trace["trace_id"]))
        self.assertEqual(got["trace_ref"], content_hash(trace))
        self.assertEqual(got["expected_verdict"], "DENY")
        self.assertEqual(got["expected_sequence"], ["DENY"])
        # the pin now records WHICH scenario/condition it re-runs, from the trial
        self.assertEqual(got["condition_id"], "governed")
        self.assertIn("scenario_id", got)

    def test_pin_for_a_trace_not_in_the_bundle_is_rejected(self) -> None:
        bundle, traces = _bundle_and_traces()
        pins = [{"trace_id": "t_never_ran", "expected_verdict": "DENY"}]
        with self.assertRaises(CPExportError) as ctx:
            export_cp(bundle, regressions=pins, traces=traces)
        self.assertIn("no such trace", str(ctx.exception))

    def test_pin_with_a_fabricated_sequence_is_rejected(self) -> None:
        bundle, traces = _bundle_and_traces()
        trace = _denied_trace(traces)
        # claim a sequence the frozen trace never produced
        pins = [{"trace_id": str(trace["trace_id"]), "expected_verdict": "DENY",
                 "expected_sequence": ["ALLOW", "ALLOW", "DENY"]}]
        with self.assertRaises(CPExportError) as ctx:
            export_cp(bundle, regressions=pins, traces=traces)
        self.assertIn("does not match the frozen", str(ctx.exception))

    def test_pin_with_a_stale_trace_ref_is_rejected(self) -> None:
        bundle, traces = _bundle_and_traces()
        trace = _denied_trace(traces)
        pins = [{"trace_id": str(trace["trace_id"]), "trace_ref": "sha256:stale",
                 "expected_verdict": "DENY"}]
        with self.assertRaises(CPExportError) as ctx:
            export_cp(bundle, regressions=pins, traces=traces)
        self.assertIn("content hash", str(ctx.exception))

    def test_export_writes_frozen_pinned_trace_bodies(self) -> None:
        # the CP config carries a pin's content hash, but a hash is not the bytes
        # to replay elsewhere — the frozen trace body must be exported too (r13)
        import subprocess
        import sys
        import tempfile
        from pathlib import Path as _Path

        from lab_contracts import content_hash

        bundle, traces = _bundle_and_traces()
        trace = _denied_trace(traces)
        with tempfile.TemporaryDirectory() as tmp:
            root = _Path(tmp)
            # write a bundle dir + a pins file, then run `export-cp` as the CLI does
            from lab_runner.bundle_io import write_bundle_dir
            bdir = root / "bundle"
            write_bundle_dir(bdir, bundle, traces)
            pins = root / "pins.json"
            pins.write_text(json.dumps([{
                "trace_id": str(trace["trace_id"]), "trace_ref": content_hash(trace),
                "expected_verdict": "DENY", "expected_sequence": ["DENY"],
            }]))
            out = root / "cp"
            result = subprocess.run(
                [sys.executable, "-m", "lab_runner", "export-cp", str(bdir),
                 "--pins", str(pins), "--out", str(out)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            frozen = out / "regression-traces" / (content_hash(trace).removeprefix("sha256:") + ".json")
            self.assertTrue(frozen.is_file())  # the bytes are there, not just a hash
            self.assertEqual(json.loads(frozen.read_text())["trace_id"], str(trace["trace_id"]))

    def test_pins_without_traces_are_refused(self) -> None:
        bundle = _bundle()
        pins = [{"trace_id": "t_x", "expected_verdict": "DENY"}]
        with self.assertRaises(CPExportError) as ctx:
            export_cp(bundle, regressions=pins)  # no traces=
        self.assertIn("no bundle traces", str(ctx.exception))

    def test_export_requires_a_recorded_config_hash(self) -> None:
        bundle = _bundle()
        for condition in bundle["conditions"]:  # type: ignore[union-attr]
            if condition["enforcement"] == "on":
                del condition["config_hash"]
        with self.assertRaises(CPExportError) as ctx:
            export_cp(bundle)
        self.assertIn("no recorded config_hash", str(ctx.exception))

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
