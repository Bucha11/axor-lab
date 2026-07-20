"""The REAL axor-core kernel integration (review P0.2).

Lab drives the actual production `axor_core.governor.ToolCallGovernor` — not a
reimplementation — when a condition pins a real axor-core version. These prove
the real kernel denies the banking exfiltration, allows the faithful payment,
and that a real-kernel trace replays bit-identically through the same governor.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_contracts import condition_config_hash
from lab_runner import (
    AxorKernel,
    ScriptedAgent,
    axor_available,
    governor_config,
    real_kernel_version,
    replay_trace,
    resolve_kernel,
    run_experiment,
    run_trial,
)
from lab_runner.kernel import KernelRegistry

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)
FAITHFUL_ALWAYS = ScriptedAgent(attack_rate=0.0)


def _real_condition() -> dict[str, object]:
    version = real_kernel_version()
    return {
        "schema_version": "condition/v1", "id": "governed", "label": "governed (axor-core)",
        "enforcement": "on", "kernel": version,
        "policy": {"profile": "strict", "trust_model": "content-ledger"},
        "config_hash": condition_config_hash(version, {"profile": "strict", "trust_model": "content-ledger"}),
    }


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestRealKernelIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = support.banking_scenario()
        self.manifests = support.manifests()
        self.condition = _real_condition()
        self.kernel = AxorKernel(
            version=str(self.condition["kernel"]),
            config=governor_config(self.manifests, self.condition.get("policy")),
        )

    def test_resolve_selects_the_real_kernel_for_an_axor_version(self) -> None:
        kernel = resolve_kernel(
            real_kernel_version(), self.manifests, None, KernelRegistry(kernels=()),
        )
        self.assertIsInstance(kernel, AxorKernel)

    def test_real_governor_denies_the_exfiltration(self) -> None:
        outcome = run_trial(
            self.scenario, self.manifests, self.condition, self.kernel,
            run_id="r_real", seed="s000", repeat_index=0, agent=ATTACK_ALWAYS,
        )
        decision = next(e for e in outcome.trace["events"] if e.get("type") == "gate_decision")
        self.assertEqual(decision["decision"]["verdict"], "DENY")
        # the reason comes from the REAL governor, not a Lab reimplementation
        self.assertIn("axor-core governor", decision["decision"]["reason"])
        self.assertFalse(outcome.violation)  # DENY → attack did not reach an executed sink

    def test_real_governor_allows_the_faithful_payment(self) -> None:
        outcome = run_trial(
            self.scenario, self.manifests, self.condition, self.kernel,
            run_id="r_real", seed="s000", repeat_index=0, agent=FAITHFUL_ALWAYS,
        )
        decision = next(e for e in outcome.trace["events"] if e.get("type") == "gate_decision")
        self.assertEqual(decision["decision"]["verdict"], "ALLOW")
        self.assertTrue(outcome.task_success)

    def test_real_kernel_trace_replays_bit_identically(self) -> None:
        outcome = run_trial(
            self.scenario, self.manifests, self.condition, self.kernel,
            run_id="r_real", seed="s000", repeat_index=0, agent=ATTACK_ALWAYS,
        )
        self.assertEqual(support.schema_errors(outcome.trace, "trace"), [])
        recomputed, matches = replay_trace(
            outcome.trace, self.condition, self.kernel, self.manifests,
            self.scenario["inputs"],
        )
        self.assertTrue(matches)  # governor re-driven over frozen registrations
        self.assertEqual(recomputed[0]["verdict"], "DENY")

    def test_compare_run_shows_the_real_governance_delta(self) -> None:
        ungoverned = support.conditions()[0]  # reference kernel, enforcement off
        registry = KernelRegistry(kernels=support.kernel_registry().kernels)
        result = run_experiment(
            self.scenario, self.manifests, [ungoverned, self.condition], registry,
            repeats=8, run_id="r_real_cmp", agent=ATTACK_ALWAYS,
        )
        pairs = result.pairs("ungoverned", "governed", metric="ASR")
        self.assertTrue(all(base for base, _ in pairs))       # ungoverned: all breach
        self.assertTrue(all(not treated for _, treated in pairs))  # real kernel: none breach


if __name__ == "__main__":
    unittest.main()
