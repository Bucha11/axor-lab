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
from lab_server.errors import NotFound, PublishRejected
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


@unittest.skipUnless(DSN, "set AXOR_LAB_TEST_POSTGRES_URL to run")
class TestPostgresRegistry(unittest.TestCase):
    """Tenants, their members and their audit log survive the process.

    Measured before this existed: a workspace showing owner 1 / member 2 /
    viewer 1 came back from a restart with only its owner, and a member token
    that had just worked returned 401.
    """

    def setUp(self) -> None:
        from lab_server.workspaces import Workspaces

        self.make = lambda: Workspaces(dsn=DSN)
        self.registry = self.make()

    def _workspace(self) -> tuple[str, str]:
        from lab_server.workspaces import Workspace

        ws_id = "ws_" + uuid.uuid4().hex[:10]
        owner = "owner-" + uuid.uuid4().hex
        self.registry.add(Workspace(id=ws_id, name="W", token=owner))
        return ws_id, owner

    def test_members_survive_a_restart(self) -> None:
        ws_id, owner = self._workspace()
        member = "member-" + uuid.uuid4().hex
        self.registry.add_member(ws_id, member, "member")

        reopened = self.make()
        self.assertIsNotNone(reopened.resolve_token(owner))
        resolved = reopened.resolve_token(member)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, ws_id)  # type: ignore[union-attr]
        self.assertEqual(reopened.role_of(member), "member")

    def test_a_removed_member_stays_removed(self) -> None:
        ws_id, _ = self._workspace()
        member = "gone-" + uuid.uuid4().hex
        self.registry.add_member(ws_id, member, "member")
        self.assertTrue(self.registry.remove_member(ws_id, member))
        self.assertIsNone(self.make().resolve_token(member))

    def test_tokens_are_stored_HASHED_never_in_the_clear(self) -> None:
        """A plaintext token in memory was careless; in a database it is a
        credential dump waiting for a pg_dump."""
        import psycopg

        from lab_server.workspaces import token_digest

        ws_id, owner = self._workspace()
        member = "secret-" + uuid.uuid4().hex
        self.registry.add_member(ws_id, member, "admin")

        with psycopg.connect(DSN) as conn:
            rows = conn.execute(
                "select token_sha256 from lab_members where ws_id=%s", (ws_id,)
            ).fetchall()
            stored = {r[0] for r in rows}
            # nothing anywhere in the registry tables echoes the token
            dumped = conn.execute(
                "select count(*) from lab_members "
                "where token_sha256 like %s", (f"%{member}%",)).fetchone()[0]
            ws_json = conn.execute(
                "select workspace::text from lab_workspaces where ws_id=%s",
                (ws_id,)).fetchone()[0]

        self.assertIn(token_digest(member), stored)
        self.assertIn(token_digest(owner), stored)
        self.assertEqual(dumped, 0)
        self.assertNotIn(member, ws_json)
        self.assertNotIn(owner, ws_json)

    def test_a_paid_plan_survives_a_restart(self) -> None:
        """A plan a restart reverted to free is a billing incident."""
        ws_id, _ = self._workspace()
        catalog = {"free": {"name": "Community"},
                   "team": {"name": "Team Workspace", "capabilities": ["hosted_execution"]}}
        self.registry.plan_catalog = catalog
        self.registry.apply_plan(ws_id, "team")

        reopened = self.make()
        reopened.plan_catalog = catalog
        workspace = reopened.get(ws_id)
        self.assertIsNotNone(workspace)
        self.assertEqual(workspace.plan["name"], "Team Workspace")  # type: ignore[union-attr]
        self.assertEqual(
            workspace.subscription["plan_id"], "team")  # type: ignore[union-attr]

    def test_the_audit_log_outlives_the_process(self) -> None:
        ws_id, _ = self._workspace()
        self.registry.audit(ws_id, "admin", "PUT /suites/x", 1000.0, "detail")
        self.registry.audit(ws_id, "member", "POST /runs", 1001.0)

        entries = self.make().audit_log(ws_id)
        self.assertEqual([e["action"] for e in entries],
                         ["PUT /suites/x", "POST /runs"])
        self.assertEqual(entries[0]["actor_role"], "admin")

    def test_a_reloaded_workspace_does_not_report_two_owners(self) -> None:
        """`members_of` counts the owner implicitly, so persisting the
        workspace's OWN credential as a member row double-counted it. Found by
        driving the real server, not by a unit test — the member count read
        "owner 2" after a restart."""
        ws_id, _ = self._workspace()
        self.registry.add_member(ws_id, "m-" + uuid.uuid4().hex, "member")
        before = self.registry.members_of(ws_id)
        after = self.make().members_of(ws_id)
        self.assertEqual(after, before)
        owners = next(r for r in after if r["role"] == "owner")
        self.assertEqual(owners["count"], 1)

    def test_a_guest_session_is_NOT_persisted(self) -> None:
        """Ephemeral by design — it must not come back from the dead."""
        guest = self.registry.create_guest(ttl_seconds=60)
        token = str(guest["token"])
        self.assertIsNotNone(self.registry.resolve_token(token))
        self.assertIsNone(self.make().resolve_token(token))


@unittest.skipUnless(DSN, "set AXOR_LAB_TEST_POSTGRES_URL to run")
class TestPostgresCatalogKeepsItsForensics(unittest.TestCase):
    """The publication catalog on Postgres must behave EXACTLY as on files.

    Its value is not the storage; it is the chain over it — a two-pass cold load
    that collects every tombstone before admitting any publication, lineage
    tombstones that outrank a surviving sibling, an acceptance restored rather
    than re-minted. That logic is untouched by this migration: only the leaf
    reads and writes changed. These tests are the evidence for that claim,
    running the same scenarios the file-backed suites run.
    """

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from tests.test_evidence_lineage_takedown import _bundle

        self._bundle = _bundle
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # a unique root per test: the object keys are relative to it, so this
        # namespaces one test's rows from another's inside the shared table
        self.root = Path(self.tmp.name) / ("cat_" + uuid.uuid4().hex[:8])

    def _store(self):  # noqa: ANN202
        from lab_server.store import PublicationStore

        return PublicationStore(root=self.root, dsn=DSN)

    def test_a_publication_survives_a_cold_reload(self) -> None:
        bundle, traces = self._bundle()
        published = self._store().publish(bundle, traces, question="q?")
        pid = str(published.publication["publication_id"])

        reloaded = self._store()  # a new store: nothing shared in memory
        self.assertEqual(
            str(reloaded.get(pid).publication["publication_id"]), pid)

    def test_the_acceptance_is_RESTORED_not_re_minted(self) -> None:
        """A persisted acceptance must come back as itself — re-minting it under
        a rotated key would rewrite the attestation of an already-published
        result."""
        bundle, traces = self._bundle()
        published = self._store().publish(bundle, traces, question="q?")
        pid = str(published.publication["publication_id"])
        minted = published.acceptance

        restored = self._store().get(pid).acceptance
        self.assertEqual(restored, minted)

    def test_a_takedown_outranks_a_reload(self) -> None:
        bundle, traces = self._bundle()
        store = self._store()
        published = store.publish(bundle, traces, question="q?")
        pid = str(published.publication["publication_id"])
        store.takedown(pid)

        reloaded = self._store()
        self.assertTrue(reloaded.is_taken_down(pid))
        with self.assertRaises(NotFound):
            reloaded.get(pid)

    def test_a_taken_down_lineage_cannot_be_republished_after_a_reload(self) -> None:
        """The property the two-pass cold load exists for: repackaging the same
        evidence must not get it back."""
        bundle, traces = self._bundle(bundle_id="b_one", created="2026-07-20T00:00:00+00:00")
        store = self._store()
        published = store.publish(bundle, traces, question="q?")
        store.takedown(str(published.publication["publication_id"]))

        repackaged, traces2 = self._bundle(
            bundle_id="b_two", created="2026-07-21T09:30:00+00:00")
        reloaded = self._store()
        with self.assertRaises(PublishRejected):
            reloaded.publish(repackaged, traces2, question="different question?")

    def test_traces_come_back_with_the_publication(self) -> None:
        bundle, traces = self._bundle()
        published = self._store().publish(bundle, traces, question="q?")
        pid = str(published.publication["publication_id"])
        restored = self._store().get(pid)
        self.assertEqual(sorted(restored.traces), sorted(traces))

    def test_the_catalog_lists_what_was_published(self) -> None:
        bundle, traces = self._bundle()
        published = self._store().publish(bundle, traces, question="q?",
                                          visibility="public")
        pid = str(published.publication["publication_id"])
        listed = [str(s.publication["publication_id"]) for s in self._store().catalog()]
        self.assertIn(pid, listed)
