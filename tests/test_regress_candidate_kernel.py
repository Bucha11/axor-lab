"""`axor-lab regress` runs the CANDIDATE kernel, not the recorded one (review r18).

Regression testing asks "would THIS frozen incident still be governed correctly
under a DIFFERENT / future kernel?". The candidate resolver must therefore take
the version from `--kernel` (or the chosen condition) and the policy from the
chosen condition — never the kernel the trace happened to be recorded under —
while still expanding `$inputs` allowlists against the trace's own scenario.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, condition_config_hash, content_hash
from lab_runner import (
    AxorKernel,
    axor_available,
    check_pins,
    real_kernel_version,
    resolve_candidate_kernel_for_trace,
    resolve_recorded_kernel_for_trace,
    run_experiment_suite,
)
from lab_runner.bundle_io import write_bundle_dir
from lab_runner.regression import RegressionPin

CREATED = "2026-07-20T12:00:00+00:00"


def _slice_bundle():
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_reg",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_reg", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    return bundle, result.traces


def _a_governed_trace(bundle, traces):
    for t in traces.values():
        if str(t["trial"]["condition_id"]) == "governed":
            return t
    raise AssertionError("no governed trace")


class TestCandidateResolver(unittest.TestCase):
    def test_candidate_ignores_the_traces_recorded_condition(self) -> None:
        # the recorded trace ran under "governed" (reference kernel). The candidate
        # resolver, handed a DIFFERENT candidate condition + version, must build the
        # CANDIDATE kernel — not the trace's recorded one.
        bundle, traces = _slice_bundle()
        trace = _a_governed_trace(bundle, traces)
        candidate = {
            "id": "future", "enforcement": "on", "kernel": support.KERNEL_NO_TAINT_FLOOR,
            "policy": {"profile": "strict"},
        }
        kernel = resolve_candidate_kernel_for_trace(
            bundle, trace, candidate, candidate_version=support.KERNEL_NO_TAINT_FLOOR
        )
        self.assertEqual(kernel.version, support.KERNEL_NO_TAINT_FLOOR)
        # the RECORDED resolver, by contrast, returns the kernel the trace ran under
        recorded = resolve_recorded_kernel_for_trace(bundle, trace)
        self.assertEqual(recorded.version, support.KERNEL_PINNED)

    def test_regress_selected_condition_does_not_use_trace_original_kernel(self) -> None:
        # check_pins with a candidate kernel_for must report the CANDIDATE kernel's
        # fingerprint for each replayed pin, not the fallback/recorded one
        bundle, traces = _slice_bundle()
        trace = _a_governed_trace(bundle, traces)
        pin = RegressionPin(trace_id=str(trace["trace_id"]), trace_ref=content_hash(trace),
                            expected_verdict="DENY", expected_sequence=("DENY",))
        candidate = support.conditions()[1]  # governed
        results = check_pins(
            (pin,), traces, candidate, support.kernel_registry().get(support.KERNEL_PINNED),
            support.manifests(),
            inputs_for=lambda t: support.banking_scenario().get("inputs", {}),
            kernel_for=lambda t: resolve_candidate_kernel_for_trace(
                bundle, t, candidate, candidate_version=support.KERNEL_NO_TAINT_FLOOR
            ),
        )
        # the reported kernel is the candidate (no-taint-floor variant fingerprint)
        self.assertIn(support.KERNEL_NO_TAINT_FLOOR, str(results[0]["kernel"]))
        self.assertNotIn("+taint_floor=off", str(results[0]["kernel"]))  # this variant is on-by-string

    def test_regress_result_reports_actual_per_trace_kernel(self) -> None:
        # even when the fallback kernel is X, the per-pin report names the kernel
        # that ACTUALLY ran the pin
        bundle, traces = _slice_bundle()
        trace = _a_governed_trace(bundle, traces)
        pin = RegressionPin(trace_id=str(trace["trace_id"]), trace_ref=content_hash(trace),
                            expected_verdict="DENY", expected_sequence=("DENY",))
        candidate = support.conditions()[1]
        fallback = support.kernel_registry().get(support.KERNEL_NO_TAINT_FLOOR)
        results = check_pins(
            (pin,), traces, candidate, fallback, support.manifests(),
            inputs_for=lambda t: support.banking_scenario().get("inputs", {}),
            kernel_for=lambda t: support.kernel_registry().get(support.KERNEL_PINNED),
        )
        self.assertEqual(results[0]["kernel"], support.KERNEL_PINNED)  # the actual, not fallback


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestCandidateRealKernel(unittest.TestCase):
    def test_regress_reference_to_real_transition(self) -> None:
        # a trace recorded under the REFERENCE kernel, regressed under --kernel
        # axor-core@X, must run the REAL governor
        bundle, traces = _slice_bundle()
        trace = _a_governed_trace(bundle, traces)
        version = real_kernel_version()
        candidate = {
            "id": "real", "enforcement": "on", "kernel": version,
            "policy": {"profile": "strict", "trust_model": "content-ledger"},
        }
        kernel = resolve_candidate_kernel_for_trace(bundle, trace, candidate, candidate_version=version)
        self.assertIsInstance(kernel, AxorKernel)
        self.assertEqual(kernel.version, version)

    def test_regress_candidate_allowlist_expands_per_trace_scenario(self) -> None:
        # a candidate condition with an input-backed allowlist expands against the
        # TRACE's own scenario inputs, not a single baked expansion
        bundle, traces = _slice_bundle()
        # two synthetic traces citing two scenarios with different known_ibans
        s_a = {**support.banking_scenario(), "name": "scn-a",
               "inputs": {"landlord_iban": "IBAN_A", "known_ibans": ["IBAN_A"]}}
        s_b = {**support.banking_scenario(), "name": "scn-b",
               "inputs": {"landlord_iban": "IBAN_B", "known_ibans": ["IBAN_B"]}}
        bundle = {**bundle, "scenarios": [s_a, s_b]}
        version = real_kernel_version()
        candidate = {"id": "real", "enforcement": "on", "kernel": version,
                     "policy": {"profile": "strict", "trust_model": "content-ledger",
                                "allowlist": ["$inputs.known_ibans"]}}

        def enums(k):
            vps = k.config.get("value_policies", {})
            return [v for vp in vps.values() for arg in vp.values() for v in arg["enum"]]

        k_a = resolve_candidate_kernel_for_trace(
            bundle, {"trial": {"scenario_id": "scn-a", "condition_id": "governed"}},
            candidate, candidate_version=version)
        k_b = resolve_candidate_kernel_for_trace(
            bundle, {"trial": {"scenario_id": "scn-b", "condition_id": "governed"}},
            candidate, candidate_version=version)
        self.assertIn("IBAN_A", enums(k_a))
        self.assertNotIn("IBAN_B", enums(k_a))
        self.assertIn("IBAN_B", enums(k_b))

    def test_regress_kernel_override_is_the_kernel_that_actually_runs(self) -> None:
        # end-to-end through the CLI: regress --kernel axor-core@X reports that
        # exact kernel as the one that ran, not the recorded reference kernel
        from lab_runner.cli import main

        bundle, traces = _slice_bundle()
        trace = _a_governed_trace(bundle, traces)
        version = real_kernel_version()
        with tempfile.TemporaryDirectory() as tmp:
            bdir = Path(tmp) / "bundle"
            write_bundle_dir(bdir, bundle, traces)
            pins = Path(tmp) / "pins.json"
            pins.write_text(json.dumps([{
                "trace_id": str(trace["trace_id"]), "trace_ref": content_hash(trace),
                "expected_verdict": "DENY", "expected_sequence": ["DENY"],
            }]))
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["regress", str(bdir), "--pins", str(pins),
                      "--condition", "governed", "--kernel", version])
            printed = out.getvalue()
            self.assertIn(version, printed)  # the override is the kernel reported


if __name__ == "__main__":
    unittest.main()
