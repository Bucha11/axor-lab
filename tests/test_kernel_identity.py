"""Kernel identity closure (review r16, P0).

A real-kernel pin (`axor-core@X`) is satisfied ONLY by the exact installed
build. The reference kernel is NEVER substituted under a real-kernel version
label: a missing or mismatched build is UnknownKernelError, which replay surfaces
as REPLAY_UNSUPPORTED_KERNEL and is never counted as bit-identical.
"""

from __future__ import annotations

import unittest

from lab_runner import (
    REPLAY_UNSUPPORTED_KERNEL,
    KernelRegistry,
    ScriptedAgent,
    default_registry,
    real_kernel_version,
    replay_bundle,
    run_trial,
)
from lab_runner.axor_backend import AxorKernel, axor_available, resolve_kernel
from lab_runner.errors import UnknownKernelError
from tests import support


class TestResolveKernelIdentity(unittest.TestCase):
    def test_missing_real_kernel_is_unsupported_not_reference(self) -> None:
        # a real-kernel pin with no matching installed build must raise, never
        # fall through to a reference Kernel(version="axor-core@...")
        registry = default_registry(("axor-core@9.9.9-not-installed",))
        with self.assertRaises(UnknownKernelError):
            resolve_kernel("axor-core@9.9.9-not-installed", support.manifests(), None, registry)

    def test_wrong_installed_axor_core_version_is_unsupported(self) -> None:
        # even WITH axor-core installed, a DIFFERENT pinned build is unsupported —
        # we never run 0.9.2 for a bundle pinning 0.4.2
        registry = default_registry(("axor-core@0.4.2",))
        with self.assertRaises(UnknownKernelError):
            resolve_kernel("axor-core@0.4.2", support.manifests(), None, registry)

    def test_reference_version_still_resolves_to_the_reference_kernel(self) -> None:
        registry = default_registry(("reference_taint_floor_kernel",))
        kernel = resolve_kernel("reference_taint_floor_kernel", support.manifests(), None, registry)
        self.assertFalse(isinstance(kernel, AxorKernel))

    @unittest.skipUnless(axor_available(), "axor-core not installed")
    def test_exact_installed_build_resolves_to_the_real_kernel(self) -> None:
        version = real_kernel_version()
        registry = default_registry((version,))
        kernel = resolve_kernel(version, support.manifests(), None, registry)
        self.assertIsInstance(kernel, AxorKernel)


class TestReplayNeverSubstitutesKernel(unittest.TestCase):
    def _real_kernel_trace(self) -> dict:
        # a trace whose condition pins a real-kernel build that is NOT installed
        trace = run_trial(
            support.banking_scenario(), support.manifests(), support.conditions()[1],
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="r", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
        ).trace
        # relabel its condition + producer to an uninstalled real-kernel build
        trace = dict(trace)
        return trace

    def test_verify_never_reports_bit_identical_under_substituted_kernel(self) -> None:
        # build a bundle whose condition pins an uninstalled axor-core build; the
        # trace was produced by the reference kernel, but the pin says axor-core.
        # Replay must report UNSUPPORTED_KERNEL, never bit-identical.
        import copy

        from lab_contracts import build_bundle, content_hash

        scenario = support.banking_scenario()
        conditions = copy.deepcopy(support.conditions())
        for c in conditions:
            c["kernel"] = "axor-core@9.9.9-not-installed"
        outcome = run_trial(
            scenario, support.manifests(), conditions[1],
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="r", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
        )
        trace = copy.deepcopy(outcome.trace)
        trace["trial"]["condition_id"] = str(conditions[1]["id"])
        trace["producer"]["kernel_version"] = "axor-core@9.9.9-not-installed"
        traces = {str(trace["trace_id"]): trace}
        trial = {
            "trial_id": content_hash(trace), "scenario_id": str(scenario["name"]),
            "condition_id": str(conditions[1]["id"]), "seed": "s000", "repeat_index": 0,
            "status": "completed", "trace_ref": content_hash(trace),
        }
        bundle = build_bundle(
            bundle_id="b_sub", created="2026-07-21T00:00:00+00:00", scenarios=[scenario],
            conditions=conditions, tool_manifests=list(support.manifests().values()),
            environment={"kernel_versions": ["axor-core@9.9.9-not-installed"],
                         "model": {"provider": "scripted", "id": "scripted",
                                   "inference_params": {"experiment_id": "x"}}},
            trials=[trial], aggregates=[], traces=traces,
        )
        versions = ("axor-core@9.9.9-not-installed",)
        kernels = {k.version: k for k in default_registry(versions).kernels}
        report = replay_bundle(bundle, traces, kernels)
        self.assertFalse(report.bit_identical)
        self.assertEqual(report.status_of()[str(trace["trace_id"])], REPLAY_UNSUPPORTED_KERNEL)


if __name__ == "__main__":
    unittest.main()
