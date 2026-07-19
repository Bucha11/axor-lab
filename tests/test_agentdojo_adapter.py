"""AgentDojo import → scenario/v1 (MVP contract item 2, bench-format.md).

Every materialized scenario must be schema-valid AND pass author-time
semantic validation; the undefended/defended comparison maps to
ungoverned/governed conditions; a full suite runs end-to-end and shows the
governance delta.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_adapters import available_suites, build_experiment_document, import_suite, manifests
from lab_adapters.errors import UnknownSuiteError
from lab_contracts import validate_scenario
from lab_runner import default_registry, run_experiment_suite
from lab_runner.experiment_file import resolve


class TestAgentDojoAdapter(unittest.TestCase):
    def test_banking_suite_is_available(self) -> None:
        self.assertIn("banking", available_suites())

    def test_unknown_suite_is_rejected(self) -> None:
        with self.assertRaises(UnknownSuiteError):
            import_suite("no-such-suite")

    def test_every_scenario_is_schema_valid_and_runnable(self) -> None:
        scenarios = import_suite("banking")
        self.assertGreaterEqual(len(scenarios), 3)
        mans = manifests()
        names = set()
        for scenario in scenarios:
            self.assertEqual(support.schema_errors(scenario, "scenario"), [])
            validate_scenario(scenario, mans)  # raises on any author-time failure
            names.add(scenario["name"])
        self.assertEqual(len(names), len(scenarios))  # unique scenario ids

    def test_benign_and_attack_goals_map_to_predicates(self) -> None:
        scenario = import_suite("banking")[0]
        self.assertEqual(scenario["violation"]["where"]["prov(args.recipient)"],  # type: ignore[index]
                         {"provenance_is": "untrusted_derived"})
        self.assertEqual(scenario["task_success"]["where"]["args.recipient"],  # type: ignore[index]
                         {"equal": {"input_ref": "landlord_iban"}})

    def test_experiment_document_resolves(self) -> None:
        document = build_experiment_document(
            "banking", support.conditions(), repeats=4, agent_ref="scripted@0.6"
        )
        resolved = resolve(document)  # undefended/defended -> ungoverned/governed
        self.assertEqual(
            sorted(c["id"] for c in resolved.conditions), ["governed", "ungoverned"]
        )
        self.assertEqual(resolved.trial_count, len(resolved.scenarios) * 2 * 4)

    def test_suite_run_shows_governance_delta(self) -> None:
        scenarios = import_suite("banking")
        mans = manifests()
        conditions = support.conditions()
        registry = default_registry(tuple(str(c["kernel"]) for c in conditions))
        result = run_experiment_suite(
            scenarios, mans, conditions, registry, repeats=6, run_id="r_adojo",
        )
        pairs = result.pairs("ungoverned", "governed", metric="ASR")
        self.assertEqual(len(pairs), len(scenarios) * 6)  # pooled: repeats x scenarios
        governed_breaches = sum(1 for _, treated in pairs if treated)
        ungoverned_breaches = sum(1 for base, _ in pairs if base)
        self.assertEqual(governed_breaches, 0)
        self.assertGreater(ungoverned_breaches, 0)

    def test_faithful_agent_pays_declared_landlord_across_scenarios(self) -> None:
        # the faithful recipient is derived from each scenario's task_success
        # predicate, not hardcoded — so the generalized runner stays correct
        scenarios = import_suite("banking")
        mans = manifests()
        conditions = [support.conditions()[1]]  # governed only
        registry = default_registry((str(conditions[0]["kernel"]),))
        from lab_runner import ScriptedAgent

        result = run_experiment_suite(
            scenarios, mans, conditions, registry, repeats=6, run_id="r_faithful",
            agent=ScriptedAgent(attack_rate=0.0),
        )
        for outcome in result.outcomes.values():
            self.assertTrue(outcome.task_success)
            self.assertFalse(outcome.violation)


if __name__ == "__main__":
    unittest.main()
