"""Contract/runtime parity (review §3).

The validator enforces the numeric/array constraints the schemas actually use
(minimum, minItems, minLength); author-time validation rejects predicate
constructs the runtime evaluator cannot run, so a scenario can never be
schema-valid yet runtime-invalid; and a wrong config_hash is caught on resolve.
"""

from __future__ import annotations

import copy
import unittest

from tests import support
from lab_contracts import ScenarioValidationError, load_schemas, validate_scenario
from lab_contracts.subset_validator import validate_against
from lab_runner.experiment_file import ExperimentFileError, resolve


def _schemas():
    return load_schemas()


class TestValidatorConstraints(unittest.TestCase):
    def test_repeats_zero_is_rejected(self) -> None:
        exp = {
            "schema_version": "experiment/v1", "id": "e", "type": "benchmark",
            "scenario_ids": ["s"], "repeats": 0, "agent_ref": "scripted",
            "conditions": support.conditions(),
        }
        errors = validate_against(exp, "experiment", _schemas())
        self.assertTrue(any("minimum" in e for e in errors), errors)

    def test_single_condition_is_rejected(self) -> None:
        exp = {
            "schema_version": "experiment/v1", "id": "e", "type": "benchmark",
            "scenario_ids": ["s"], "repeats": 5, "agent_ref": "scripted",
            "conditions": [support.conditions()[0]],  # only 1, schema requires ≥2
        }
        errors = validate_against(exp, "experiment", _schemas())
        self.assertTrue(any("minItems" in e for e in errors), errors)

    def test_negative_seq_is_rejected(self) -> None:
        trace = {
            "schema_version": "trace/v1", "trace_id": "t", "values": [],
            "trial": {"run_id": "r", "scenario_id": "s", "condition_id": "c",
                      "seed": "s0", "repeat_index": -1},  # minimum 0
            "producer": {"mode": "wrapped_code", "provenance_fidelity": "explicit_flow_tracked",
                         "kernel_version": "k"},
            "events": [{"seq": -5, "node": "root", "type": "tool_result"}],  # minimum 0
        }
        errors = validate_against(trace, "trace", _schemas())
        self.assertTrue(any("minimum" in e for e in errors), errors)


class TestAuthorTimeMatchesRuntime(unittest.TestCase):
    def test_result_field_address_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        scenario["violation"] = {
            "event": "tool_call", "tool": "send_money",
            "where": {"result.status": {"equal": "sent"}},  # result.x unsupported
        }
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(any("not supported by the runtime" in e for e in ctx.exception.errors))

    def test_unsupported_event_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        scenario["violation"] = {
            "event": "tool_result", "tool": "read_txns",  # evaluator: tool_call only
            "where": {"args.x": {"equal": "y"}},
        }
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(any("not supported by the runtime evaluator" in e for e in ctx.exception.errors))

    def test_count_is_rejected(self) -> None:
        scenario = support.banking_scenario()
        scenario["violation"] = {
            "event": "tool_call", "tool": "send_money",
            "where": {"prov(args.recipient)": {"provenance_is": "untrusted_derived"}},
            "count": {"min": 2},  # not evaluated → reject
        }
        with self.assertRaises(ScenarioValidationError) as ctx:
            validate_scenario(scenario, support.manifests())
        self.assertTrue(any("'count'" in e for e in ctx.exception.errors))

    def test_the_slice_scenario_still_passes(self) -> None:
        validate_scenario(support.banking_scenario(), support.manifests())


class TestConfigHashVerifiedOnResolve(unittest.TestCase):
    def _document(self) -> dict[str, object]:
        return {
            "experiment": {
                "schema_version": "experiment/v1", "id": "exp", "type": "benchmark",
                "scenario_ids": ["banking-exfil-01"], "conditions": support.conditions(),
                "repeats": 2, "agent_ref": "scripted", "run_mode": "compare",
            },
            "scenarios": [support.banking_scenario()],
            "tool_manifests": list(support.manifests().values()),
        }

    def test_wrong_config_hash_is_rejected_on_resolve(self) -> None:
        document = self._document()
        for condition in document["experiment"]["conditions"]:  # type: ignore[index]
            if condition["enforcement"] == "on":
                condition["config_hash"] = "sha256:deadbeef"
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(document)
        self.assertTrue(any("config_hash" in e for e in ctx.exception.errors))

    def test_correct_config_hash_resolves(self) -> None:
        resolve(self._document())  # support.conditions() carry correct hashes


if __name__ == "__main__":
    unittest.main()
