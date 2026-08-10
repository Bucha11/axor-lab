"""Artifact registry — durable, versioned, queryable, retained (feature 5/7).

Durability landed in feature 1. A REGISTRY adds: newest-first ordering so a
suite's successive artifacts read as version history, a ?suite= filter for one
suite's history, and plan-bounded retention (max_artifacts) that evicts the
oldest beyond the window — the honest behaviour for a store the customer keeps
writing to.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.screens import ScreenStore, enforce_artifact_retention
from lab_server.workspaces import Workspace, Workspaces
from lab_suite import builtin_registry, run_suite

ADMIN = "admin-token"
CREATED = "2026-08-10T00:00:00+00:00"
ENVIRONMENT = {"model": {"provider": "p", "id": "i"}}


def _artifact(artifact_id: str, created: str, suite_id: str = "budget") -> dict:
    suite = builtin_registry().get("budget")
    manifest = {**suite.manifest(), "id": suite_id}
    run = run_suite(manifest, run_id="seed", suite=suite)
    return run.artifact(artifact_id, created, ENVIRONMENT)


class TestRetentionUnit(unittest.TestCase):
    def test_retention_keeps_the_newest_and_evicts_the_rest(self) -> None:
        store = ScreenStore()
        for i in range(5):
            store.put("artifact", _artifact(f"a{i}", f"2026-08-10T0{i}:00:00+00:00"),
                      id_field="artifact_id")
        evicted = enforce_artifact_retention(store, keep=2)
        self.assertEqual(len(evicted), 3)
        kept = sorted(store.ids("artifact"))
        self.assertEqual(kept, ["a3", "a4"])  # the two newest by created

    def test_none_keeps_everything(self) -> None:
        store = ScreenStore()
        store.put("artifact", _artifact("a", CREATED), id_field="artifact_id")
        self.assertEqual(enforce_artifact_retention(store, keep=None), [])


class RegistryApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspaces = Workspaces()
        self.workspaces.add(Workspace(id="default", name="Admin", token=ADMIN,
                                      is_admin=True))
        self.server = make_runtime_server(
            port=0, control_token=ADMIN, workspaces=self.workspaces)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body: object = None,
             token: str = ADMIN) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class TestVersionHistory(RegistryApiTestCase):
    def test_artifacts_come_back_newest_first(self) -> None:
        self.call("POST", "/artifacts",
                  {"artifact": _artifact("old", "2026-08-10T01:00:00+00:00")})
        self.call("POST", "/artifacts",
                  {"artifact": _artifact("new", "2026-08-10T09:00:00+00:00")})
        _, listing = self.call("GET", "/artifacts")
        ids = [a["artifact_id"] for a in listing["artifacts"]]
        self.assertEqual(ids[:2], ["new", "old"])

    def test_the_suite_filter_returns_one_suites_history(self) -> None:
        self.call("POST", "/artifacts",
                  {"artifact": _artifact("b1", "2026-08-10T01:00:00+00:00", "budget")})
        self.call("POST", "/artifacts",
                  {"artifact": _artifact("o1", "2026-08-10T02:00:00+00:00", "other")})
        _, listing = self.call("GET", "/artifacts?suite=budget")
        ids = [a["artifact_id"] for a in listing["artifacts"]]
        self.assertEqual(ids, ["b1"])
        self.assertEqual(listing["artifacts"][0]["suite_id"], "budget")


class TestRetentionOverHttp(RegistryApiTestCase):
    def test_a_plan_window_evicts_older_artifacts_on_write(self) -> None:
        _, tenant = self.call(
            "POST", "/workspaces",
            {"name": "Starter", "plan": {"name": "starter", "max_artifacts": 2}})
        token = tenant["token"]
        for i in range(4):
            self.assertEqual(self.call(
                "POST", "/artifacts",
                {"artifact": _artifact(f"a{i}", f"2026-08-10T0{i}:00:00+00:00")},
                token=token)[0], 201)
        _, listing = self.call("GET", "/artifacts", token=token)
        ids = sorted(a["artifact_id"] for a in listing["artifacts"])
        self.assertEqual(ids, ["a2", "a3"])  # only the two newest survive


if __name__ == "__main__":
    unittest.main()
