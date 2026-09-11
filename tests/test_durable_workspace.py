"""A workspace that vanishes on restart is a demo, not a product.

RFC §16 lists a hosted workspace as the commercial half; the plan (§4.8) said
durable storage is a swap of `ScreenStore`, not of the endpoints. This pins that
swap: with a `persist_dir`, documents survive a process restart, and the
in-memory default is unchanged.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from lab_server.screens import ScreenStore
from lab_suite import builtin_registry


def _suite() -> dict:
    return builtin_registry().get("budget").manifest()


class TestDurability(unittest.TestCase):
    def test_a_saved_suite_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = ScreenStore(persist_dir=tmp)
            edited = {**_suite(), "id": "mine", "name": "Mine", "origin": "workspace"}
            first.put("suite", edited)
            # a NEW store over the same directory — the restart
            second = ScreenStore(persist_dir=tmp)
            self.assertEqual(second.get("suite", "mine")["name"], "Mine")

    def test_a_delete_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = ScreenStore(persist_dir=tmp)
            first.put("suite", {**_suite(), "id": "trash", "origin": "workspace"})
            first.delete("suite", "trash")
            second = ScreenStore(persist_dir=tmp)
            self.assertNotIn("trash", second.ids("suite"))

    def test_ids_with_colons_persist(self) -> None:
        """Artifact and evidence ids carry colons/slashes; the on-disk filename
        must round-trip them."""
        with tempfile.TemporaryDirectory() as tmp:
            case = {
                "schema_version": "evidence-case/v1", "id": "sha256:ab/cd",
                "kind": "latency_spike", "title": "t",
                "trial_ref": {"trial_id": "t1"}, "severity": "low",
            }
            ScreenStore(persist_dir=tmp).put("evidence-case", case)
            reloaded = ScreenStore(persist_dir=tmp)
            self.assertEqual(reloaded.get("evidence-case", "sha256:ab/cd")["title"], "t")

    def test_the_in_memory_default_is_unchanged(self) -> None:
        store = ScreenStore()
        store.put("suite", {**_suite(), "id": "mem", "origin": "workspace"})
        self.assertEqual(store.get("suite", "mem")["id"], "mem")
        # nothing was written anywhere — a fresh in-memory store is empty
        self.assertEqual(ScreenStore().ids("suite"), [])

    def test_a_corrupt_file_is_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScreenStore(persist_dir=tmp)
            store.put("suite", {**_suite(), "id": "ok", "origin": "workspace"})
            (Path(tmp) / "suite" / "garbage.json").write_text("{ not json")
            # a fresh store loads what it can and ignores the corrupt file
            reloaded = ScreenStore(persist_dir=tmp)
            self.assertIn("ok", reloaded.ids("suite"))

    def test_an_edit_overwrites_on_disk_not_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScreenStore(persist_dir=tmp)
            store.put("suite", {**_suite(), "id": "s", "version": "1.0",
                                "origin": "workspace"})
            store.put("suite", {**_suite(), "id": "s", "version": "2.0",
                                "origin": "workspace"})
            reloaded = ScreenStore(persist_dir=tmp)
            self.assertEqual(reloaded.get("suite", "s")["version"], "2.0")
            self.assertEqual(len(reloaded.ids("suite")), 1)


class TestServerWiring(unittest.TestCase):
    def test_make_runtime_server_accepts_a_data_dir(self) -> None:
        import json
        import threading
        import urllib.request

        from lab_server.runtime_jobs import make_runtime_server

        with tempfile.TemporaryDirectory() as tmp:
            server = make_runtime_server(port=0, control_token="t", data_dir=tmp)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def post(path: str, body: dict) -> int:
                req = urllib.request.Request(
                    f"{base}{path}", data=json.dumps(body).encode(), method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", "Bearer t")
                with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
                    return r.status

            self.assertEqual(post("/suites", {"suite": {
                **copy.deepcopy(_suite()), "id": "persisted", "origin": "workspace"}}), 201)
            # the document is on disk under the data dir
            self.assertTrue((Path(tmp) / "suite").exists())
            self.assertTrue(list((Path(tmp) / "suite").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
