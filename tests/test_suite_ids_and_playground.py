"""Suite ids the router can address, create that does not overwrite, and a
Playground that runs the suite the user saved.

Three ways a saved suite used to go missing:

- the Playground resolved `suite_id` against the built-in registry only, so a
  workspace suite could not be tried and an edited built-in ran the shipped
  document instead of the edit;
- the schema's own example id (`acme.refund-policy`) saved fine and then 404'd
  on every `/suites/{id}` route, because the routes allowed no dots;
- POST /suites upserted, so renaming a suite in the Builder to an id another
  saved suite already had replaced that suite without a word.
"""

from __future__ import annotations

import unittest

from lab_suite import builtin_registry

from tests.test_screen_api import ScreenApiTestCase


def _budget() -> dict:
    return builtin_registry().get("budget").manifest()


class TestThePlaygroundRunsTheSavedSuite(ScreenApiTestCase):
    def test_a_workspace_suite_can_be_tried_by_id(self) -> None:
        self.call("POST", "/suites", {"suite": {**_budget(), "id": "mine"}})
        status, payload = self.call("POST", "/playground/trial", {"suite_id": "mine"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["mode"], "playground")

    def test_an_edited_builtin_runs_the_edit_not_the_shipped_document(self) -> None:
        edited = _budget()
        first = dict(edited["scenarios"][0], name="renamed-in-the-builder")
        edited["scenarios"] = [first, *edited["scenarios"][1:]]
        self.assertEqual(self.call("PUT", "/suites/budget", {"suite": edited})[0], 200)
        # the edited scenario name exists ONLY in the saved copy: resolving it
        # proves the trial ran the edit (the built-in would answer 404)
        status, payload = self.call(
            "POST", "/playground/trial",
            {"suite_id": "budget", "scenario": "renamed-in-the-builder"},
        )
        self.assertEqual(status, 200, payload)

    def test_an_unknown_id_is_still_a_404(self) -> None:
        status, _ = self.call("POST", "/playground/trial", {"suite_id": "nothing-here"})
        self.assertEqual(status, 404)


class TestDottedSuiteIds(ScreenApiTestCase):
    def test_the_schema_example_id_is_reachable_after_saving(self) -> None:
        suite = {**_budget(), "id": "acme.refund-policy"}
        self.assertEqual(self.call("POST", "/suites", {"suite": suite})[0], 201)
        status, fetched = self.call("GET", "/suites/acme.refund-policy")
        self.assertEqual(status, 200, fetched)
        self.assertEqual(fetched["id"], "acme.refund-policy")
        edited = {**suite, "description": "edited"}
        self.assertEqual(
            self.call("PUT", "/suites/acme.refund-policy", {"suite": edited})[0], 200)
        self.assertEqual(self.call("DELETE", "/suites/acme.refund-policy")[0], 200)
        self.assertEqual(self.call("GET", "/suites/acme.refund-policy")[0], 404)

    def test_an_unaddressable_id_is_refused_at_save_time(self) -> None:
        """Saving it would succeed and then 404 on every open, run and delete."""
        for bad in (".", "..", "a/b", "../escape", "has space", ""):
            with self.subTest(bad=bad):
                status, payload = self.call(
                    "POST", "/suites", {"suite": {**_budget(), "id": bad}})
                self.assertIn(status, (400, 422), payload)
        # and nothing was stored under any of them
        _, catalog = self.call("GET", "/suites")
        ids = {c["id"] for c in catalog["suites"]}
        self.assertFalse(ids & {".", "..", "a/b", "../escape", "has space"})

    def test_dot_segments_do_not_match_a_suite_route(self) -> None:
        for path in ("/suites/..", "/suites/.", "/suites/%2E%2E", "/suites/../yaml"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path)[0], 404)


class TestCreateDoesNotOverwrite(ScreenApiTestCase):
    def test_creating_an_id_that_is_already_saved_is_a_409(self) -> None:
        original = {**_budget(), "id": "taken", "description": "the original"}
        self.assertEqual(self.call("POST", "/suites", {"suite": original})[0], 201)
        status, payload = self.call(
            "POST", "/suites",
            {"suite": {**original, "description": "an impostor"}})
        self.assertEqual(status, 409, payload)
        self.assertIn("already exists", payload["error"])
        _, fetched = self.call("GET", "/suites/taken")
        self.assertEqual(fetched["description"], "the original")

    def test_put_is_still_the_edit_route(self) -> None:
        suite = {**_budget(), "id": "editable"}
        self.call("POST", "/suites", {"suite": suite})
        status, _ = self.call(
            "PUT", "/suites/editable", {"suite": {**suite, "description": "v2"}})
        self.assertEqual(status, 200)

    def test_a_builtin_id_can_still_be_created_over(self) -> None:
        """Not an overwrite: the workspace copy shadows the built-in and DELETE
        brings the shipped suite back."""
        self.assertEqual(self.call("POST", "/suites", {"suite": _budget()})[0], 201)


if __name__ == "__main__":
    unittest.main()
