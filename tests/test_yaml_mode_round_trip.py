"""Basic → YAML → Basic is lossless (acceptance criterion 8.5).

The Builder's three modes edit ONE `suite/v1` document (RFC §13). The plan's
risk table names what happens when that stops being true: Basic mode grows
fields the YAML mode cannot express, and the two modes silently disagree about
what the experiment is. The guard was supposed to land in Phase 1 and did not —
there was no YAML mode at all, and `test_canonical_round_trip_is_lossless` is a
JSON→JSON check that would pass with no YAML support whatsoever.

YAML is not a superset of JSON in the direction that matters. YAML 1.1 resolves
unquoted `yes`/`no`/`on`/`off` to booleans, `~` to null, and `2026-08-08` to a
date object. A manifest with `"on"` as a STRING — a tag, a metric name, an
`enforcement` value — can come back as `True`, changing the manifest hash
without anyone editing the document. The adversarial cases below are the point
of this file; the built-in suites round-tripping is the easy half.
"""

from __future__ import annotations

import unittest

from lab_contracts import content_hash
from lab_suite import builtin_registry, from_yaml, round_trips, to_yaml, validate_manifest
from lab_suite.errors import SuiteError

# Values that are ordinary JSON strings and YAML 1.1 landmines.
LANDMINES = [
    "yes", "no", "on", "off", "true", "false", "True", "False",
    "null", "Null", "~", "none",
    "2026-08-08", "2026-08-08T00:00:00Z", "12:30",
    "1.0", "007", "0x1f", "1e5", "+1", "-", "", " ",
    "a: b", "- item", "#comment", "@ref", "*anchor", "&anchor", "%tag",
    "line\nbreak", "tab\there", "ünicode", "emoji \U0001f9ea",
]


class TestEveryBuiltInSurvivesTheRoundTrip(unittest.TestCase):
    def test_each_manifest_comes_back_byte_identical(self) -> None:
        for suite_id in builtin_registry().ids():
            with self.subTest(suite=suite_id):
                manifest = builtin_registry().get(suite_id).manifest()
                self.assertTrue(round_trips(manifest))

    def test_the_returned_manifest_still_validates(self) -> None:
        """Byte-identical is the strong claim; still-valid is the one a user
        notices first."""
        for suite_id in builtin_registry().ids():
            with self.subTest(suite=suite_id):
                manifest = builtin_registry().get(suite_id).manifest()
                self.assertEqual(validate_manifest(from_yaml(to_yaml(manifest))), [])


class TestYamlLandminesStayStrings(unittest.TestCase):
    def _manifest(self, **extra: object) -> dict[str, object]:
        return {
            "schema_version": "suite/v1", "id": "landmines", "name": "Landmines",
            "execution": {"strategy": "matrix", "repeats": 1,
                          "seed_policy": "per_repeat"},
            **extra,
        }

    def test_every_landmine_round_trips_as_the_string_it_was(self) -> None:
        for value in LANDMINES:
            with self.subTest(value=value):
                manifest = self._manifest(description=value, tags=[value])
                back = from_yaml(to_yaml(manifest))
                self.assertEqual(back["description"], value)
                self.assertIsInstance(back["description"], str)
                self.assertEqual(content_hash(back), content_hash(manifest))

    def test_a_landmine_as_a_KEY_round_trips_too(self) -> None:
        """Suite-defined metric names and evaluator ids become keys. A key
        coerced to a bool is a metric the manifest can no longer name."""
        manifest = self._manifest(ui_schema={"labels": {v: v for v in LANDMINES}})
        self.assertTrue(round_trips(manifest))

    def test_numbers_do_not_become_strings_or_the_reverse(self) -> None:
        manifest = self._manifest(
            ui_schema={"n": {"int": 1, "float": 1.5, "bool": True,
                             "str_one": "1", "str_true": "true", "nil": None}},
        )
        back = from_yaml(to_yaml(manifest))
        values = back["ui_schema"]["n"]
        self.assertIsInstance(values["int"], int)
        self.assertNotIsInstance(values["int"], bool)
        self.assertIsInstance(values["float"], float)
        self.assertIs(values["bool"], True)
        self.assertEqual(values["str_one"], "1")
        self.assertEqual(values["str_true"], "true")
        self.assertIsNone(values["nil"])


class TestUnrepresentableYamlIsRefusedAtTheEdge(unittest.TestCase):
    """YAML can express things a manifest cannot. Accepting one turns a
    losslessness bug into a serialization crash three steps later, in a place
    that says nothing about which field was wrong."""

    def test_an_unquoted_date_stays_the_string_it_was_typed_as(self) -> None:
        """PyYAML's SafeLoader turns `2026-08-08` into a `datetime.date`, which
        `suite/v1` has no type for and JSON cannot serialize. The loader drops
        that resolver, so the author gets the string they wrote — and the round
        trip stays lossless instead of crashing on the way out."""
        loaded = from_yaml("id: x\ncreated: 2026-08-08\n")
        self.assertEqual(loaded["created"], "2026-08-08")
        self.assertIsInstance(loaded["created"], str)

    def test_a_nested_date_stays_a_string_too(self) -> None:
        loaded = from_yaml("execution:\n  window:\n    - 2026-08-08\n")
        self.assertEqual(loaded["execution"]["window"], ["2026-08-08"])

    def test_a_top_level_list_is_refused(self) -> None:
        with self.assertRaises(SuiteError):
            from_yaml("- not\n- a\n- manifest\n")

    def test_a_non_string_key_is_refused(self) -> None:
        with self.assertRaises(SuiteError) as caught:
            from_yaml("1: one\n")
        self.assertIn("non-string key", str(caught.exception))


class TestTheModesEditOneDocument(unittest.TestCase):
    def test_a_manifest_edited_as_yaml_is_the_same_object_the_sdk_runs(self) -> None:
        """The whole point of the rule: what the YAML mode produces goes
        straight into the runner, with no second parser and no adapter."""
        from lab_suite import run_suite

        suite = builtin_registry().get("budget")
        edited = from_yaml(to_yaml(suite.manifest()).replace("repeats: 5", "repeats: 2"))
        self.assertEqual(validate_manifest(edited), [])
        run = run_suite(edited, run_id="r_yaml", suite=suite)
        self.assertEqual(len(run.trials), 2)

    def test_yaml_is_optional_and_says_so_when_absent(self) -> None:
        """PyYAML is an extra. Without it the mode must fail with an
        instruction, not an ImportError from three frames down."""
        import builtins

        from lab_suite.yaml_mode import YamlUnavailable

        real_import = builtins.__import__

        def _no_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = _no_yaml
        try:
            with self.assertRaises(YamlUnavailable) as caught:
                to_yaml({"id": "x"})
        finally:
            builtins.__import__ = real_import
        self.assertIn("axor-lab[yaml]", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
