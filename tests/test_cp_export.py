"""B2 — Control Plane export (control-plane-handoff.md).

The validated policy + manifests + regressions carry over byte-identically; the
parametric_config_hash is the carry-over key (kernel+policy with symbolic
$inputs, stable when production re-parameterizes), while config_hash is the
recorded fingerprint of the concrete config the Lab run measured. The
production-todo lists exactly the four NOT-reused categories; the earned bridge
surfaces only after a result where governance changed the outcome.
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
    if not governance_helps:
        # a REAL no-effect run: the governed condition allowlists the attacker, so
        # it ALSO allows the exfil — the traces themselves show no delta, which the
        # evidence-derived bridge recomputes (a fabricated aggregate can't earn) (r16)
        gov = conditions[1]
        gov["policy"] = {"profile": "strict", "trust_model": "content-ledger",
                         "allowlist": [support.ATTACKER_IBAN]}
        gov["config_hash"] = condition_config_hash(support.KERNEL_PINNED, gov["policy"])
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=24, run_id="r_cp",  # >= the earned-bridge minimum effective n
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
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
    def test_config_hash_is_the_recorded_fingerprint_byte_identical(self) -> None:
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

    def test_production_todo_names_parametric_hash_as_carry_over_key(self) -> None:
        # the todo must name the PARAMETRIC hash as the carry-over key — the
        # concrete config_hash/runtime_config_hashes describe the Lab run, not what
        # production hashes, so calling config_hash the carry-over key was a
        # naming lie (review r18)
        export = export_cp(_bundle())
        todo = export.production_todo
        self.assertIn("parametric_config_hash", todo)
        self.assertIn("carry-over key", todo)
        # it must NOT still claim the concrete config_hash is the carry-over key
        self.assertNotIn("`config_hash` is the carry-over key", todo)

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
        bundle, traces = _bundle_and_traces(governance_helps=True)
        self.assertTrue(earned_bridge(bundle, traces=traces))

    def test_earned_bridge_false_when_no_delta(self) -> None:
        # a REAL no-effect run: the governed arm allowlists the attacker, so the
        # recomputed ASR shows no delta — a fabricated aggregate could not rescue it
        bundle, traces = _bundle_and_traces(governance_helps=False)
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_export_refuses_a_tampered_config_hash(self) -> None:
        bundle = _bundle()
        for condition in bundle["conditions"]:  # type: ignore[union-attr]
            if condition["enforcement"] == "on":
                condition["config_hash"] = "sha256:deadbeef"
        with self.assertRaises(CPExportError):
            export_cp(bundle)


KERNEL = support.KERNEL_PINNED


def _multi_enforcing_bundle() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """baseline (off) + two enforcing conditions; only `allowlist` improves ASR.
    The baseline id is 'baseline', NOT the literal 'ungoverned'.

    This is a REAL run, not a synthetic bundle: the earned bridge recomputes ASR
    from the traces (review r16), so the deltas below come from actual enforcement.
    `strict` here allowlists the attacker (so it still permits the exfil → no
    delta); `allowlist` here runs the real content-ledger deny (→ real delta).
    The ids are deliberately mismatched to the policies to prove the bridge keys
    off measured evidence and the enforcement-off ROLE, never off condition names."""
    strict_policy = {"profile": "strict", "trust_model": "content-ledger",
                     "allowlist": [support.ATTACKER_IBAN]}  # permits the exfil → no delta
    allowlist_policy = {"profile": "strict", "trust_model": "content-ledger"}  # denies → delta
    conditions: list[dict[str, object]] = [
        {"schema_version": "condition/v1", "id": "baseline", "label": "baseline",
         "enforcement": "off", "kernel": KERNEL,
         "config_hash": condition_config_hash(KERNEL, None)},
        {"schema_version": "condition/v1", "id": "strict", "label": "strict",
         "enforcement": "on", "kernel": KERNEL, "policy": strict_policy,
         "config_hash": condition_config_hash(KERNEL, strict_policy)},
        {"schema_version": "condition/v1", "id": "allowlist", "label": "allowlist",
         "enforcement": "on", "kernel": KERNEL, "policy": allowlist_policy,
         "config_hash": condition_config_hash(KERNEL, allowlist_policy)},
    ]
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=24, run_id="r_multi",  # >= the earned-bridge minimum effective n
    )

    def _agg(cid: str) -> object:
        pairs = result.pairs("baseline", cid, metric="ASR")
        if cid == "baseline":
            return binary_aggregate("ASR", "baseline", sum(1 for b, _ in pairs if b), len(pairs))
        return binary_aggregate("ASR", cid, sum(1 for _, t in pairs if t), len(pairs),
                                test=mcnemar_test(pairs, vs="baseline"))

    bundle = build_bundle(
        bundle_id="b_multi", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials,
        aggregates=[_agg("baseline"), _agg("strict"), _agg("allowlist")],
        traces=result.traces,
    )
    return bundle, result.traces


class TestMultiConditionExport(unittest.TestCase):
    def test_export_requires_condition_when_multiple_enforcing(self) -> None:
        bundle, _ = _multi_enforcing_bundle()
        with self.assertRaises(CPExportError) as ctx:
            export_cp(bundle)
        self.assertIn("multiple enforcing", str(ctx.exception))

    def test_explicit_condition_is_exported_with_its_supporting_refs(self) -> None:
        bundle, traces = _multi_enforcing_bundle()
        export = export_cp(bundle, condition_id="allowlist", traces=traces)
        source: dict[str, object] = export.config["source"]  # type: ignore[assignment]
        self.assertEqual(source["condition_id"], "allowlist")
        self.assertEqual(source["baseline_condition_id"], "baseline")
        self.assertTrue(export.earned_bridge)
        # supporting evidence is the RECOMPUTED analysis receipt (r17), not stored
        # aggregate refs the bridge never consulted
        from lab_contracts import content_hash
        analysis: dict = source["bridge_analysis"]  # type: ignore[assignment]
        self.assertEqual(analysis["kind"], "cp_bridge_analysis/v1")
        self.assertEqual(analysis["baseline_condition_id"], "baseline")
        self.assertEqual(analysis["treated_condition_id"], "allowlist")
        self.assertEqual(source["bridge_analysis_ref"], content_hash(analysis))

    def test_exported_condition_without_delta_does_not_claim_the_bridge(self) -> None:
        # strict is enforcing but did NOT change the outcome — the bridge must
        # not fire just because SOME other condition did
        bundle, traces = _multi_enforcing_bundle()
        export = export_cp(bundle, condition_id="strict", traces=traces)
        self.assertEqual(export.config["source"]["condition_id"], "strict")  # type: ignore[index]
        self.assertFalse(export.earned_bridge)

    def test_unknown_condition_is_rejected(self) -> None:
        bundle, traces = _multi_enforcing_bundle()
        with self.assertRaises(CPExportError):
            export_cp(bundle, condition_id="nope", traces=traces)

    def test_earned_bridge_uses_enforcement_off_baseline_not_literal_id(self) -> None:
        # baseline id is 'baseline' (enforcement off); the old code hardcoded
        # 'ungoverned' and would have returned False
        bundle, traces = _multi_enforcing_bundle()
        self.assertTrue(earned_bridge(bundle, condition_id="allowlist", traces=traces))


if __name__ == "__main__":
    unittest.main()
