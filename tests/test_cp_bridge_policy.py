"""Control Plane earned-bridge is derived from evidence, not stored aggregates
(review r16). The bridge recomputes ASR from the TRACES; fabricated
hash-consistent aggregates cannot earn it; the denominator is the full completed
set; and the delta must be statistically separated (its 95% interval excludes
zero), not merely large.
"""

from __future__ import annotations

import importlib.util
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
        # a deterministic-agent bundle is a MATCHED design: the receipt carries the
        # paired counts over the coordinate intersection, not two pooled arms (r19)
        self.assertEqual(analysis["comparison_design"], "matched_pairs")
        self.assertEqual(analysis["paired"]["completed_pairs"], 24)
        self.assertEqual(analysis["paired"]["dropped_pairs"], 0)
        self.assertEqual(analysis["paired"]["discordant"], {"b": 24, "c": 0})
        # the trial_refs are the actual completed-trial trace hashes, and the
        # receipt is content-addressable
        self.assertTrue(analysis["trial_refs"]["governed"])
        self.assertTrue(content_hash(analysis).startswith("sha256:"))


def _two_scenario_bundle(arm_scenarios, design=None):
    """Build a bundle where each arm's trials are assigned to the given scenarios,
    binding ungoverned trials to VIOLATING traces and governed to CLEAN traces.

    `arm_scenarios` = {"ungoverned": [scn...], "governed": [scn...]} — the scenario
    each of that arm's 24 trials runs under. Lets a test make the two arms cover a
    DIFFERENT mix of scenarios (composition shift) while keeping ASR 1.0 vs 0.0."""
    base = support.banking_scenario()
    names = sorted({n for scns in arm_scenarios.values() for n in scns})
    scenarios = [{**base, "name": n} for n in names]
    conditions = support.conditions()
    traces: dict[str, dict[str, object]] = {}
    trials: list[dict[str, object]] = []
    order = 0
    for cid, scns in arm_scenarios.items():
        pool = _VIOLATING if cid == "ungoverned" else _CLEAN
        for i, scn in enumerate(scns):
            trace = {**pool[i % 30], "trace_id": f"t_{cid}_{i}"}
            ref = content_hash(trace)
            traces[str(trace["trace_id"])] = trace
            trials.append({
                "trial_id": f"tr_{cid}_{i}", "scenario_id": scn,
                "condition_id": cid, "seed": f"s{i:03d}", "repeat_index": i,
                "status": "completed", "trace_ref": ref, "execution_order": order,
            })
            order += 1
    agg_design = {"comparison_design": design} if design else {}
    aggregates = [
        binary_aggregate("ASR", "ungoverned",
                         sum(1 for t in trials if t["condition_id"] == "ungoverned"),
                         sum(1 for t in trials if t["condition_id"] == "ungoverned"), **agg_design),
        binary_aggregate("ASR", "governed", 0,
                         sum(1 for t in trials if t["condition_id"] == "governed"), **agg_design),
    ]
    bundle = build_bundle(
        bundle_id="b_comp", created=CREATED, scenarios=scenarios, conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=trials, aggregates=aggregates, traces=traces,
    )
    return bundle, traces


def _controlled_bundle(pairs, scenarios_of=None, design=None):
    """A matched bundle over one scenario 'scn' (unless `scenarios_of[i]` overrides
    per pair) with FULL control of each pair's (base, treated) outcome: True =
    violating trace, False = clean trace, None = a FAILED trial (no trace). Lets a
    test build a specific discordant b/c count or seed both-arm failures."""
    base = support.banking_scenario()
    names = sorted({(scenarios_of or {}).get(i, "scn") for i in range(len(pairs))})
    scenarios = [{**base, "name": n} for n in names]
    conditions = support.conditions()
    traces: dict[str, dict[str, object]] = {}
    trials: list[dict[str, object]] = []
    order = 0
    for i, (base_out, treated_out) in enumerate(pairs):
        scn = (scenarios_of or {}).get(i, "scn")
        for cid, outcome in (("ungoverned", base_out), ("governed", treated_out)):
            trial = {"trial_id": f"tr_{cid}_{i}", "scenario_id": scn,
                     "condition_id": cid, "seed": f"s{i:03d}", "repeat_index": i,
                     "execution_order": order}
            order += 1
            if outcome is None:
                trial["status"] = "failed"
                trial["failure_reason"] = "seeded failure"
            else:
                trace = {**(_VIOLATING if outcome else _CLEAN)[i % 30],
                         "trace_id": f"t_{cid}_{i}"}
                ref = content_hash(trace)
                traces[str(trace["trace_id"])] = trace
                trial["status"] = "completed"
                trial["trace_ref"] = ref
            trials.append(trial)
    agg_design = {"comparison_design": design} if design else {}
    aggregates = [
        binary_aggregate("ASR", "ungoverned",
                         sum(1 for b, _ in pairs if b), sum(1 for b, _ in pairs if b is not None),
                         **agg_design),
        binary_aggregate("ASR", "governed",
                         sum(1 for _, t in pairs if t), sum(1 for _, t in pairs if t is not None),
                         **agg_design),
    ]
    bundle = build_bundle(
        bundle_id="b_ctl", created=CREATED, scenarios=scenarios, conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=trials, aggregates=aggregates, traces=traces,
    )
    return bundle, traces


class TestCausalValidity(unittest.TestCase):
    def test_matched_bridge_requires_minimum_absolute_effect(self) -> None:
        # 200 pairs, 180 concordant, 15 discordant favouring governance, 5 against.
        # McNemar is significant (p<0.05) but the NET risk reduction is only
        # (15-5)/200 = 0.05 < the 0.10 floor → practical significance gate fails,
        # so the bridge is NOT earned despite the significant p (review r20).
        pairs = ([(True, False)] * 15 + [(False, True)] * 5
                 + [(True, True)] * 90 + [(False, False)] * 90)
        bundle, traces = _controlled_bundle(pairs)
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_matched_bridge_earns_when_effect_clears_the_floor(self) -> None:
        # same shape but 40 discordant-b vs 5-c over 200 → net (40-5)/200 = 0.175
        pairs = ([(True, False)] * 40 + [(False, True)] * 5
                 + [(True, True)] * 80 + [(False, False)] * 75)
        bundle, traces = _controlled_bundle(pairs)
        self.assertTrue(earned_bridge(bundle, traces=traces))
        from lab_runner.cp_export import bridge_analysis
        analysis = bridge_analysis(bundle, "governed", traces=traces)
        self.assertGreaterEqual(analysis["paired"]["absolute_risk_reduction"], 0.10)

    def test_matched_bridge_counts_both_failed_units_as_dropped(self) -> None:
        # 24 completed discordant-b pairs (earns) + 6 pairs where BOTH arms FAILED.
        # The planned denominator must include the failed pairs (review r20).
        pairs = [(True, False)] * 24 + [(None, None)] * 6
        bundle, traces = _controlled_bundle(pairs)
        from lab_runner.cp_export import bridge_analysis
        analysis = bridge_analysis(bundle, "governed", traces=traces)
        self.assertEqual(analysis["paired"]["planned_pairs"], 30)
        self.assertEqual(analysis["paired"]["completed_pairs"], 24)
        self.assertEqual(analysis["paired"]["dropped_pairs"], 6)

    def test_independent_bridge_rejects_inverse_scenario_weighting(self) -> None:
        # THE r20 confound: same scenario SET, equal totals, every per-scenario arm
        # ratio exactly 0.5 — but baseline is 40 hard/20 easy while governed is 20
        # hard/40 easy. governance changed NOTHING (hard always violates, easy
        # never); the pooled ASR delta is pure reweighting. Exact per-scenario
        # balance must reject it.
        bundle, traces = _weighting_confound_bundle()
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_bridge_rejects_duplicate_metric_condition_aggregates(self) -> None:
        bundle, traces = _controlled_bundle([(True, False)] * 24)
        # inject a SECOND ASR aggregate for governed → the design read is ambiguous
        bundle["aggregates"].append(
            binary_aggregate("ASR", "governed", 0, 24, comparison_design="independent_samples")
        )
        with self.assertRaises(CPExportError):
            earned_bridge(bundle, traces=traces)

    def test_multiple_baselines_require_explicit_selection(self) -> None:
        bundle, traces = _controlled_bundle([(True, False)] * 24)
        # add a SECOND enforcement-off condition → baseline is ambiguous
        conditions = list(bundle["conditions"])
        off = next(c for c in conditions if c["enforcement"] == "off")
        conditions.append({**off, "id": "ungoverned2"})
        bundle["conditions"] = conditions
        with self.assertRaises(CPExportError):
            earned_bridge(bundle, traces=traces)


def _weighting_confound_bundle():
    """Same scenario set + equal totals, but a DIFFERENT per-scenario mix per arm:
    baseline 40 hard/20 easy, governed 20 hard/40 easy; hard always violates, easy
    never — so the pooled ASR differs purely by weighting, not governance."""
    base = support.banking_scenario()
    scenarios = [{**base, "name": "hard"}, {**base, "name": "easy"}]
    conditions = support.conditions()
    traces: dict[str, dict[str, object]] = {}
    trials: list[dict[str, object]] = []
    order = 0

    def _add(cid, scn, violating, n):
        nonlocal order
        pool = _VIOLATING if violating else _CLEAN
        for k in range(n):
            trace = {**pool[k % 30], "trace_id": f"t_{cid}_{scn}_{k}"}
            ref = content_hash(trace)
            traces[str(trace["trace_id"])] = trace
            trials.append({"trial_id": f"tr_{cid}_{scn}_{k}", "scenario_id": scn,
                           "condition_id": cid, "seed": f"s{order:03d}", "repeat_index": order,
                           "status": "completed", "trace_ref": ref, "execution_order": order})
            order += 1

    _add("ungoverned", "hard", True, 40)
    _add("ungoverned", "easy", False, 20)
    _add("governed", "hard", True, 20)
    _add("governed", "easy", False, 40)
    aggregates = [
        binary_aggregate("ASR", "ungoverned", 40, 60, comparison_design="independent_samples"),
        binary_aggregate("ASR", "governed", 20, 60, comparison_design="independent_samples"),
    ]
    bundle = build_bundle(
        bundle_id="b_conf", created=CREATED, scenarios=scenarios, conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=trials, aggregates=aggregates, traces=traces,
    )
    return bundle, traces


class TestDesignAwareBridge(unittest.TestCase):
    def test_disjoint_scenarios_do_not_earn_despite_huge_delta(self) -> None:
        # THE false bridge (r19): baseline runs 24 HEAVY scenarios (ASR 1.0),
        # governed runs 24 DIFFERENT light scenarios (ASR 0.0). Equal arm sizes,
        # maximal delta, but NOT ONE shared experimental unit — the delta is a
        # composition shift, not a governance effect. Must NOT earn.
        heavy = [f"scn-heavy-{i}" for i in range(24)]
        light = [f"scn-light-{i}" for i in range(24)]
        bundle, traces = _two_scenario_bundle({"ungoverned": heavy, "governed": light})
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_same_scenarios_paired_earns(self) -> None:
        # the honest case: BOTH arms run the SAME 24 scenarios, ungoverned violates
        # all, governed denies all → 24 discordant pairs in governance's favour
        shared = [f"scn-{i}" for i in range(24)]
        bundle, traces = _two_scenario_bundle({"ungoverned": shared, "governed": shared})
        self.assertTrue(earned_bridge(bundle, traces=traces))
        from lab_runner.cp_export import bridge_analysis
        analysis = bridge_analysis(bundle, "governed", traces=traces)
        self.assertEqual(analysis["comparison_design"], "matched_pairs")
        self.assertEqual(analysis["paired"]["completed_pairs"], 24)

    def test_partial_scenario_overlap_drops_below_pairing_floor(self) -> None:
        # arms share only a few scenarios and diverge on the rest — the scenario
        # SETS differ, so the composition guard rejects before pairing
        base = [f"scn-{i}" for i in range(24)]
        governed = [f"scn-{i}" for i in range(20)] + [f"scn-extra-{i}" for i in range(4)]
        bundle, traces = _two_scenario_bundle({"ungoverned": base, "governed": governed})
        self.assertFalse(earned_bridge(bundle, traces=traces))

    def test_analysis_records_scenario_balance(self) -> None:
        shared = [f"scn-{i}" for i in range(24)]
        bundle, traces = _two_scenario_bundle({"ungoverned": shared, "governed": shared})
        from lab_runner.cp_export import bridge_analysis
        analysis = bridge_analysis(bundle, "governed", traces=traces)
        # every scenario appears once per arm — the receipt records the balance so a
        # reader can see the arms tested the same composition
        self.assertEqual(set(analysis["scenario_balance"]["ungoverned"]), set(shared))
        self.assertEqual(analysis["scenario_balance"]["governed"][shared[0]], 1)


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

    def test_verify_cp_export_recomputes_from_scratch(self) -> None:
        # the export directory is SELF-CONTAINED: verify-cp-export recomputes the
        # whole handoff (graph + bridge + provenance) from source-bundle/ and
        # confirms it equals cp-deploy.json (review r19)
        import tempfile
        from pathlib import Path

        from lab_runner.bundle_io import write_bundle_dir
        from lab_runner.cli import main

        bundle, traces = _powered_real_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "bundle"
            write_bundle_dir(bdir, bundle, traces)
            out = Path(tmp) / "cp"
            self.assertEqual(main(["export-cp", str(bdir), "--condition", "governed",
                                    "--out", str(out)]), 0)
            # the directory carries its own source bundle + all traces
            self.assertTrue((out / "source-bundle" / "bundle.json").is_file())
            # recompute from scratch → matches
            self.assertEqual(main(["verify-cp-export", str(out)]), 0)
            # a DOCTORED deploy config no longer recomputes → fails
            deploy = out / "cp-deploy.json"
            cfg = json.loads(deploy.read_text())
            cfg["config_hash"] = "sha256:" + "0" * 64
            deploy.write_text(json.dumps(cfg))
            self.assertEqual(main(["verify-cp-export", str(out)]), 1)

    def test_verify_cp_export_checks_bridge_analysis_and_stale_files(self) -> None:
        import tempfile
        from pathlib import Path

        from lab_runner.bundle_io import write_bundle_dir
        from lab_runner.cli import main

        bundle, traces = _powered_real_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "bundle"
            write_bundle_dir(bdir, bundle, traces)
            out = Path(tmp) / "cp"
            self.assertEqual(main(["export-cp", str(bdir), "--condition", "governed",
                                    "--out", str(out)]), 0)
            # tampering the bridge-analysis file is caught by the manifest (r20)
            ba = out / "bridge-analysis.json"
            obj = json.loads(ba.read_text())
            obj["treated"] = {"violations": 0, "n": 999}
            ba.write_text(json.dumps(obj))
            self.assertEqual(main(["verify-cp-export", str(out)]), 1)
            # restore, then a STALE/injected file is caught too
            self.assertEqual(main(["export-cp", str(bdir), "--condition", "governed",
                                    "--out", str(out), "--overwrite"]), 0)
            (out / "sneaky.txt").write_text("not in the manifest")
            self.assertEqual(main(["verify-cp-export", str(out)]), 1)

    def test_reexport_requires_overwrite_and_clears_stale(self) -> None:
        import tempfile
        from pathlib import Path

        from lab_runner.bundle_io import write_bundle_dir
        from lab_runner.cli import main

        bundle, traces = _powered_real_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "bundle"
            write_bundle_dir(bdir, bundle, traces)
            out = Path(tmp) / "cp"
            self.assertEqual(main(["export-cp", str(bdir), "--condition", "governed",
                                    "--out", str(out)]), 0)
            stale = out / "bridge-traces" / "stale.json"
            stale.write_text("{}")
            # a re-export into a non-empty dir without --overwrite is refused
            self.assertNotEqual(
                main(["export-cp", str(bdir), "--condition", "governed", "--out", str(out)]), 0
            )
            # with --overwrite the stale file is gone and the export verifies
            self.assertEqual(main(["export-cp", str(bdir), "--condition", "governed",
                                    "--out", str(out), "--overwrite"]), 0)
            self.assertFalse(stale.exists())
            self.assertEqual(main(["verify-cp-export", str(out)]), 0)

    @unittest.skipUnless(importlib.util.find_spec("nacl"), "PyNaCl not installed")
    def test_signed_manifest_verifies_and_wrong_key_fails(self) -> None:
        import tempfile
        from pathlib import Path

        from nacl.signing import SigningKey

        from lab_runner.bundle_io import write_bundle_dir
        from lab_runner.cli import EXIT_UNVERIFIED, main

        sk = SigningKey.generate()
        priv, pub = bytes(sk).hex(), bytes(sk.verify_key).hex()
        wrong = bytes(SigningKey.generate().verify_key).hex()
        bundle, traces = _powered_real_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "bundle"
            write_bundle_dir(bdir, bundle, traces)
            out = Path(tmp) / "cp"
            self.assertEqual(main(["export-cp", str(bdir), "--condition", "governed",
                                    "--out", str(out), "--author", "acme", "--sign-key", priv]), 0)
            # correct key → verified (0)
            self.assertEqual(main(["verify-cp-export", str(out), "--pubkey", pub]), 0)
            # signed but no key → UNVERIFIED (5), not a silent pass
            self.assertEqual(main(["verify-cp-export", str(out)]), EXIT_UNVERIFIED)
            # wrong key → INVALID (1)
            self.assertEqual(main(["verify-cp-export", str(out), "--pubkey", wrong]), 1)


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

        import copy
        bundle, traces = _real_slice_bundle()
        env = dict(bundle["environment"])  # type: ignore[union-attr]
        prov = copy.deepcopy(env["config_provenance"])
        # nested {scenario: {condition: hash}} (review r19) — doctor the governed
        # hash for the one scenario that ran
        sid = next(s for s, cmap in prov["runtime_config_hashes"].items() if "governed" in cmap)
        prov["runtime_config_hashes"][sid]["governed"] = "sha256:" + "0" * 64  # doctored
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


class TestMandatoryRuntimeProvenance(unittest.TestCase):
    def test_runtime_config_hash_is_recorded_on_trial_execution(self) -> None:
        # the runner records the concrete runtime config hash ON the completed
        # trial, at execution — not reconstructed later (review r19)
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=2, run_id="r_prov",
        )
        completed = [t for t in result.trials if t["status"] == "completed"]
        self.assertTrue(completed)
        for trial in completed:
            self.assertTrue(str(trial.get("runtime_config_hash", "")).startswith("sha256:"))
            self.assertTrue(trial.get("config_compiler_version"))

    def test_config_provenance_pair_key_is_unambiguous(self) -> None:
        # provenance is nested {scenario: {condition: hash}} — no '<sid>|<cid>'
        # string key that could collide (review r19)
        bundle, _ = _real_slice_bundle()
        rch = bundle["environment"]["config_provenance"]["runtime_config_hashes"]
        self.assertIsInstance(rch, dict)
        for _sid, cmap in rch.items():
            self.assertIsInstance(cmap, dict)  # nested by condition, not a flat string
            for _cid, h in cmap.items():
                self.assertTrue(str(h).startswith("sha256:"))

    def test_cp_export_requires_config_provenance(self) -> None:
        # an evidence-backed export (traces given) over a bundle with NO recorded
        # provenance is refused — it cannot prove the runtime config it ships ran
        from lab_contracts import build_bundle
        from lab_runner.cp_export import CPExportError, export_cp

        bundle, traces = _real_slice_bundle()
        env = dict(bundle["environment"])  # type: ignore[union-attr]
        env["config_provenance"] = {}  # present but empty — nothing recorded
        stripped = build_bundle(
            bundle_id="b_noprov", created=CREATED, scenarios=bundle["scenarios"],
            conditions=bundle["conditions"], tool_manifests=bundle["tool_manifests"],
            environment=env, trials=bundle["trials"], aggregates=bundle["aggregates"],
            traces=traces,
        )
        with self.assertRaises(CPExportError) as ctx:
            export_cp(stripped, condition_id="governed", traces=traces)
        self.assertIn("config_provenance", str(ctx.exception))

    def test_cp_export_rejects_missing_runtime_hash_key(self) -> None:
        # provenance that OMITS the governed hash for an executed scenario is
        # refused — every exported hash must correspond to a recorded trial (r19)
        import copy
        from lab_contracts import build_bundle
        from lab_runner.cp_export import CPExportError, export_cp

        bundle, traces = _real_slice_bundle()
        env = dict(bundle["environment"])  # type: ignore[union-attr]
        prov = copy.deepcopy(env["config_provenance"])
        sid = next(s for s, cmap in prov["runtime_config_hashes"].items() if "governed" in cmap)
        del prov["runtime_config_hashes"][sid]["governed"]  # drop the executed key
        env["config_provenance"] = prov
        doctored = build_bundle(
            bundle_id="b_missing", created=CREATED, scenarios=bundle["scenarios"],
            conditions=bundle["conditions"], tool_manifests=bundle["tool_manifests"],
            environment=env, trials=bundle["trials"], aggregates=bundle["aggregates"],
            traces=traces,
        )
        with self.assertRaises(CPExportError) as ctx:
            export_cp(doctored, condition_id="governed", traces=traces)
        self.assertIn("no recorded runtime_config_hash", str(ctx.exception))


class TestExecutionProvenanceEnforcement(unittest.TestCase):
    def test_completed_trial_requires_runtime_config_hash(self) -> None:
        from lab_contracts import validate_artifact
        bundle, _ = _real_slice_bundle()
        ct = next(t for t in bundle["trials"] if t["status"] == "completed")
        del ct["runtime_config_hash"]
        errs = validate_artifact(bundle, "bundle")
        self.assertTrue(any("runtime_config_hash" in e for e in errs))

    def test_completed_trial_requires_config_compiler_version(self) -> None:
        from lab_contracts import validate_artifact
        bundle, _ = _real_slice_bundle()
        ct = next(t for t in bundle["trials"] if t["status"] == "completed")
        del ct["config_compiler_version"]
        errs = validate_artifact(bundle, "bundle")
        self.assertTrue(any("config_compiler_version" in e for e in errs))

    def test_runtime_hash_records_resolved_kernel_fingerprint(self) -> None:
        # the runner records the ACTUAL resolved backend fingerprint on the trial,
        # not just the declared condition.kernel string (review r20)
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=2, run_id="r_fp",
        )
        for trial in (t for t in result.trials if t["status"] == "completed"):
            self.assertTrue(trial.get("resolved_kernel_fingerprint"))

    def test_divergent_runtime_hashes_for_same_pair_are_rejected(self) -> None:
        # two completed trials of the SAME (scenario, condition) recording DIFFERENT
        # runtime hashes is a compiler/config drift the builder must not silently
        # collapse to one (review r20)
        from lab_contracts import build_bundle
        bundle, traces = _real_slice_bundle()
        gov = [t for t in bundle["trials"]
               if t["status"] == "completed" and t["condition_id"] == "governed"]
        gov[0]["runtime_config_hash"] = "sha256:" + "a" * 64
        gov[1]["runtime_config_hash"] = "sha256:" + "b" * 64  # divergent
        with self.assertRaises(ValueError) as ctx:
            build_bundle(
                bundle_id="b_div", created=CREATED, scenarios=bundle["scenarios"],
                conditions=bundle["conditions"], tool_manifests=bundle["tool_manifests"],
                environment={k: v for k, v in bundle["environment"].items()
                             if k != "config_provenance"},
                trials=bundle["trials"], aggregates=bundle["aggregates"], traces=traces,
            )
        self.assertIn("divergent runtime_config_hash", str(ctx.exception))

    def test_evidence_export_refuses_reconstructed_provenance(self) -> None:
        # a bundle whose provenance was reconstructed at build time (not recorded at
        # execution) is refused an evidence-backed export (review r20)
        import copy
        from lab_contracts import build_bundle
        from lab_runner.cp_export import CPExportError, export_cp
        bundle, traces = _real_slice_bundle()
        env = dict(bundle["environment"])  # type: ignore[union-attr]
        prov = copy.deepcopy(env["config_provenance"])
        prov["provenance_status"] = "reconstructed_legacy"
        env["config_provenance"] = prov
        doctored = build_bundle(
            bundle_id="b_recon", created=CREATED, scenarios=bundle["scenarios"],
            conditions=bundle["conditions"], tool_manifests=bundle["tool_manifests"],
            environment=env, trials=bundle["trials"], aggregates=bundle["aggregates"],
            traces=traces,
        )
        with self.assertRaises(CPExportError) as ctx:
            export_cp(doctored, condition_id="governed", traces=traces)
        self.assertIn("reconstructed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
