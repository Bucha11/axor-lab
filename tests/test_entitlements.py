"""Entitlement gate — a workspace's plan bounds what it may do (feature 3/7).

This is the point of features 1-2: durable, isolated tenants give an entitlement
something real to bind to. A plan carries numeric limits (max_suites,
max_artifacts) and capability flags (hosted_execution, private_registry) that
features 4-6 consume. Over a limit is 402 Payment Required — authorised and
well-formed, just not in the plan.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.workspaces import (
    EntitlementError,
    Workspace,
    Workspaces,
    has_capability,
    require_capability,
    require_within,
)
from lab_suite import builtin_registry

ADMIN = "admin-token"


class TestPlanHelpers(unittest.TestCase):
    def _ws(self, plan: dict) -> Workspace:
        return Workspace(id="w", name="w", token="t", plan=plan)

    def test_none_limit_is_unlimited(self) -> None:
        require_within(self._ws({"max_suites": None}), "max_suites", 10_000)  # no raise

    def test_a_numeric_limit_refuses_the_next(self) -> None:
        ws = self._ws({"name": "starter", "max_suites": 2})
        require_within(ws, "max_suites", 1)  # 1 existing, adding a 2nd is fine
        with self.assertRaises(EntitlementError):
            require_within(ws, "max_suites", 2)  # 2 existing, a 3rd is over

    def test_a_missing_capability_is_refused(self) -> None:
        ws = self._ws({"capabilities": ["hosted_execution"]})
        self.assertTrue(has_capability(ws, "hosted_execution"))
        require_capability(ws, "hosted_execution")  # no raise
        with self.assertRaises(EntitlementError):
            require_capability(ws, "private_registry")


class EntitlementApiTestCase(unittest.TestCase):
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

    def _suite(self, suite_id: str) -> dict:
        return {**builtin_registry().get("budget").manifest(),
                "id": suite_id, "origin": "workspace"}


class TestMaxSuitesGate(EntitlementApiTestCase):
    def test_a_starter_plan_caps_new_suites_at_the_limit(self) -> None:
        _, created = self.call("POST", "/workspaces",
                               {"name": "Starter", "plan": {"name": "starter",
                                                            "max_suites": 2}})
        token = created["token"]
        self.assertEqual(self.call("POST", "/suites", {"suite": self._suite("a")},
                                   token=token)[0], 201)
        self.assertEqual(self.call("POST", "/suites", {"suite": self._suite("b")},
                                   token=token)[0], 201)
        # the third is over the plan → 402
        status, payload = self.call("POST", "/suites", {"suite": self._suite("c")},
                                    token=token)
        self.assertEqual(status, 402, payload)
        self.assertIn("max_suites", payload["error"])

    def test_editing_an_existing_suite_does_not_count_against_the_limit(self) -> None:
        _, created = self.call("POST", "/workspaces",
                               {"name": "Starter", "plan": {"max_suites": 1}})
        token = created["token"]
        self.call("POST", "/suites", {"suite": self._suite("only")}, token=token)
        # re-saving the SAME suite is an edit, not a new suite — still allowed at limit
        edited = {**self._suite("only"), "description": "edited"}
        self.assertEqual(
            self.call("PUT", "/suites/only", {"suite": edited}, token=token)[0], 200)

    def test_the_default_plan_is_unlimited(self) -> None:
        # admin (default plan) can create well past any starter cap
        for i in range(5):
            self.assertEqual(
                self.call("POST", "/suites", {"suite": self._suite(f"s{i}")})[0], 201)


if __name__ == "__main__":
    unittest.main()
