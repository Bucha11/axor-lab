"""The REAL axor-core kernel integration (review P0.2).

Lab drives the actual production `axor_core.governor.ToolCallGovernor` — not a
reimplementation — when a condition pins a real axor-core version. These prove
the real kernel denies the banking exfiltration, allows the faithful payment,
and that a real-kernel trace replays bit-identically through the same governor.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_contracts import (
    compiled_governor_config,
    condition_config_hash,
    content_hash,
    executable_config_hash,
)
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


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestRealKernelRepin(unittest.TestCase):
    """`--real-kernel` must repin EVERY condition and produce a bundle that
    verifies — not just the enforcement-on ones (review r13)."""

    def test_repin_covers_baseline_and_bundle_verifies(self) -> None:
        from lab_contracts import build_bundle, verify_bundle
        from lab_runner import run_experiment_suite
        from lab_runner.cli import _environment, _repin_to_real_kernel
        from lab_runner.experiment_file import ResolvedExperiment

        version = real_kernel_version()
        conditions = support.conditions()  # ungoverned(off) + governed(on), reference kernel
        self.assertNotEqual(conditions[0]["kernel"], version)  # baseline starts on reference

        _repin_to_real_kernel({"experiment": {"id": "e_real", "conditions": conditions}})
        # ALL conditions — the enforcement-off baseline included — now on the real
        # kernel, so the compare isolates enforcement and the bundle has ONE kernel
        self.assertTrue(all(c["kernel"] == version for c in conditions))
        self.assertTrue(all(
            c["config_hash"] == condition_config_hash(version, c.get("policy")) for c in conditions
        ))

        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), conditions,
            support.kernel_registry(), repeats=2, run_id="r_repin", agent=ATTACK_ALWAYS,
        )
        resolved = ResolvedExperiment(
            experiment={"id": "e_real", "agent_ref": "scripted", "repeats": 2},
            scenarios=(support.banking_scenario(),), manifests=support.manifests(),
            conditions=tuple(conditions), agent=None, kernel_registry=support.kernel_registry(),
        )
        env = _environment(resolved, "scripted")
        # a SINGLE kernel_version, never a comma-joined pseudo-value verify rejects
        self.assertEqual(env["kernel_version"], version)

        bundle = build_bundle(
            bundle_id="b_repin", created="2026-07-20T12:00:00+00:00",
            scenarios=[support.banking_scenario()], conditions=conditions,
            tool_manifests=list(support.manifests().values()), environment=env,
            trials=result.trials, aggregates=[], traces=result.traces,
        )
        verify_bundle(bundle, result.traces)  # must NOT raise (was: mixed-kernel env)

    def test_environment_omits_kernel_version_for_a_mixed_kernel_bundle(self) -> None:
        # a legitimately mixed-kernel bundle omits the global kernel_version rather
        # than writing a comma-joined value that fails verify AFTER the run
        from lab_runner.cli import _environment
        from lab_runner.experiment_file import ResolvedExperiment

        mixed = support.conditions()
        mixed[1] = {**mixed[1], "kernel": real_kernel_version()}  # two distinct kernels
        resolved = ResolvedExperiment(
            experiment={"id": "e_mixed", "agent_ref": "scripted", "repeats": 1},
            scenarios=(support.banking_scenario(),), manifests=support.manifests(),
            conditions=tuple(mixed), agent=None, kernel_registry=support.kernel_registry(),
        )
        env = _environment(resolved, "scripted")
        self.assertNotIn("kernel_version", env)


class TestExecutableConfigHash(unittest.TestCase):
    """The carry-over key is the hash of the COMPILED governor config — manifests
    and their untrusted-field taint patterns included, not just kernel+policy
    (review r16). This runs regardless of axor-core (the hash is pure)."""

    def test_executable_config_hash_equals_hash_of_compiled_governor_config(self) -> None:
        kernel = support.KERNEL_PINNED
        policy = {"profile": "strict"}
        manifests = list(support.manifests().values())
        h = executable_config_hash(kernel, policy, manifests)
        # it is literally the content hash of the canonical compiled config...
        self.assertEqual(h, content_hash(compiled_governor_config(kernel, policy, manifests)))
        # ...and that compiled config IS the config the runner's governor runs
        # (egress sinks / untrusted sources / driving args all agree)
        canon = compiled_governor_config(kernel, policy, manifests)
        gc = governor_config(support.manifests(), policy)
        self.assertEqual(set(canon["egress_sinks"]), gc["egress_sinks"])
        self.assertEqual(set(canon["untrusted_sources"]), gc["untrusted_sources"])
        self.assertEqual(canon["driving_args"], gc["driving_args"])
        # it binds more than kernel+policy — a different identity than the plain anchor
        self.assertNotEqual(h, condition_config_hash(kernel, policy))

    def test_executable_config_hash_changes_with_untrusted_fields(self) -> None:
        kernel = support.KERNEL_PINNED
        policy = {"profile": "strict"}
        manifests = list(support.manifests().values())
        before = executable_config_hash(kernel, policy, manifests)
        # taint a DIFFERENT result field — the governor now mints taint from a new
        # source, so it governs differently; the old hash (effect+args_schema only)
        # missed this entirely
        tampered = support.deep({m["id"]: m for m in manifests})["read_txns"]
        tampered["untrusted_fields"] = ["result.transactions[].amount"]
        new_manifests = [tampered if m["id"] == "read_txns" else m for m in manifests]
        after = executable_config_hash(kernel, policy, new_manifests)
        self.assertNotEqual(before, after)

        # and dropping untrusted_fields entirely also moves the hash
        stripped = support.deep({m["id"]: m for m in manifests})["read_txns"]
        stripped.pop("untrusted_fields", None)
        dropped = [stripped if m["id"] == "read_txns" else m for m in manifests]
        self.assertNotEqual(before, executable_config_hash(kernel, policy, dropped))

    def test_input_backed_allowlist_expands_in_compiled_config(self) -> None:
        # $inputs.known_ibans in the policy allowlist expands to the concrete IBANs
        # against the scenario inputs — parity with the reference kernel's
        # _resolve_allowlist; the literal "$inputs.known_ibans" must NOT survive
        policy = {"profile": "strict", "allowlist": ["$inputs.known_ibans"]}
        inputs = support.banking_scenario()["inputs"]
        compiled = compiled_governor_config(
            support.KERNEL_PINNED, policy, list(support.manifests().values()), inputs
        )
        enums = [vp for vp in compiled["value_policies"].values()]
        flat = [v for vp in enums for arg in vp.values() for v in arg["enum"]]
        self.assertIn(support.LANDLORD_IBAN, flat)
        self.assertNotIn("$inputs.known_ibans", flat)


class TestParametricVsRuntimeConfigHash(unittest.TestCase):
    """The parametric carry-over identity (symbolic $inputs) is NOT the concrete
    per-scenario runtime config identity (review r17). Naming the parametric hash
    "the full runtime config" was dishonest: two scenarios share it yet govern
    differently once an input-backed allowlist expands."""

    def _manifests(self):
        return list(support.manifests().values())

    def test_parametric_policy_hash_differs_from_runtime_config_hash(self) -> None:
        from lab_contracts import parametric_policy_hash, runtime_config_hash

        kernel = support.KERNEL_PINNED
        policy = {"profile": "strict", "allowlist": ["$inputs.known_ibans"]}
        inputs = support.banking_scenario()["inputs"]
        parametric = parametric_policy_hash(kernel, policy, self._manifests())
        runtime = runtime_config_hash(kernel, policy, self._manifests(), inputs)
        # the parametric hash keeps $inputs symbolic; the runtime hash expands it —
        # for an input-backed allowlist they MUST differ
        self.assertNotEqual(parametric, runtime)

    def test_runtime_config_hash_changes_with_scenario_inputs(self) -> None:
        from lab_contracts import parametric_policy_hash, runtime_config_hash

        kernel = support.KERNEL_PINNED
        policy = {"profile": "strict", "allowlist": ["$inputs.known_ibans"]}
        ms = self._manifests()
        h_a = runtime_config_hash(kernel, policy, ms, {"known_ibans": ["IBAN_A"]})
        h_b = runtime_config_hash(kernel, policy, ms, {"known_ibans": ["IBAN_B"]})
        self.assertNotEqual(h_a, h_b)  # different concrete allowlists → different runtime
        # but the parametric identity is stable across those scenarios
        p_a = parametric_policy_hash(kernel, policy, ms)
        p_b = parametric_policy_hash(kernel, policy, ms)
        self.assertEqual(p_a, p_b)

    def test_runtime_hash_equals_parametric_when_no_input_refs(self) -> None:
        # a policy with a LITERAL allowlist (no $inputs) has nothing to expand, so
        # the parametric and runtime identities coincide
        from lab_contracts import parametric_policy_hash, runtime_config_hash

        kernel = support.KERNEL_PINNED
        policy = {"profile": "strict", "allowlist": ["DE00LITERAL"]}
        ms = self._manifests()
        self.assertEqual(
            parametric_policy_hash(kernel, policy, ms),
            runtime_config_hash(kernel, policy, ms, {"known_ibans": ["x"]}),
        )


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestRealKernelInputAllowlist(unittest.TestCase):
    def test_real_kernel_expands_allowlist_input_refs(self) -> None:
        # a real-kernel condition whose allowlist is input-backed must enforce the
        # CONCRETE destinations — the governor config carries the expanded IBANs,
        # not the symbolic "$inputs.known_ibans" the governor could never match
        version = real_kernel_version()
        policy = {"profile": "strict", "trust_model": "content-ledger",
                  "allowlist": ["$inputs.known_ibans"]}
        inputs = support.banking_scenario()["inputs"]
        kernel = resolve_kernel(
            version, support.manifests(), policy, KernelRegistry(kernels=()), inputs,
        )
        self.assertIsInstance(kernel, AxorKernel)
        vps = kernel.config.get("value_policies", {})
        flat = [v for vp in vps.values() for arg in vp.values() for v in arg["enum"]]
        self.assertIn(support.LANDLORD_IBAN, flat)
        self.assertNotIn("$inputs.known_ibans", flat)


if __name__ == "__main__":
    unittest.main()
