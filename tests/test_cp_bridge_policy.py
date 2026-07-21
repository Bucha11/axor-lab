"""Control Plane earned-bridge is derived from evidence, not stored aggregates
(review r16). The bridge recomputes ASR from the TRACES; fabricated
hash-consistent aggregates cannot earn it; the denominator is the full completed
set; and the delta must be statistically separated (its 95% interval excludes
zero), not merely large.
"""

from __future__ import annotations

import json
import unittest

from tests import support
from lab_analysis import binary_aggregate
from lab_contracts import build_bundle, content_hash
from lab_runner import ScriptedAgent, run_experiment_suite
from lab_runner.cp_export import CPExportError, earned_bridge

CREATED = "2026-07-20T12:00:00+00:00"


def _trace_pools():
    """Run the real slice; return (violating_traces, clean_traces) — the
    ungoverned run violates, the governed run is denied (no violation)."""
    result = run_experiment_suite(
        [support.banking_scenario()], support.manifests(), support.conditions(),
        support.kernel_registry(), repeats=30, run_id="r_pool", agent=ScriptedAgent(attack_rate=1.0),
    )
    scenario = support.banking_scenario()
    from lab_runner import evaluate
    violating, clean = [], []
    for trace in result.traces.values():
        if evaluate(scenario["violation"], trace, scenario.get("inputs", {})):
            violating.append(trace)
        else:
            clean.append(trace)
    return violating, clean


_VIOLATING, _CLEAN = _trace_pools()


def _bundle(base_violations: int, base_n: int, treated_violations: int, treated_n: int):
    """Build a bundle whose ungoverned/governed conditions have EXACTLY the given
    violation counts, by binding trials to real violating/clean traces."""
    assert base_violations <= min(base_n, len(_VIOLATING))
    assert treated_violations <= min(treated_n, len(_VIOLATING))
    scenario = support.banking_scenario()
    conditions = support.conditions()
    traces: dict[str, dict[str, object]] = {}
    trials: list[dict[str, object]] = []
    order = 0

    def _add(cid: str, n: int, violations: int):
        nonlocal order
        for i in range(n):
            src = (_VIOLATING if i < violations else _CLEAN)[i % 30]
            # give each trial a distinct trace by tagging the trace_id
            trace = {**src, "trace_id": f"t_{cid}_{i}"}
            ref = content_hash(trace)
            traces[str(trace["trace_id"])] = trace
            trials.append({
                "trial_id": f"tr_{cid}_{i}", "scenario_id": str(scenario["name"]),
                "condition_id": cid, "seed": f"s{i:03d}", "repeat_index": i,
                "status": "completed", "trace_ref": ref, "execution_order": order,
            })
            order += 1

    _add("ungoverned", base_n, base_violations)
    _add("governed", treated_n, treated_violations)
    # honest aggregates recomputed from the same counts
    aggregates = [
        binary_aggregate("ASR", "ungoverned", base_violations, base_n),
        binary_aggregate("ASR", "governed", treated_violations, treated_n),
    ]
    bundle = build_bundle(
        bundle_id="b_cpb", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=trials, aggregates=aggregates, traces=traces,
    )
    return bundle, traces


class TestEvidenceDerivedBridge(unittest.TestCase):
    def test_powered_separated_delta_earns_from_traces(self) -> None:
        bundle, traces = _bundle(24, 24, 0, 24)  # ASR 1.0 -> 0.0
        self.assertTrue(earned_bridge(bundle, traces=traces))

    def test_no_traces_cannot_earn(self) -> None:
        bundle, _ = _bundle(24, 24, 0, 24)
        self.assertFalse(earned_bridge(bundle))  # no traces → not verifiable

    def test_below_minimum_n_does_not_earn(self) -> None:
        bundle, traces = _bundle(8, 8, 0, 8)
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_no_real_effect_does_not_earn(self) -> None:
        bundle, traces = _bundle(24, 24, 24, 24)  # both arms violate → delta 0
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_cp_bridge_rejects_fabricated_hash_consistent_aggregates(self) -> None:
        # real traces show NO effect (both arms violate), but the aggregates are
        # fabricated to claim a perfect governed result. The bridge recomputes
        # from traces, so the fabrication is ignored → not earned.
        bundle, traces = _bundle(24, 24, 24, 24)
        gov = next(a for a in bundle["aggregates"] if a["condition_id"] == "governed")
        gov["estimate"] = 0.0  # fabricated: claim governance eliminated the attack
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_cp_bridge_requires_interval_to_exclude_zero(self) -> None:
        # a real delta of 0.15 (20/20 vs 17/20) clears the min-delta gate, but at
        # n=20 the difference's 95% interval includes zero → not a production
        # signal → not earned
        bundle, traces = _bundle(20, 20, 17, 20)
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_cp_bridge_rejects_partial_trace_dictionary(self) -> None:
        # a POWERED, separated run — but the caller hands the bridge only the
        # favourable subset of traces. Every completed trial must have its trace;
        # a partial dict is a HARD error, never a silent skip (review r17).
        bundle, traces = _bundle(24, 24, 0, 24)
        # drop half the traces — a cherry-picked subset
        keys = list(traces)
        partial = {k: traces[k] for k in keys[: len(keys) // 2]}
        with self.assertRaises(CPExportError):
            earned_bridge(bundle, traces=partial)

    def test_cp_bridge_requires_every_completed_trial_trace(self) -> None:
        # remove ONE completed trial's trace → the bridge refuses to compute
        bundle, traces = _bundle(24, 24, 0, 24)
        victim = next(iter(traces))
        del traces[victim]
        with self.assertRaises(CPExportError):
            earned_bridge(bundle, traces=traces)

    def test_cp_export_verifies_bundle_before_bridge_analysis(self) -> None:
        # export_cp over a partial trace set raises rather than exporting a config
        # whose bridge was earned on incomplete evidence
        from lab_runner.cp_export import export_cp

        bundle, traces = _bundle(24, 24, 0, 24)
        keys = list(traces)
        partial = {k: traces[k] for k in keys[: len(keys) // 2]}
        with self.assertRaises(CPExportError):
            export_cp(bundle, condition_id="governed", traces=partial)

    def test_cp_bridge_supporting_ref_names_recomputed_analysis(self) -> None:
        from lab_contracts import content_hash
        from lab_runner.cp_export import bridge_analysis

        bundle, traces = _bundle(24, 24, 0, 24)
        analysis = bridge_analysis(bundle, "governed", traces=traces)
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis["kind"], "cp_bridge_analysis/v1")
        self.assertEqual(analysis["treated"]["n"], 24)
        self.assertEqual(analysis["baseline"]["n"], 24)
        # the trial_refs are the actual completed-trial trace hashes, and the
        # receipt is content-addressable
        self.assertTrue(analysis["trial_refs"]["governed"])
        self.assertTrue(content_hash(analysis).startswith("sha256:"))


def _real_slice_bundle():
    """A real run-suite bundle+traces that passes verify_bundle when untouched."""
    from lab_analysis import mcnemar_test
    from lab_contracts import build_bundle

    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_graph",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_graph", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    return bundle, result.traces


def _powered_real_bundle():
    """A real, POWERED slice (ungoverned all-violate, governed all-deny) that both
    earns the bridge and passes verify_bundle."""
    from lab_analysis import mcnemar_test
    from lab_contracts import build_bundle

    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=24, run_id="r_pow", agent=ScriptedAgent(attack_rate=1.0),
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_pow", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    return bundle, result.traces


class TestBridgeExportPortability(unittest.TestCase):
    def test_bridge_export_contains_recomputable_evidence(self) -> None:
        # the CLI export writes the FROZEN bridge trace bodies, so the earned-bridge
        # analysis is independently recomputable from the export directory alone
        import tempfile
        from pathlib import Path

        from lab_contracts import content_hash
        from lab_runner.bundle_io import write_bundle_dir
        from lab_runner.cli import main

        bundle, traces = _powered_real_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "bundle"
            write_bundle_dir(bdir, bundle, traces)
            out = Path(tmp) / "cp"
            self.assertEqual(main(["export-cp", str(bdir), "--condition", "governed",
                                    "--out", str(out)]), 0)
            cfg = json.loads((out / "cp-deploy.json").read_text())
            self.assertTrue(cfg["source"]["bridge_analysis"])  # earned
            # every trial_ref in the analysis has a frozen body on disk
            refs = [r for v in cfg["source"]["bridge_analysis"]["trial_refs"].values() for r in v]
            self.assertTrue(refs)
            for ref in refs:
                body = out / "bridge-traces" / (ref.removeprefix("sha256:") + ".json")
                self.assertTrue(body.is_file())
                self.assertEqual(content_hash(json.loads(body.read_text())), ref)


class TestExportVerifiesGraph(unittest.TestCase):
    def test_export_cp_calls_verify_bundle(self) -> None:
        # a graph-invalid bundle (a completed trial whose trace's own coordinates
        # disagree) is refused by export_cp, which runs the full bundle graph
        # verification rather than trusting caller discipline (review r18)
        from lab_runner.cp_export import CPExportError, export_cp

        bundle, traces = _real_slice_bundle()
        export_cp(bundle, condition_id="governed", traces=traces)  # clean → ok
        for trial in bundle["trials"]:
            if trial.get("status") == "completed" and trial["condition_id"] == "governed":
                trial["condition_id"] = "ungoverned"  # now disagrees with its trace
                break
        with self.assertRaises(CPExportError):
            export_cp(bundle, condition_id="governed", traces=traces)

    def test_export_cp_rejects_trial_trace_coordinate_mismatch(self) -> None:
        from lab_runner.cp_export import CPExportError, export_cp

        bundle, traces = _real_slice_bundle()
        for trial in bundle["trials"]:
            if trial.get("status") == "completed":
                trial["scenario_id"] = "some-other-scenario"  # trace disagrees
                break
        with self.assertRaises(CPExportError):
            export_cp(bundle, condition_id="governed", traces=traces)


class TestRecordedRuntimeHash(unittest.TestCase):
    def test_runtime_config_hash_is_recorded_during_run(self) -> None:
        from lab_contracts import CONFIG_COMPILER_VERSION

        bundle, _ = _real_slice_bundle()
        prov = bundle["environment"]["config_provenance"]
        self.assertEqual(prov["compiler_version"], CONFIG_COMPILER_VERSION)
        self.assertTrue(prov["runtime_config_hashes"])

    def test_export_rejects_recomputed_runtime_hash_different_from_recorded(self) -> None:
        # REBUILD with a doctored-but-content-consistent provenance (build_bundle
        # keeps a pre-supplied config_provenance and hashes it), so the failure is
        # the runtime-hash CHECK — not an incidental content-hash mismatch — proving
        # the export refuses a config identity that never actually ran (review r18)
        from lab_contracts import build_bundle
        from lab_runner.cp_export import CPExportError, export_cp

        bundle, traces = _real_slice_bundle()
        env = dict(bundle["environment"])  # type: ignore[union-attr]
        prov = {"compiler_version": env["config_provenance"]["compiler_version"],
                "runtime_config_hashes": dict(env["config_provenance"]["runtime_config_hashes"])}
        gov_key = next(k for k in prov["runtime_config_hashes"] if k.endswith("|governed"))
        prov["runtime_config_hashes"][gov_key] = "sha256:" + "0" * 64  # doctored
        env["config_provenance"] = prov
        doctored = build_bundle(
            bundle_id="b_doc", created=CREATED, scenarios=bundle["scenarios"],
            conditions=bundle["conditions"], tool_manifests=bundle["tool_manifests"],
            environment=env, trials=bundle["trials"], aggregates=bundle["aggregates"],
            traces=traces,
        )
        with self.assertRaises(CPExportError) as ctx:
            export_cp(doctored, condition_id="governed", traces=traces)
        self.assertIn("does not match what ran", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
