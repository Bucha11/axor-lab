"""Billing — how a workspace actually GETS the commercial features.

The entitlement gate (feature 3) reads a workspace's active plan; billing is
what changes that plan. The honest architecture: the server never touches a card.
A customer picks a plan and opens a checkout (the provider hosts the payment
page); on a settled payment the provider calls /billing/webhook, which activates
the subscription and applies the bought plan, opening the gate. A lapsed payment
falls the workspace back to free — paid features close, they do not linger.

An admin may also grant a plan directly (comp / enterprise / trial) with no
payment.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.workspaces import PLAN_CATALOG, Workspace, Workspaces, plan_limit
from lab_suite import builtin_registry

ADMIN = "admin-token"
WEBHOOK = "whsec_test"


class BillingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspaces = Workspaces()
        self.workspaces.add(Workspace(id="default", name="Admin", token=ADMIN,
                                      is_admin=True))
        self.server = make_runtime_server(
            port=0, control_token=ADMIN, workspaces=self.workspaces,
            billing_webhook_secret=WEBHOOK)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body: object = None,
             token: str = ADMIN, headers: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            request.add_header(k, v)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _free_tenant(self) -> str:
        # a workspace created on the free plan from the catalog
        _, created = self.call("POST", "/workspaces",
                               {"name": "T", "plan": dict(PLAN_CATALOG["free"])})
        return created["token"]

    def _suite(self, suite_id: str) -> dict:
        return {**builtin_registry().get("budget").manifest(),
                "id": suite_id, "origin": "workspace"}


class TestCatalog(BillingTestCase):
    def test_the_plan_catalog_is_offered(self) -> None:
        status, payload = self.call("GET", "/billing/plans")
        self.assertEqual(status, 200)
        names = {p["name"] for p in payload["plans"]}
        self.assertEqual(names, {"free", "starter", "pro"})


class TestPurchaseFlow(BillingTestCase):
    def test_free_blocks_then_a_paid_plan_opens_the_gate(self) -> None:
        token = self._free_tenant()
        # free plan: hosted execution is not in capabilities → 402
        self.assertEqual(
            self.call("POST", "/hosted-runtimes", {"model": "m"}, token=token)[0], 402)

        # 1) open a checkout for pro
        status, checkout = self.call("POST", "/billing/checkout",
                                     {"plan_id": "pro"}, token=token)
        self.assertEqual(status, 201, checkout)
        self.assertTrue(checkout["checkout_url"].startswith("https://"))
        session_id = checkout["session_id"]

        # 2) the provider confirms payment via the webhook (server↔provider,
        #    secret-gated, no control token)
        status, _ = self.call("POST", "/billing/webhook",
                              {"session_id": session_id, "event": "payment_succeeded"},
                              token=None, headers={"X-Billing-Secret": WEBHOOK})
        self.assertEqual(status, 200)

        # 3) the gate is now open — hosted execution is granted by the pro plan
        self.assertEqual(
            self.call("POST", "/hosted-runtimes", {"model": "m"}, token=token)[0], 201)
        _, sub = self.call("GET", "/workspaces/current/subscription", token=token)
        self.assertEqual(sub["subscription"], {"plan_id": "pro", "status": "active"})

    def test_a_failed_payment_lapses_to_free(self) -> None:
        token = self._free_tenant()
        _, checkout = self.call("POST", "/billing/checkout", {"plan_id": "pro"},
                                token=token)
        # activate, then fail a subsequent payment
        self.call("POST", "/billing/webhook",
                  {"session_id": checkout["session_id"], "event": "payment_succeeded"},
                  token=None, headers={"X-Billing-Secret": WEBHOOK})
        self.call("POST", "/billing/webhook",
                  {"session_id": checkout["session_id"], "event": "payment_failed"},
                  token=None, headers={"X-Billing-Secret": WEBHOOK})
        # paid feature closes again
        self.assertEqual(
            self.call("POST", "/hosted-runtimes", {"model": "m"}, token=token)[0], 402)


class TestWebhookSecurity(BillingTestCase):
    def test_a_wrong_secret_is_refused(self) -> None:
        token = self._free_tenant()
        _, checkout = self.call("POST", "/billing/checkout", {"plan_id": "pro"},
                                token=token)
        status, _ = self.call("POST", "/billing/webhook",
                              {"session_id": checkout["session_id"],
                               "event": "payment_succeeded"},
                              token=None, headers={"X-Billing-Secret": "wrong"})
        self.assertEqual(status, 401)
        # and the plan was NOT applied
        self.assertEqual(
            self.call("POST", "/hosted-runtimes", {"model": "m"}, token=token)[0], 402)

    def test_checkout_is_admin_only(self) -> None:
        token = self._free_tenant()
        # provision a plain member and try to buy
        _, member = self.call("POST", "/workspaces/current/members",
                              {"role": "member"}, token=token)
        self.assertEqual(
            self.call("POST", "/billing/checkout", {"plan_id": "pro"},
                      token=member["token"])[0], 403)


class TestManualGrant(BillingTestCase):
    def test_a_server_admin_can_comp_a_plan(self) -> None:
        _, tenant = self.call("POST", "/workspaces",
                              {"name": "Enterprise", "plan": dict(PLAN_CATALOG["free"])})
        # the admin grants starter directly (no payment)
        status, _ = self.call("POST", "/workspaces/current/plan",
                              {"plan_id": "starter", "workspace_id": self._id(tenant)})
        self.assertEqual(status, 200)
        # the granted workspace now has starter limits
        ws = self.workspaces.get(self._id(tenant))
        self.assertEqual(plan_limit(ws, "max_suites"), PLAN_CATALOG["starter"]["max_suites"])

    def _id(self, tenant: dict) -> str:
        return tenant["id"]


if __name__ == "__main__":
    unittest.main()
