"""Control Plane earned-bridge is derived from evidence, not stored aggregates
(review r16). The bridge recomputes ASR from the TRACES; fabricated
hash-consistent aggregates cannot earn it; the denominator is the full completed
set; and the delta must be statistically separated (its 95% interval excludes
zero), not merely large.
"""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
