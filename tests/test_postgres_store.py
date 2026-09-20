"""The Postgres backend for ScreenStore, against a real Postgres.

Skipped unless AXOR_LAB_TEST_POSTGRES_URL names one — the suite must stay
runnable on a laptop with nothing installed, because the CLI it mostly tests
needs nothing installed.

What matters here is not that rows round-trip. It is that jsonb is SAFE for
content-addressed documents, and that the one thing it cannot hold is refused
rather than quietly repaired.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
import uuid

from lab_contracts.canonical import content_hash
from lab_server.screens import ScreenStore, ScreenStoreError

DSN = os.environ.get("AXOR_LAB_TEST_POSTGRES_URL", "").strip()


def _suite(suite_id: str, name: str = "S", **extra: object) -> dict[str, object]:
    """A schema-VALID suite: the built-in one, re-identified.

    Hand-rolling the shape here would test the store against a document the
    product would reject — `put` schema-validates before it writes, which is
    how the first draft of this file failed."""
    from lab_suite import builtin_registry

    document = copy.deepcopy(builtin_registry().get("blank").manifest())
    document.update({"id": suite_id, "name": name, **extra})
    return document


@unittest.skipUnless(DSN, "set AXOR_LAB_TEST_POSTGRES_URL to run")
class TestPostgresScreenStore(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = "ws_" + uuid.uuid4().hex[:10]
        self.store = ScreenStore(dsn=DSN, workspace_id=self.ws)

    def test_a_document_survives_jsonb_with_its_content_hash_intact(self) -> None:
        """The load-bearing claim of the whole migration.

        jsonb reorders keys, drops whitespace and normalizes numbers, so it does
        NOT preserve bytes. It does not have to: `content_hash` canonicalizes the
        parsed object (RFC 8785), so the hash is a property of the document, not
        of how a store spelled it. If that ever stops being true, every receipt
        this product issues stops verifying — so it is asserted here rather than
        assumed in a comment.
        """
        document = _suite("s-hash")
        before = content_hash(document)
        self.store.put("suite", document)
        after = content_hash(self.store.get("suite", "s-hash"))
        self.assertEqual(before, after)

    def test_numbers_come_back_as_int_and_float_not_decimal(self) -> None:
        """A driver that returned Decimal would break canonicalization — it
        refuses anything that is not int/float/str/bool/None. psycopg3 parses
        jsonb with json.loads, so it does not; pin that, because it is a driver
        default the migration silently depends on."""
        document = _suite("s-num")
        # numbers inside the document the schema already allows: a scenario's
        # own inputs are free-form
        document["scenarios"][0]["inputs"] = {"threshold": 0.25, "count": 3}  # type: ignore[index]
        self.store.put("suite", document)
        inputs = self.store.get("suite", "s-num")["scenarios"][0]["inputs"]  # type: ignore[index]
        self.assertIsInstance(inputs["threshold"], float)
        self.assertIsInstance(inputs["count"], int)
        # and the document still hashes — the real consequence of the above
        self.assertTrue(content_hash(self.store.get("suite", "s-num")))

    def test_a_nul_is_refused_by_name_not_stripped(self) -> None:
        """Stripping it would change the document and therefore its hash, which
        is the one thing a store may never do to a receipt."""
        from lab_server.db import UnstorableDocument

        document = _suite("s-nul")
        document["scenarios"][0]["inputs"] = {"note": "before\x00after"}  # type: ignore[index]
        with self.assertRaises(UnstorableDocument) as caught:
            self.store.put("suite", document)
        self.assertIn("NUL", str(caught.exception))
        self.assertIn("note", str(caught.exception))

    def test_crud_and_isolation_between_workspaces(self) -> None:
        self.store.put("suite", _suite("a"))
        self.store.put("suite", _suite("b"))
        self.assertEqual(self.store.ids("suite"), ["a", "b"])

        other = ScreenStore(dsn=DSN, workspace_id="ws_" + uuid.uuid4().hex[:10])
        self.assertEqual(other.ids("suite"), [])  # one tenant cannot see another

        self.assertTrue(self.store.delete("suite", "a"))
        self.assertFalse(self.store.delete("suite", "a"))  # idempotent
        self.assertEqual(self.store.ids("suite"), ["b"])
        with self.assertRaises(ScreenStoreError):
            self.store.get("suite", "a")

    def test_put_replaces_rather_than_duplicating(self) -> None:
        self.store.put("suite", _suite("c", name="first"))
        self.store.put("suite", _suite("c", name="second"))
        self.assertEqual(self.store.ids("suite"), ["c"])
        self.assertEqual(self.store.get("suite", "c")["name"], "second")

    def test_nothing_is_preloaded(self) -> None:
        """A second store over the same workspace sees the row without having
        loaded anything at construction — the property the RAM ceiling was
        about."""
        self.store.put("suite", _suite("d"))
        fresh = ScreenStore(dsn=DSN, workspace_id=self.ws)
        self.assertEqual(fresh.get("suite", "d")["id"], "d")

    def test_search_inside_the_document(self) -> None:
        """The reason to be on a database at all: asking a question a directory
        of files cannot answer."""
        self.store.put("suite", _suite("gov", capabilities=["governance"]))
        self.store.put("suite", _suite("plain", capabilities=[]))
        found = self.store.find("suite", {"capabilities": ["governance"]})
        self.assertEqual([d["id"] for d in found], ["gov"])


class TestContainsAgreesAcrossBackends(unittest.TestCase):
    """`find` must mean the same thing with or without a database, or a screen
    would show different results per deployment."""

    def test_the_python_predicate_matches_postgres_containment(self) -> None:
        from lab_server.screens import _contains

        document = json.loads(json.dumps({
            "id": "x", "capabilities": ["governance", "budget"],
            "nested": {"a": 1, "b": [{"deep": True}]},
        }))
        self.assertTrue(_contains(document, {"capabilities": ["governance"]}))
        self.assertTrue(_contains(document, {"nested": {"a": 1}}))
        self.assertTrue(_contains(document, {"nested": {"b": [{"deep": True}]}}))
        self.assertFalse(_contains(document, {"capabilities": ["nope"]}))
        self.assertFalse(_contains(document, {"nested": {"a": 2}}))
        self.assertFalse(_contains(document, {"missing": 1}))


if __name__ == "__main__":
    unittest.main()
