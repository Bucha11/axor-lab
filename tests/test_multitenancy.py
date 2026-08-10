"""Many tenants on one server, each isolated — RFC §16 hosted workspace.

The single-token demo could not say WHO a request was, so it could not keep one
tenant's suites out of another's view, and had nothing to hang an entitlement on
(feature 3). A workspace is identified by its token; two workspaces share a
process but not their data.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.workspaces import Workspace, Workspaces
from lab_suite import builtin_registry

ADMIN = "admin-token"


class MultiTenantTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspaces = Workspaces()
        self.workspaces.add(Workspace(id="default", name="Admin", token=ADMIN,
                                      is_admin=True))
        self.server = make_runtime_server(
            port=0, control_token=ADMIN, workspaces=self.workspaces)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body: object = None,
             token: str = ADMIN) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _suite(self, suite_id: str) -> dict:
        return {**builtin_registry().get("budget").manifest(),
                "id": suite_id, "origin": "workspace"}


class TestIdentity(MultiTenantTestCase):
    def test_current_names_the_workspace_behind_the_token(self) -> None:
        status, payload = self.call("GET", "/workspaces/current")
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "default")
        self.assertNotIn("token", payload)  # a client never sees its raw token echoed

    def test_an_unknown_token_is_401(self) -> None:
        status, _ = self.call("GET", "/workspaces/current", token="nope")
        self.assertEqual(status, 401)


class TestProvisioning(MultiTenantTestCase):
    def test_admin_creates_a_workspace_and_gets_its_token(self) -> None:
        status, created = self.call("POST", "/workspaces", {"name": "Acme"})
        self.assertEqual(status, 201, created)
        self.assertTrue(created["token"])
        self.assertEqual(created["name"], "Acme")

    def test_a_non_admin_cannot_create_or_list(self) -> None:
        _, created = self.call("POST", "/workspaces", {"name": "Acme"})
        member = created["token"]
        self.assertEqual(self.call("POST", "/workspaces", {}, token=member)[0], 403)
        self.assertEqual(self.call("GET", "/workspaces", token=member)[0], 403)


class TestIsolation(MultiTenantTestCase):
    def test_one_tenants_suites_are_invisible_to_another(self) -> None:
        """The whole point of a workspace: Acme's suite is not in Globex's
        catalog, and Globex cannot fetch it by id."""
        _, acme = self.call("POST", "/workspaces", {"name": "Acme"})
        _, globex = self.call("POST", "/workspaces", {"name": "Globex"})
        acme_t, globex_t = acme["token"], globex["token"]

        self.assertEqual(
            self.call("POST", "/suites", {"suite": self._suite("acme-secret")},
                      token=acme_t)[0], 201)

        # Acme sees it; Globex does not
        _, acme_cat = self.call("GET", "/suites", token=acme_t)
        _, globex_cat = self.call("GET", "/suites", token=globex_t)
        self.assertIn("acme-secret", [c["id"] for c in acme_cat["suites"]])
        self.assertNotIn("acme-secret", [c["id"] for c in globex_cat["suites"]])
        # and cannot fetch it directly
        self.assertEqual(
            self.call("GET", "/suites/acme-secret", token=globex_t)[0], 404)

    def test_runs_are_isolated_per_workspace(self) -> None:
        _, acme = self.call("POST", "/workspaces", {"name": "Acme"})
        _, globex = self.call("POST", "/workspaces", {"name": "Globex"})
        # Acme connects a runtime and dispatches; Globex sees no runs
        conn = self.call("POST", "/runtimes/connect", {"model": "m"},
                         token=acme["token"])[1]
        self.call("POST", "/suites/budget/dispatch",
                  {"runtime_ref": conn["runtime_ref"]}, token=acme["token"])
        _, acme_runs = self.call("GET", "/runs", token=acme["token"])
        _, globex_runs = self.call("GET", "/runs", token=globex["token"])
        self.assertTrue(acme_runs["runs"])
        self.assertEqual(globex_runs["runs"], [])

    def test_a_runtime_key_resolves_to_its_own_workspace(self) -> None:
        """A runtime carries only its ingest key; the server must route it to the
        workspace that connected it, not another."""
        _, acme = self.call("POST", "/workspaces", {"name": "Acme"})
        conn = self.call("POST", "/runtimes/connect", {"model": "m"},
                         token=acme["token"])[1]
        # the runtime lists ITS jobs with the ingest key, across the same server
        status, jobs = self.call("GET", "/runtime/jobs", token=conn["ingest_key"])
        self.assertEqual(status, 200)
        self.assertIn("jobs", jobs)


if __name__ == "__main__":
    unittest.main()
