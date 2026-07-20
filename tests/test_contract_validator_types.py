"""Contract validator type discipline (review round 3, Patch 11).

The minimal validator keyed types off isinstance, and 'null' was not in the type
map — so a schema requiring null accepted ANY value (isinstance fell through to
"unknown type → match"), and an unknown/misspelled type matched everything.
Booleans must also never satisfy number/integer (bool is an int subclass).
"""

from __future__ import annotations

import unittest

from lab_contracts.subset_validator import validate_against


def _schema(prop_type: str) -> dict:
    return {"t": {"type": "object", "properties": {"x": {"type": prop_type}},
                  "required": ["x"]}}


class TestValidatorTypes(unittest.TestCase):
    def _errs(self, value: object, prop_type: str) -> list[str]:
        return validate_against({"x": value}, "t", _schema(prop_type))

    def test_null_accepts_none(self) -> None:
        self.assertEqual(self._errs(None, "null"), [])

    def test_null_rejects_non_none(self) -> None:
        # the bug: 'null' fell through to "match anything"
        self.assertTrue(self._errs(5, "null"))
        self.assertTrue(self._errs("x", "null"))
        self.assertTrue(self._errs(False, "null"))

    def test_boolean_is_not_an_integer(self) -> None:
        self.assertTrue(self._errs(True, "integer"))
        self.assertTrue(self._errs(False, "number"))

    def test_integer_accepts_int_not_float(self) -> None:
        self.assertEqual(self._errs(3, "integer"), [])
        self.assertTrue(self._errs(3.5, "integer"))

    def test_unknown_type_name_matches_nothing(self) -> None:
        # a misspelled type used to accept everything; now it accepts nothing
        self.assertTrue(self._errs(5, "integr"))
        self.assertTrue(self._errs("x", "strng"))


class TestExperimentBooleanRepeats(unittest.TestCase):
    def test_boolean_repeats_is_rejected(self) -> None:
        from lab_contracts import validate_artifact
        experiment = {
            "schema_version": "experiment/v1", "id": "e", "agent_ref": "scripted@0.6",
            "repeats": True, "scenario_ids": ["s"], "conditions": [],
        }
        errors = validate_artifact(experiment, "experiment")
        self.assertTrue(any("repeats" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
