"""Suite Platform contracts — Phase 1 (docs/spec-suite-platform/INTEGRATION_PLAN.md).

The new spec makes the Experiment Suite the core abstraction and demotes
governance to an optional capability. These tests pin the contract-level
consequences of that inversion, so a later phase cannot quietly undo them:

  - a run with NO governance is representable (the primary workflow);
  - a scenario with no attack model is representable;
  - a trial records what it COST, and an unmeasured metric stays absent;
  - artifact/v1 wraps bundle/v1 without moving the bundle's content hash;
  - the suite manifest survives a canonical round-trip losslessly (the
    Basic/Advanced/YAML single-document rule, in its Phase-1-testable form);
  - every slice example passes BOTH validators.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from lab_contracts import load_schemas, validate_artifact, validate_scenario
from lab_contracts.canonical import canonical_json, content_hash
from lab_contracts.subset_validator import validate_against

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "contracts" / "examples" / "slice-examples.json"


def _examples() -> dict[str, tuple[str, dict[str, object]]]:
    return json.loads(EXAMPLES.read_text())


def _example(name: str) -> dict[str, object]:
    return _examples()[name][1]


class TestGovernanceIsOptional(unittest.TestCase):
    def test_single_arm_experiment_validates(self) -> None:
        exp = _example("experiment_single_arm")
        self.assertNotIn("conditions", exp)
        self.assertEqual(validate_artifact(exp, "experiment"), [])

    def test_suite_needs_no_conditions(self) -> None:
        suite = _example("suite_budget")
        self.assertEqual(validate_artifact(suite, "suite"), [])
        self.assertNotIn("conditions", suite["execution"])  # type: ignore[index]
        self.assertNotIn("governance", suite.get("capabilities", []))  # type: ignore[arg-type]

    def test_scenario_without_attack_model_validates(self) -> None:
        """A budget/performance scenario has no injection and no violation.
        The schema used to require both, which made every non-security suite
        unrepresentable."""
        scenario = _example("scenario_budget_no_injection")
        self.assertNotIn("injection", scenario)
        self.assertNotIn("violation", scenario)
        self.assertEqual(validate_artifact(scenario, "scenario"), [])
        # and the SEMANTIC layer accepts it too — not just the schema
        manifests = {str(_example("tool_read_txns")["id"]): _example("tool_read_txns")}
        validate_scenario(scenario, manifests)

    def test_violation_without_injection_is_refused(self) -> None:
        """Relaxing the pair must not accept an incoherent scenario: a breach
        predicate with no attack vector can never fire."""
        scenario = copy.deepcopy(_example("scenario_budget_no_injection"))
        scenario["violation"] = {"event": "tool_call", "tool": "read_txns"}
        manifests = {str(_example("tool_read_txns")["id"]): _example("tool_read_txns")}
        with self.assertRaises(Exception) as ctx:
            validate_scenario(scenario, manifests)
        self.assertIn("injection", str(ctx.exception))


class TestTrialMetrics(unittest.TestCase):
    def _trial(self) -> dict[str, object]:
        bundle = _example("bundle_banking")
        return copy.deepcopy(bundle["trials"][0])  # type: ignore[index]

    def test_metrics_block_validates(self) -> None:
        bundle = copy.deepcopy(_example("bundle_banking"))
        bundle["trials"][0]["metrics"] = {  # type: ignore[index]
            "duration_ms": 18400, "steps": 14, "tokens_in": 1820,
            "tokens_out": 521, "cost_usd": 0.021,
        }
        self.assertEqual(validate_artifact(bundle, "bundle"), [])

    def test_suite_metrics_ride_alongside_platform_metrics(self) -> None:
        """A suite defines its own metrics (RFC §6); they live in the same
        object as the platform ones rather than a second bag."""
        bundle = copy.deepcopy(_example("bundle_banking"))
        bundle["trials"][0]["metrics"] = {  # type: ignore[index]
            "duration_ms": 900, "exchange_rate_age_hours": 26.5, "tier": "gold",
        }
        self.assertEqual(validate_artifact(bundle, "bundle"), [])

    def test_metrics_reject_a_structured_value(self) -> None:
        """Metrics are scalars. A nested object here would be an aggregate
        smuggled onto a trial, and aggregates must stay recomputable from the
        trials rather than be asserted by whoever wrote the bundle."""
        bundle = copy.deepcopy(_example("bundle_banking"))
        bundle["trials"][0]["metrics"] = {"latency": {"p95": 1200}}  # type: ignore[index]
        self.assertTrue(validate_artifact(bundle, "bundle"))

    def test_no_metric_defaults_to_a_value(self) -> None:
        """An unmeasured metric must be ABSENT, never defaulted.

        A metric_threshold regression reads this object: a schema `default` of
        0 on cost_usd would silently PASS a budget invariant that was never
        measured, and a default on duration_ms would pass a latency one. A
        missing measurement is not a measurement of zero, so no property here
        may carry a default at all.
        """
        trial_schema = load_schemas()["bundle"]["properties"]["trials"]["items"]  # type: ignore[index]
        metrics = trial_schema["properties"]["metrics"]  # type: ignore[index]
        for name, spec in metrics["properties"].items():  # type: ignore[index]
            with self.subTest(metric=name):
                self.assertNotIn("default", spec, f"metric '{name}' must not default")
        # a partial metrics block is legal — you record what you measured
        bundle = copy.deepcopy(_example("bundle_banking"))
        bundle["trials"][0]["metrics"] = {"duration_ms": 12}  # type: ignore[index]
        self.assertEqual(validate_artifact(bundle, "bundle"), [])


class TestArtifactWrapsBundle(unittest.TestCase):
    def test_embedding_does_not_move_the_bundle_hash(self) -> None:
        """§4.4 of the plan: artifact/v1 was made a WRAPPER precisely so that
        adopting it cannot invalidate a published bundle hash. If someone later
        flattens the bundle's fields into the artifact, this fails."""
        artifact = _example("artifact_banking")
        bundle = _example("bundle_banking")
        self.assertEqual(content_hash(artifact["bundle"]), content_hash(bundle))

    def test_artifact_validates_with_no_evidence_or_regressions(self) -> None:
        """A pre-Suite-Platform bundle upgrades by wrapping: empty evidence and
        regressions are legal, so the migration is lossless and mechanical."""
        artifact = copy.deepcopy(_example("artifact_banking"))
        artifact.pop("evidence_cases", None)
        artifact.pop("regressions", None)
        artifact.pop("suite", None)
        self.assertEqual(validate_artifact(artifact, "artifact"), [])

    def test_embedded_bundle_is_still_schema_checked(self) -> None:
        """The wrapper must not become a hiding place: a malformed bundle
        inside an artifact is still a validation failure."""
        artifact = copy.deepcopy(_example("artifact_banking"))
        del artifact["bundle"]["bundle_id"]  # type: ignore[index]
        errors = validate_artifact(artifact, "artifact")
        self.assertTrue(any("bundle_id" in e for e in errors), errors)


class TestSuiteManifestIsOneDocument(unittest.TestCase):
    def test_canonical_round_trip_is_lossless(self) -> None:
        """The Builder's Basic, Advanced and YAML modes all edit ONE manifest
        (RFC §13). The Phase-1-testable form of that rule: the manifest
        survives serialization with nothing dropped or reordered away."""
        suite = _example("suite_budget")
        once = canonical_json(suite)
        twice = canonical_json(json.loads(once))
        self.assertEqual(once, twice)
        self.assertEqual(json.loads(once), suite)

    def test_every_builder_section_has_a_home_in_the_manifest(self) -> None:
        """Each of the six Builder sections must map to a real manifest field.
        A section with nowhere to store its state is how Basic mode ends up
        owning data the YAML mode cannot see."""
        suite = _example("suite_budget")
        section_to_field = {
            "agents": "agents", "scenarios": "scenarios", "environment": "environment",
            "execution": "execution", "evaluation": "evaluation", "artifact": "artifact",
        }
        declared = {s["id"] for s in suite["ui_schema"]["sections"]}  # type: ignore[index]
        self.assertEqual(declared, set(section_to_field))
        properties = load_schemas()["suite"]["properties"]  # type: ignore[index]
        for section, field in section_to_field.items():
            self.assertIn(field, properties, f"Builder section '{section}' has no manifest field")


class TestTheBuilderBlankScenario(unittest.TestCase):
    """The other half of a cross-language contract.

    `web/src/lib/sections.ts::blankScenario` is what the Builder's "+ Add"
    writes; this checks a scenario of exactly that shape survives the real
    validator. `web/src/lib/sections.test.ts` pins the derivation on the
    TypeScript side — if you change one, change both.

    The old blank was a constant, and it was refused twice over: `tools: []`
    against `minItems 1`, and `task_success: {event: "final_output"}` against
    an evaluator that supports only `tool_call`. So the button reliably
    invalidated the suite, and the item form (name and task) offered no way to
    fix either without dropping into YAML.
    """

    #: verbatim `blankScenario(manifest)` for a suite whose first tool is `note`
    BLANK = {
        "schema_version": "scenario/v1",
        "name": "",
        "task": "",
        "inputs": {},
        "tools": [{"$ref": "note"}],
        "fixtures": {},
        "task_success": {"event": "tool_call", "tool": "note"},
    }

    def _blank_suite(self) -> dict:
        from lab_suite import builtin_registry

        return copy.deepcopy(builtin_registry().get("blank").manifest())

    def test_the_first_tool_of_the_blank_suite_is_the_one_it_refs(self) -> None:
        """`blankScenario` takes `environment.tools[0].id`; if that stops being
        `note` this test's fixture is stale, not the code."""
        manifest = self._blank_suite()
        self.assertEqual(
            [t["id"] for t in manifest["environment"]["tools"]][:1], ["note"],
        )

    def test_adding_one_leaves_the_suite_resolvable(self) -> None:
        from lab_suite import plan_suite

        manifest = self._blank_suite()
        manifest["scenarios"].append(copy.deepcopy(self.BLANK))
        # the real thing a dispatch does — schema, suite semantics AND the
        # per-scenario semantics the schema alone does not reach
        self.assertEqual(len(plan_suite(manifest).planned), 2)

    def test_the_two_keys_the_form_never_shows_are_the_two_that_broke(self) -> None:
        from lab_suite import SuiteValidationError, plan_suite

        for key, bad in (
            ("tools", []),
            ("task_success", {"event": "final_output"}),
        ):
            with self.subTest(key=key):
                manifest = self._blank_suite()
                manifest["scenarios"].append({**copy.deepcopy(self.BLANK), key: bad})
                with self.assertRaises(SuiteValidationError):
                    plan_suite(manifest)

    def test_a_toolless_suite_refuses_rather_than_papering_over(self) -> None:
        """`blankScenario` emits `tools: []` when the suite declares none. That
        is the true state of the document — there is nothing to reference — and
        the validator should say so."""
        from lab_suite import SuiteValidationError, plan_suite

        manifest = self._blank_suite()
        manifest["environment"]["tools"] = []
        with self.assertRaises(SuiteValidationError):
            plan_suite(manifest)


class TestTheSuiteSDKsOwnKnobs(unittest.TestCase):
    """`config_schema` / `config` / `ui_schema` — RFC §12, and the half of §13
    that reads "Every suite contributes declarative schemas".

    `config_schema` and `ui_schema` were in the schema from the start, and
    nothing read either. The schema's own description of `config_schema` says
    "The Builder renders it; a value that fails it is rejected at author time,
    not at run time" — and neither half was true, so a third-party suite could
    declare knobs no screen showed and no validator checked. `sections.test.ts`
    even used `ui_schema` as its example of a key NO form renders, which turned
    the unimplemented feature into a passing test.

    The TypeScript half is `web/src/lib/sections.ts::configFields`.
    """

    SCHEMA = {
        "type": "object",
        "properties": {
            "depth": {"type": "integer", "title": "Search depth"},
            "style": {"enum": ["terse", "verbose"]},
        },
        "required": ["depth"],
        "additionalProperties": False,
    }

    def _suite(self, **over: object) -> dict:
        from lab_suite import builtin_registry

        return {**copy.deepcopy(builtin_registry().get("blank").manifest()), **over}

    def _errors(self, **over: object) -> list[str]:
        from lab_suite import validate_manifest

        return validate_manifest(self._suite(**over))

    def test_a_suite_declaring_nothing_is_unaffected(self) -> None:
        self.assertEqual(self._errors(), [])

    def test_config_is_checked_against_config_schema_at_author_time(self) -> None:
        self.assertEqual(
            self._errors(config_schema=self.SCHEMA,
                         config={"depth": 3, "style": "terse"}),
            [],
        )
        for label, config, fragment in (
            ("wrong type", {"depth": "three"}, "type integer, got str"),
            ("missing required", {"style": "terse"}, "missing required 'depth'"),
            ("unknown key", {"depth": 1, "nope": 2}, "additional property 'nope'"),
            ("bad enum", {"depth": 1, "style": "loud"}, "not in enum"),
        ):
            with self.subTest(case=label):
                errors = self._errors(config_schema=self.SCHEMA, config=config)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(fragment, errors[0])

    def test_values_with_nothing_describing_them_are_refused(self) -> None:
        errors = self._errors(config={"depth": 3})
        self.assertEqual(len(errors), 1)
        self.assertIn("declares no `config_schema`", errors[0])

    def test_a_layout_cannot_introduce_a_field(self) -> None:
        """The schema says `ui_schema` "can never introduce a field
        config_schema does not define". Nothing enforced it, so a layout could
        render an input writing a value nothing validates."""
        errors = self._errors(
            config_schema=self.SCHEMA, config={"depth": 1},
            ui_schema={"sections": [{"id": "execution", "fields": ["depth", "ghost"]}]},
        )
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("'ghost'", errors[0])
        self.assertIn("a layout cannot introduce a field", errors[0])

    def test_a_layout_over_declared_fields_is_fine(self) -> None:
        self.assertEqual(
            self._errors(
                config_schema=self.SCHEMA, config={"depth": 1},
                ui_schema={"sections": [
                    {"id": "execution", "fields": ["depth"]},
                    {"id": "evaluation", "fields": ["style"], "advanced": True},
                ]},
            ),
            [],
        )

    def test_a_ui_schema_that_only_titles_sections_still_validates(self) -> None:
        """The shape the shipped slice example uses — no `fields` at all."""
        self.assertEqual(
            self._errors(ui_schema={"sections": [{"id": "execution", "title": "Execution"}]}),
            [],
        )

    def test_config_survives_a_canonical_round_trip(self) -> None:
        """The Basic/Advanced/YAML single-document rule reaches these too."""
        from lab_contracts.canonical import canonical_json

        suite = self._suite(config_schema=self.SCHEMA, config={"depth": 4})
        once = canonical_json(suite)
        self.assertEqual(json.loads(once)["config"], {"depth": 4})
        self.assertEqual(canonical_json(json.loads(once)), once)


class TestRegressionKinds(unittest.TestCase):
    def test_metric_threshold_regression_validates(self) -> None:
        reg = _example("regression_latency_threshold")
        self.assertEqual(validate_artifact(reg, "regression"), [])
        self.assertEqual(reg["rule"]["kind"], "metric_threshold")  # type: ignore[index]

    def test_verdict_sequence_survives_as_one_kind(self) -> None:
        """The governance pin is not deleted — it becomes one rule kind."""
        reg = copy.deepcopy(_example("regression_latency_threshold"))
        reg["rule"] = {"kind": "verdict_sequence", "verdicts": ["ALLOW", "DENY"],
                       "kernel": "reference_taint_floor_kernel"}
        self.assertEqual(validate_artifact(reg, "regression"), [])

    def test_rule_must_be_exactly_one_kind(self) -> None:
        reg = copy.deepcopy(_example("regression_latency_threshold"))
        reg["rule"] = {"kind": "metric_threshold", "metric": "duration_ms",
                       "op": "lt", "value": 1, "verdicts": ["DENY"]}
        self.assertTrue(validate_artifact(reg, "regression"))

    def test_prose_expectation_is_not_the_rule(self) -> None:
        """`expectation` is documentation. A regression whose only invariant is
        prose would be unrunnable, so `rule` stays required."""
        reg = copy.deepcopy(_example("regression_latency_threshold"))
        del reg["rule"]
        errors = validate_artifact(reg, "regression")
        self.assertTrue(any("rule" in e for e in errors), errors)


class TestEvidenceCaseIsGeneric(unittest.TestCase):
    def test_non_security_evidence_case_validates(self) -> None:
        case = _example("evidence_case_latency")
        self.assertEqual(validate_artifact(case, "evidence-case"), [])
        self.assertEqual(case["kind"], "latency_spike")
        self.assertNotIn("governance", case)

    def test_governance_chain_is_an_optional_block(self) -> None:
        case = copy.deepcopy(_example("evidence_case_latency"))
        case["kind"] = "prompt_injection"
        case["governance"] = {
            "mode": "observed_governed_twin", "twin_trial_id": "t_587",
            "chain": {"injection": "v_inj", "driving_value_id": "v_recipient"},
            "replay_status": "exactly_replayable", "explicit_flow_tracked": True,
        }
        self.assertEqual(validate_artifact(case, "evidence-case"), [])

    def test_kind_is_open_for_third_party_suites(self) -> None:
        """RFC §17 wants a third-party suite ecosystem; a new investigation
        kind must not require a platform schema change."""
        case = copy.deepcopy(_example("evidence_case_latency"))
        case["kind"] = "acme.refund_policy_drift"
        self.assertEqual(validate_artifact(case, "evidence-case"), [])


class TestSliceExampleParity(unittest.TestCase):
    def test_every_example_passes_both_validators(self) -> None:
        """contracts/validate.py (dev) and lab_contracts.subset_validator
        (runtime) must agree. They drifted once already: the runtime validator
        understands if/then and fails closed on unknown types, so an example
        could be 'green' in the contracts directory and rejected at runtime."""
        schemas = load_schemas()
        for name, (schema_name, obj) in _examples().items():
            with self.subTest(example=name):
                self.assertEqual(validate_against(obj, schema_name, schemas), [],
                                 f"{name} fails the runtime validator")
                self.assertEqual(validate_artifact(obj, schema_name), [],
                                 f"{name} fails semantic validation")

    def test_schemas_are_in_sync_between_contracts_and_package(self) -> None:
        """contracts/schemas/ is the source of truth; lab_contracts/schemas/ is
        the shipped copy. A drift means the wheel validates against a different
        contract than the repo documents."""
        source = REPO_ROOT / "contracts" / "schemas"
        packaged = REPO_ROOT / "lab_contracts" / "schemas"
        source_files = {p.name for p in source.glob("*.schema.json")}
        packaged_files = {p.name for p in packaged.glob("*.schema.json")}
        self.assertEqual(source_files, packaged_files)
        for name in sorted(source_files):
            with self.subTest(schema=name):
                self.assertEqual(
                    json.loads((source / name).read_text()),
                    json.loads((packaged / name).read_text()),
                    f"{name} differs between contracts/ and lab_contracts/",
                )


if __name__ == "__main__":
    unittest.main()
