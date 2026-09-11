"""RBAC + audit (SSO/compliance) — feature 7/7.

RFC §16 commercial: SSO/RBAC and compliance. A member's token carries a role
within its workspace (owner > admin > member > viewer); SSO/OIDC is the identity
source that MINTS those member tokens — modelled here as member provisioning, so
the authorization substrate is real without an IdP wired. A viewer may read but
not mutate; provisioning members and reading the audit log are admin+. Every
mutation is recorded to the workspace's audit log (compliance).
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.workspaces import Workspace, Workspaces, role_at_least
from lab_suite import builtin_registry

OWNER = "owner-token"


class TestRoleRank(unittest.TestCase):
    def test_ordering(self) -> None:
        self.assertTrue(role_at_least("owner", "viewer"))
        self.assertTrue(role_at_least("member", "member"))
        self.assertFalse(role_at_least("viewer", "member"))
        self.assertFalse(role_at_least("member", "admin"))


class RbacTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspaces = Workspaces()
        self.workspaces.add(Workspace(id="default", name="W", token=OWNER,
                                      is_admin=True))
        self.server = make_runtime_server(
            port=0, control_token=OWNER, workspaces=self.workspaces)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body: object = None,
             token: str = OWNER) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _member(self, role: str) -> str:
        _, created = self.call("POST", "/workspaces/current/members", {"role": role})
        return created["token"]

    def _suite(self, suite_id: str) -> dict:
        return {**builtin_registry().get("budget").manifest(),
                "id": suite_id, "origin": "workspace"}


class TestProvisioning(RbacTestCase):
    def test_owner_mints_a_member_token_with_a_role(self) -> None:
        status, created = self.call("POST", "/workspaces/current/members",
                                    {"role": "viewer"})
        self.assertEqual(status, 201, created)
        self.assertEqual(created["role"], "viewer")
        self.assertTrue(created["token"])

    def test_current_reports_the_callers_role(self) -> None:
        viewer = self._member("viewer")
        _, whoami = self.call("GET", "/workspaces/current", token=viewer)
        self.assertEqual(whoami["role"], "viewer")

    def test_you_cannot_mint_an_owner(self) -> None:
        self.assertEqual(self.call("POST", "/workspaces/current/members",
                                   {"role": "owner"})[0], 400)


class TestReadWriteGate(RbacTestCase):
    def test_a_viewer_can_read_but_not_write(self) -> None:
        viewer = self._member("viewer")
        self.assertEqual(self.call("GET", "/suites", token=viewer)[0], 200)
        status, payload = self.call("POST", "/suites",
                                    {"suite": self._suite("x")}, token=viewer)
        self.assertEqual(status, 403, payload)
        self.assertIn("read-only", payload["error"])

    def test_a_member_can_write(self) -> None:
        member = self._member("member")
        self.assertEqual(self.call("POST", "/suites",
                                   {"suite": self._suite("y")}, token=member)[0], 201)

    def test_provisioning_and_audit_are_admin_only(self) -> None:
        member = self._member("member")
        # a plain member cannot mint members or read the audit log
        self.assertEqual(self.call("POST", "/workspaces/current/members",
                                   {"role": "viewer"}, token=member)[0], 403)
        self.assertEqual(self.call("GET", "/workspaces/current/audit",
                                   token=member)[0], 403)
        # an admin can
        admin = self._member("admin")
        self.assertEqual(self.call("GET", "/workspaces/current/audit",
                                   token=admin)[0], 200)


class TestAuditLog(RbacTestCase):
    def test_mutations_are_recorded_with_the_actor_role_not_the_token(self) -> None:
        member = self._member("member")
        self.call("POST", "/suites", {"suite": self._suite("audited")}, token=member)
        _, log = self.call("GET", "/workspaces/current/audit")
        actions = [e["action"] for e in log["audit"]]
        self.assertTrue(any("POST /suites" in a for a in actions))
        # the log carries roles, never tokens
        self.assertTrue(all("token" not in e for e in log["audit"]))
        self.assertIn("member", [e["actor_role"] for e in log["audit"]])

    def test_reads_are_not_audited(self) -> None:
        self.call("GET", "/suites")
        _, log = self.call("GET", "/workspaces/current/audit")
        self.assertFalse(any("GET /suites" in str(e["action"]) for e in log["audit"]))


if __name__ == "__main__":
    unittest.main()
