"""Two-stage validation + duplicate detection in resolve() (review r2, Patch 6).

- A schema-invalid scenario used to reach the semantic validator, which
  dereferences scenario['violation'] unconditionally → a raw KeyError instead
  of a clean [validating] error. Semantic validation now runs only on a
  schema-valid scenario.
- Duplicate manifest ids / scenario names silently overwrote (last wins); they
  are now reported as validation errors.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from lab_runner.errors import ExperimentFileError
from lab_runner.experiment_file import resolve

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "banking-exfil-01.axl"


def _document() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text())


class TestValidationPipeline(unittest.TestCase):
    def test_example_resolves(self) -> None:
        resolve(_document())  # sanity: the shipped example is valid

    def test_scenario_missing_violation_is_a_clean_error_not_keyerror(self) -> None:
        doc = _document()
        del doc["scenarios"][0]["violation"]  # type: ignore[index]
        # must be an ExperimentFileError (stage: validating), NOT a raw KeyError
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("violation" in e for e in ctx.exception.errors))

    def test_duplicate_manifest_id_is_rejected(self) -> None:
        doc = _document()
        manifests = doc["tool_manifests"]  # type: ignore[index]
        manifests.append(copy.deepcopy(manifests[0]))  # same id twice
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("duplicate tool_manifest id" in e for e in ctx.exception.errors))

    def test_duplicate_scenario_name_is_rejected(self) -> None:
        doc = _document()
        scenarios = doc["scenarios"]  # type: ignore[index]
        scenarios.append(copy.deepcopy(scenarios[0]))  # same name twice
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("duplicate scenario name" in e for e in ctx.exception.errors))


if __name__ == "__main__":
    unittest.main()
