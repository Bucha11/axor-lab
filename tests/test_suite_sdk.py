"""The Suite SDK — Suite Platform RFC §6/§12.

The manifest is the core abstraction; these tests are about it being genuinely
EXECUTABLE and genuinely CHECKED. The validation tests matter most: each one is
a manifest that is structurally valid JSON Schema and still describes something
incoherent, which is exactly the class of error a Builder will produce.
"""

from __future__ import annotations

import copy
import unittest

from lab_contracts import validate_artifact
from lab_runner.invariants import STATUS_PASSED
from lab_suite import SuiteValidationError, builtin_registry, run_suite
from lab_suite.manifest import resolve_suite, validate_manifest

ENVIRONMENT = {"model": {"provider": "scripted", "id": "scripted@0.6"}}
CREATED = "2026-08-02T00:00:00+00:00"


def _budget() -> dict[str, object]:
    return copy.deepcopy(builtin_registry().get("budget").manifest())


class TestRegistry(unittest.TestCase):
    def test_the_launch_set_is_three(self) -> None:
        self.assertEqual(builtin_registry().ids(), ("agentdojo", "blank", "budget"))

    def test_every_builtin_manifest_is_valid(self) -> None:
        registry = builtin_registry()
        for suite_id in registry.ids():
            with self.subTest(suite=suite_id):
                self.assertEqual(registry.validate(suite_id), [])

    def test_the_catalog_renders_one_card_per_suite(self) -> None:
        cards = builtin_registry().catalog()
        self.assertEqual(len(cards), 3)
        for card in cards:
            self.assertTrue(card["name"])
            self.assertTrue(card["description"])

    def test_an_unknown_suite_is_a_typed_error(self) -> None:
        from lab_suite.errors import SuiteNotFound
        with self.assertRaises(SuiteNotFound):
            builtin_registry().get("performance")


class TestManifestValidation(unittest.TestCase):
    """Each case is schema-valid and semantically wrong."""

    def test_a_suite_with_no_scenarios_is_refused(self) -> None:
        manifest = _budget()
        manifest.pop("scenarios")
        errors = validate_manifest(manifest)
        self.assertTrue(any("no scenarios" in e for e in errors), errors)

    def test_governance_declared_without_arms_is_refused(self) -> None:
        """A suite that thinks it is measuring governance and is not."""
        manifest = _budget()
        manifest["capabilities"] = ["governance"]
        errors = validate_manifest(manifest)
        self.assertTrue(any("nothing would be governed" in e for e in errors), errors)

    def test_arms_without_declaring_governance_are_refused(self) -> None:
        """The converse: a kernel dependency hidden from the capability list."""
        manifest = _budget()
        manifest["execution"]["conditions"] = [  # type: ignore[index]
            {"schema_version": "condition/v1", "id": "governed", "enforcement": "on",
             "kernel": "reference_taint_floor_kernel"},
        ]
        errors = validate_manifest(manifest)
        self.assertTrue(any("must be declared, not implied" in e for e in errors), errors)

    def test_a_comparison_test_on_one_arm_is_refused(self) -> None:
        """The most misleading manifest an author can write: it looks like it
        will produce a comparison, and it cannot."""
        manifest = _budget()
        manifest["evaluation"]["aggregations"][0]["test"] = "mcnemar"  # type: ignore[index]
        errors = validate_manifest(manifest)
        self.assertTrue(any("a comparison needs 2" in e for e in errors), errors)

    def test_random_seeds_with_matched_pairs_are_refused(self) -> None:
        manifest = copy.deepcopy(builtin_registry().get("agentdojo").manifest())
        manifest["execution"]["seed_policy"] = "random"  # type: ignore[index]
        errors = validate_manifest(manifest)
        self.assertTrue(any("forfeits the paired trials" in e for e in errors), errors)

    def test_an_aggregation_over_an_undeclared_metric_is_refused(self) -> None:
        manifest = _budget()
        manifest["evaluation"]["aggregations"].append(  # type: ignore[index,union-attr]
            {"metric": "hallucination_rate", "fn": "rate"})
        errors = validate_manifest(manifest)
        self.assertTrue(any("does not declare" in e for e in errors), errors)

    def test_several_agents_under_a_single_topology_are_refused(self) -> None:
        manifest = _budget()
        manifest["agents"] = [{"ref": "a"}, {"ref": "b"}]  # type: ignore[index]
        errors = validate_manifest(manifest)
        self.assertTrue(any("say which" in e for e in errors), errors)

    def test_a_multi_agent_topology_validates_but_will_not_run(self) -> None:
        """The manifest is authorable and valid (the platform is agent-count
        agnostic by design), but executing a topology needs a scheduler that
        does not exist yet — so a RUN is refused rather than silently executing
        a single agent and mislabelling the artifact a planner_workers run."""
        from lab_suite import run_suite
        from lab_suite.errors import SuiteError
        from lab_suite.manifest import topology_execution_error

        manifest = _budget()
        manifest["agents"] = [{"ref": "planner", "role": "planner"},
                              {"ref": "worker", "role": "worker"}]
        manifest["topology"] = {"kind": "planner_workers"}
        # VALID — authoring is unblocked
        self.assertEqual(validate_manifest(manifest), [])
        # but NOT executable
        self.assertIsNotNone(topology_execution_error(manifest))
        with self.assertRaises(SuiteError) as ctx:
            run_suite(manifest, run_id="x",
                      suite=builtin_registry().get("budget"))
        self.assertIn("planner_workers", str(ctx.exception))

    def test_single_topology_has_no_execution_error(self) -> None:
        from lab_suite.manifest import topology_execution_error
        self.assertIsNone(topology_execution_error(_budget()))

    def test_a_stand_in_agent_may_not_advertise_a_model(self) -> None:
        """The Builder renders `agents[]` verbatim and the artifact carries it,
        so `{"ref": "scripted@0.6", "model": "claude-opus-4-8"}` presents a
        model as the thing that produced the numbers while the trials are run
        by a hash of (scenario, seed). Nothing reads the field — which is why it
        has to be refused rather than ignored."""
        manifest = _budget()
        manifest["agents"] = [{"ref": "scripted@0.6", "model": "claude-opus-4-8"}]
        errors = validate_manifest(manifest)
        self.assertTrue(any("deterministic stand-in" in e for e in errors), errors)

    def test_a_real_agent_ref_keeps_its_identity(self) -> None:
        """The same fields are how a runtime-executed suite records WHICH model
        it ran — the rule is about a stand-in claiming one, not about the
        fields."""
        manifest = _budget()
        manifest["agents"] = [
            {"ref": "gpt-4o@2026-05", "provider": "openai", "model": "gpt-4o"},
        ]
        errors = validate_manifest(manifest)
        self.assertFalse([e for e in errors if "stand-in" in e], errors)

    def test_no_builtin_pins_an_agent_at_all(self) -> None:
        """The executor is bound at dispatch — the Run panel's dropdown of
        connected runtimes — not in the manifest. A built-in that pinned
        `scripted@0.6` made the Builder present a fixture as an agent choice;
        `agents[]` remains in the schema for multi-agent topologies, where the
        entries are roles, not the executor."""
        for suite_id in builtin_registry().ids():
            manifest = builtin_registry().get(suite_id).manifest()
            self.assertNotIn("agents", manifest, suite_id)
            self.assertNotIn("topology", manifest, suite_id)

    def test_schema_errors_suppress_semantic_noise(self) -> None:
        """A manifest that fails the schema reports THAT, not a cascade of
        semantic complaints about fields the schema already rejected."""
        manifest = _budget()
        manifest["execution"] = "every tuesday"  # type: ignore[assignment]
        errors = validate_manifest(manifest)
        self.assertTrue(errors)
        self.assertTrue(all(e.startswith("[schema]") for e in errors), errors)

    def test_resolve_raises_with_every_error_at_once(self) -> None:
        manifest = _budget()
        manifest.pop("scenarios")
        with self.assertRaises(SuiteValidationError) as ctx:
            resolve_suite(manifest)
        self.assertTrue(ctx.exception.errors)


class TestResolution(unittest.TestCase):
    def test_scenario_refs_are_frozen_into_the_run(self) -> None:
        """A ref resolves to its BODY, so a later registry change cannot
        retroactively alter what a finished artifact says was run."""
        manifest = _budget()
        scenario = manifest.pop("scenarios")[0]  # type: ignore[index]
        manifest["scenario_refs"] = ["budget-spend-summary-01"]
        registry = {"budget-spend-summary-01": scenario}
        resolved = resolve_suite(manifest, registry)
        self.assertEqual(len(resolved.scenarios), 1)
        registry["budget-spend-summary-01"]["task"] = "MUTATED"  # type: ignore[index]
        self.assertNotEqual(resolved.scenarios[0]["task"], "MUTATED")

    def test_a_dangling_ref_is_refused(self) -> None:
        manifest = _budget()
        manifest.pop("scenarios")
        manifest["scenario_refs"] = ["nope"]
        self.assertTrue(any("resolves to nothing" in e for e in validate_manifest(manifest)))

    def test_trial_count_accounts_for_arms(self) -> None:
        resolved = resolve_suite(_budget())
        self.assertEqual(resolved.trial_count(), 5)
        self.assertFalse(resolved.governed)
        agentdojo = resolve_suite(builtin_registry().get("agentdojo").manifest())
        self.assertTrue(agentdojo.governed)
        self.assertEqual(
            agentdojo.trial_count(),
            len(agentdojo.scenarios) * 2 * agentdojo.repeats,
        )


class TestExecution(unittest.TestCase):
    def _run(self, suite_id: str):
        registry = builtin_registry()
        suite = registry.get(suite_id)
        return suite, run_suite(suite.manifest(), run_id=f"r_{suite_id}", suite=suite)

    def test_every_builtin_runs_and_produces_a_valid_artifact(self) -> None:
        for suite_id in builtin_registry().ids():
            with self.subTest(suite=suite_id):
                _, run = self._run(suite_id)
                self.assertTrue(run.trials)
                self.assertEqual({str(t["status"]) for t in run.trials}, {"completed"})
                artifact = run.artifact(f"a_{suite_id}", CREATED, ENVIRONMENT)
                self.assertEqual(validate_artifact(artifact, "artifact"), [])

    def test_the_artifact_carries_the_suite_that_produced_it(self) -> None:
        _, run = self._run("budget")
        artifact = run.artifact("a", CREATED, ENVIRONMENT)
        self.assertEqual(artifact["suite"]["id"], "budget")  # type: ignore[index]

    def test_include_traces_drives_the_reproducibility_claim(self) -> None:
        """`artifact.include_traces` was read by nothing while the artifact
        hardcoded exact_replay — a claim over a body carrying no traces. A suite
        that declares it false produces a metrics-only artifact that says so."""
        suite = builtin_registry().get("budget")
        governed = run_suite(suite.manifest(), run_id="r", suite=suite)
        self.assertEqual(
            governed.artifact("a", CREATED, ENVIRONMENT)["reproduce"]["reproducibility"],
            "exact_replay",
        )
        manifest = copy.deepcopy(suite.manifest())
        manifest["artifact"]["include_traces"] = False  # type: ignore[index]
        metrics_only = run_suite(manifest, run_id="r", suite=suite)
        self.assertEqual(
            metrics_only.artifact("a", CREATED, ENVIRONMENT)["reproduce"]["reproducibility"],
            "not_reproducible",
        )

    def test_only_declared_aggregations_are_computed(self) -> None:
        """The platform must not helpfully add a comparison the suite never
        asked for — that is how McNemar became a default instead of a request."""
        suite, run = self._run("budget")
        declared = {
            str(a["metric"]) for a in suite.manifest()["evaluation"]["aggregations"]  # type: ignore[index,union-attr]
        }
        computed = {str(a["metric"]) for a in run.aggregates}
        self.assertEqual(computed, declared)
        self.assertTrue(all("test" not in a for a in run.aggregates))

    def test_a_suite_metric_cannot_overwrite_a_platform_metric(self) -> None:
        suite, _ = self._run("budget")

        class Liar(type(suite)):  # type: ignore[misc]
            def metrics_for(self, outcome, scenario):  # noqa: ANN001, ANN201
                return {"duration_ms": 0.0, "reads": 99}

        run = run_suite(suite.manifest(), run_id="r_liar", suite=Liar())
        metrics: dict[str, object] = run.trials[0]["metrics"]  # type: ignore[assignment]
        self.assertGreater(float(metrics["duration_ms"]), 0.0)  # type: ignore[arg-type]
        self.assertEqual(metrics["reads"], 99)

    def test_a_suite_metric_reaches_the_trial(self) -> None:
        _, run = self._run("budget")
        self.assertIn("reads", run.trials[0]["metrics"])  # type: ignore[operator]

    def test_the_suites_own_regressions_are_checked(self) -> None:
        """Budget ships two invariants and both are evaluable.

        It used to ship a third, over `cost_usd`, which errored on every run
        this repo can perform — nothing here measures cost. Erroring on an
        absent metric is correct and is pinned in
        `test_platform_slice_e2e.py`; a BUILT-IN that can never satisfy its own
        invariant is a different thing, and it was one.

        `RG-budget-reads` bounds the suite's OWN metric, so this also pins that
        a suite-defined metric reaches a regression rule at all."""
        _, run = self._run("budget")
        by_id = {
            str(regression["id"]): result
            for regression, result in zip(run.resolved.regressions, run.invariants)
        }
        self.assertEqual(by_id["RG-budget-latency"].status, STATUS_PASSED)
        self.assertEqual(by_id["RG-budget-reads"].status, STATUS_PASSED)
        artifact = run.artifact("a", CREATED, ENVIRONMENT)
        statuses = {
            str(r["id"]): r["history"][0]["status"]  # type: ignore[index]
            for r in artifact["regressions"]  # type: ignore[union-attr]
        }
        self.assertEqual(statuses["RG-budget-latency"], STATUS_PASSED)

    def test_an_ungoverned_suite_still_claims_exact_replay(self) -> None:
        """Enforcement is off, but the kernel observed — so the verdicts it
        recorded replay bit-identically."""
        _, run = self._run("budget")
        artifact = run.artifact("a", CREATED, ENVIRONMENT)
        self.assertEqual(
            artifact["reproduce"]["reproducibility"], "exact_replay",  # type: ignore[index]
        )

    def test_a_governed_suite_claims_exact_replay(self) -> None:
        _, run = self._run("agentdojo")
        artifact = run.artifact("a", CREATED, ENVIRONMENT)
        self.assertEqual(artifact["reproduce"]["reproducibility"], "exact_replay")  # type: ignore[index]


class TestAgentDojoReproducesTheComparison(unittest.TestCase):
    def test_governance_suppresses_the_attack_without_costing_utility(self) -> None:
        """The Table-1 shape, produced through the suite SDK and the general
        loop rather than the bespoke slice runner."""
        suite = builtin_registry().get("agentdojo")
        run = run_suite(suite.manifest(), run_id="r_dojo", suite=suite)
        by = {(str(a["metric"]), str(a["condition_id"])): a for a in run.aggregates}
        self.assertGreater(float(by[("ASR", "ungoverned")]["estimate"]), 0.0)  # type: ignore[arg-type]
        self.assertEqual(float(by[("ASR", "governed")]["estimate"]), 0.0)  # type: ignore[arg-type]
        self.assertEqual(
            float(by[("task_success", "ungoverned")]["estimate"]),  # type: ignore[arg-type]
            float(by[("task_success", "governed")]["estimate"]),  # type: ignore[arg-type]
        )

    def test_a_rate_is_only_computed_over_a_recorded_boolean(self) -> None:
        """No name-based substitution: a rate over something never recorded as a
        boolean yields no aggregate rather than a guessed one."""
        manifest = copy.deepcopy(builtin_registry().get("budget").manifest())
        manifest["evaluation"]["metrics"].append(  # type: ignore[index,union-attr]
            {"name": "consensus", "label": "Consensus", "kind": "boolean",
             "source": "trial_metric", "from": "consensus"})
        manifest["evaluation"]["aggregations"].append(  # type: ignore[index,union-attr]
            {"metric": "consensus", "fn": "rate", "unit_of_analysis": "trial"})
        run = run_suite(manifest, run_id="r_c", suite=builtin_registry().get("budget"))
        self.assertNotIn("consensus", {str(a["metric"]) for a in run.aggregates})


if __name__ == "__main__":
    unittest.main()
