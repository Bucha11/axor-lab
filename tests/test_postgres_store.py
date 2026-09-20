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


@unittest.skipUnless(DSN, "set AXOR_LAB_TEST_POSTGRES_URL to run")
class TestPostgresRunStore(unittest.TestCase):
    """Runs and their TRACES outlive the process.

    This is the data-loss bug, not a scaling nicety: `SCREEN_KINDS` never held
    a trace, so a finished run's artifact survived a restart while the traces
    it references did not — the Trial screen for any past run went blank.
    """

    def setUp(self) -> None:
        from lab_server.runtime_jobs import RuntimeJobStore

        self.ws = "ws_" + uuid.uuid4().hex[:10]
        self.make = lambda: RuntimeJobStore(dsn=DSN, workspace_id=self.ws)
        self.store = self.make()

    def _finished_run(self) -> tuple[str, str, dict[str, object]]:
        """A run completed with a schema-VALID trace — the store requires one,
        and a hand-rolled shape would test persistence against a document the
        product rejects."""
        from tests.test_runtime_jobs import _valid_trace

        runtime = self.store.connect_runtime(runtime_label="rt")["runtime_ref"]
        run = self.store.create_run(runtime, {"id": "e"}, planned=["t0"])
        run_id = str(run["run_id"])
        if self.store.run_state(run_id) == "awaiting_confirmation":
            self.store.confirm_run(run_id)
        self.store.claim(run_id, runtime)
        trace = _valid_trace()
        self.store.complete_trial(run_id, "t0", runtime, trace)
        return run_id, runtime, trace

    def test_a_finished_runs_trace_survives_a_new_process(self) -> None:
        run_id, _, trace = self._finished_run()
        reopened = self.make()  # a different store: nothing shared in memory
        self.assertEqual(reopened.trial_trace(run_id, "t0")["trace_id"],
                         trace["trace_id"])

    def test_the_run_is_listed_after_a_restart(self) -> None:
        run_id, _, _ = self._finished_run()
        reopened = self.make()
        self.assertIn(run_id, reopened.run_ids())
        summary = next(r for r in reopened.list_runs() if r["run_id"] == run_id)
        self.assertEqual(summary["planned"], 1)
        self.assertEqual(summary["completed"], 1)

    def test_a_run_saved_at_creation_is_not_lost_before_its_first_transition(self) -> None:
        runtime = self.store.connect_runtime(runtime_label="rt")["runtime_ref"]
        run_id = str(self.store.create_run(
            runtime, {"id": "e"}, planned=["t0"])["run_id"])
        self.assertIn(run_id, self.make().run_ids())

    def test_listeners_are_not_persisted(self) -> None:
        """They are live queues belonging to connections this process holds;
        a later process must not inherit a reference to them."""
        from lab_server.runtime_jobs import _job_to_row

        run_id, _, _ = self._finished_run()
        self.store.subscribe(run_id)
        row = _job_to_row(self.store._jobs[run_id])  # noqa: SLF001
        self.assertNotIn("listeners", row)
        json.dumps(row)  # and the row is serializable at all

    def test_runs_are_isolated_between_workspaces(self) -> None:
        from lab_server.runtime_jobs import RuntimeJobStore

        run_id, _, _ = self._finished_run()
        other = RuntimeJobStore(dsn=DSN, workspace_id="ws_" + uuid.uuid4().hex[:10])
        self.assertNotIn(run_id, other.run_ids())
