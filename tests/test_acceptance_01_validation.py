"""Acceptance test 1 — validation rejects bad scenarios with specific errors.

A scenario with a predicate naming a non-existent tool, no untrusted-field
injection vector, no egress sink, or an unresolved $inputs.x is rejected at
author time (stage: validating), before anything runs.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_ref import ScenarioValidationError, validate_scenario


class TestScenarioValidation(unittest.TestCase):
    def test_good_scenario_passes(self) -> None:
        validate_scenario(support.banking_scenario(), support.manifests())

    def test_good_scenario_is_schema_valid(self) -> None:
        self.assertEqual(support.schema_errors(support.banking_scenario(), "scenario"), [])

    def test_predicate_naming_unknown_tool_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        scenario["violation"]["tool"] = "wire_transfer"  # type: ignore[index]
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(any("unknown tool 'wire_transfer'" in e for e in ctx.exception.errors))
        self.assertTrue(all(e.startswith("[validating]") for e in ctx.exception.errors))

    def test_injection_without_untrusted_vector_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        mans = support.manifests()
        mans["read_txns"]["untrusted_fields"] = []
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, mans)
        self.assertTrue(any("not a declared untrusted field" in e for e in ctx.exception.errors))

    def test_missing_injection_placement_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        del scenario["fixtures"]["read_txns"]["injection_placement"]  # type: ignore[union-attr]
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(any("injection has no vector" in e for e in ctx.exception.errors))

    def test_no_egress_sink_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        mans = support.manifests()
        mans["send_money"]["effect"] = {"default_class": "READ", "driving_args": ["recipient"]}
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, mans)
        self.assertTrue(any("nothing to breach" in e for e in ctx.exception.errors))

    def test_unresolved_inputs_ref_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        del scenario["inputs"]["landlord_iban"]  # type: ignore[union-attr]
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(
            any("$inputs.landlord_iban does not resolve" in e for e in ctx.exception.errors)
        )

    def test_unresolved_manifest_effect_ref_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        del scenario["inputs"]["known_ibans"]  # type: ignore[union-attr]
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(
            any("$inputs.known_ibans does not resolve" in e for e in ctx.exception.errors)
        )

    def test_unknown_matcher_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        scenario["violation"]["where"] = {"args.recipient": {"sounds_like": "x"}}  # type: ignore[index]
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(any("unknown matcher 'sounds_like'" in e for e in ctx.exception.errors))

    def test_all_failures_reported_at_once(self) -> None:
        scenario = support.banking_scenario()
        scenario["violation"]["tool"] = "wire_transfer"  # type: ignore[index]
        del scenario["inputs"]["landlord_iban"]  # type: ignore[union-attr]
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertGreaterEqual(len(ctx.exception.errors), 2)


if __name__ == "__main__":
    unittest.main()
