"""What gates the product, and what it charges for.

Audited against a running server with a REALISTIC plan catalog — one whose
display names differ from its ids, which the example catalog does not, and
which is the difference between "billing works" and "billing works on the
fixture".

Six things were wrong, and none of them showed up under the example catalog:
the plan id never left the server, so Subscribe and Grant answered `unknown
plan 'Team'`; a settled checkout could be replayed; a provisioned tenant held
every capability while its subscription read `free`; `/guest-session` minted
unbounded anonymous workspaces and reaped none of them; the webhook secret was
compared with `!=`; and three redundant `import secrets` inside `do_POST` made
the name function-local, so any use in a branch that missed one raised
UnboundLocalError as a bare 500.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_server.screens import ScreenStore
from lab_server.workspaces import Workspace, Workspaces

TOKEN = "ctl"
SECRET = "whsec"

#: An operator's catalog: the id is a SKU, the name is what a customer reads.
#: `EXAMPLE_PLAN_CATALOG` names every plan after its own id, which is exactly
#: why it hid the bug this fixture exists to catch.
CATALOG = {
    "free": {"name": "Free", "price_usd": 0, "max_suites": 3,
             "max_artifacts": 10, "max_hosted_runtimes": 0, "capabilities": []},
    "team_2026": {"name": "Team", "price_usd": 99, "max_suites": 25,
                  "max_artifacts": 200, "max_hosted_runtimes": 2,
                  "capabilities": ["private_registry"]},
}


class _Server:
    def __init__(self, *, guest_sessions: bool = False) -> None:
        self.workspaces = Workspaces(plan_catalog=dict(CATALOG))
        self.workspaces.add(
            Workspace(id="default", name="Default", token=TOKEN, is_admin=True))
        self.server = make_runtime_server(
            port=0, control_token=TOKEN, store=RuntimeJobStore(),
            screens=ScreenStore(), workspaces=self.workspaces,
            billing_webhook_secret=SECRET, guest_sessions=guest_sessions)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, method: str, path: str, body: object = None, *,
             token: str | None = TOKEN, headers: dict[str, str] | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TestThePlanIdReachesTheClient(unittest.TestCase):
    """A catalog is a map of id -> plan, and the route sent the VALUES. So the
    id a client must quote back to /billing/checkout never left the server, the
    screen fell back to the display name, and every catalog whose name differs
    from its id — which is every real one — answered "unknown plan 'Team'"."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.api = _Server()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.api.close()

    def test_every_plan_carries_the_id_checkout_takes(self) -> None:
        _, payload = self.api.call("GET", "/billing/plans")
        by_id = {p["plan_id"]: p["name"] for p in payload["plans"]}
        self.assertEqual(by_id, {"free": "Free", "team_2026": "Team"})

    def test_checkout_accepts_what_the_catalog_advertised(self) -> None:
        _, payload = self.api.call("GET", "/billing/plans")
        for plan in payload["plans"]:
            with self.subTest(plan=plan["plan_id"]):
                status, _ = self.api.call(
                    "POST", "/billing/checkout", {"plan_id": plan["plan_id"]})
                self.assertEqual(status, 201)

    def test_a_display_name_is_not_an_id(self) -> None:
        """The guard on the guard: if this ever starts passing, the fixture has
        drifted back to naming plans after their ids and proves nothing."""
        status, body = self.api.call("POST", "/billing/checkout", {"plan_id": "Team"})
        self.assertEqual(status, 400)
        self.assertIn("unknown plan", body["error"])


class TestTheWebhook(unittest.TestCase):
    def setUp(self) -> None:
        self.api = _Server()
        _, session = self.api.call("POST", "/billing/checkout", {"plan_id": "team_2026"})
        self.session_id = session["session_id"]

    def tearDown(self) -> None:
        self.api.close()

    def _deliver(self, event: str, secret: str = SECRET):
        return self.api.call(
            "POST", "/billing/webhook",
            {"session_id": self.session_id, "event": event},
            token=None, headers={"X-Billing-Secret": secret})

    def test_a_wrong_or_missing_secret_is_a_401_not_a_500(self) -> None:
        """It answered 500 'UnboundLocalError': `do_POST` carried three
        redundant `import secrets` lines in route branches, which makes the name
        local to the WHOLE function, so a branch that reaches none of them
        cannot see the module."""
        self.assertEqual(self._deliver("payment_succeeded", secret="nope")[0], 401)
        status, _ = self.api.call(
            "POST", "/billing/webhook",
            {"session_id": self.session_id, "event": "payment_succeeded"}, token=None)
        self.assertEqual(status, 401)

    def test_a_settled_payment_opens_the_plan(self) -> None:
        self.assertEqual(self._deliver("payment_succeeded")[0], 200)
        _, payload = self.api.call("GET", "/workspaces/current/subscription")
        self.assertEqual(payload["subscription"],
                         {"plan_id": "team_2026", "status": "active"})
        self.assertEqual(payload["plan"]["capabilities"], ["private_registry"])

    def test_a_purchase_delivered_twice_grants_once(self) -> None:
        self.assertEqual(self._deliver("payment_succeeded")[0], 200)
        status, body = self._deliver("payment_succeeded")
        self.assertEqual(status, 409)
        self.assertIn("already settled", body["error"])

    def test_a_stale_lapse_against_a_spent_checkout_is_refused(self) -> None:
        """The initial charge's failure arriving AFTER a later renewal
        succeeded. Acting on it downgrades a paying customer on a redelivery."""
        self._deliver("payment_succeeded")
        status, body = self._deliver("payment_failed")
        self.assertEqual(status, 409)
        self.assertIn("workspace_id", body["error"])
        _, payload = self.api.call("GET", "/workspaces/current/subscription")
        self.assertEqual(payload["subscription"]["status"], "active")

    def test_a_failed_first_charge_falls_back_to_free(self) -> None:
        self.assertEqual(self._deliver("payment_failed")[0], 200)
        _, payload = self.api.call("GET", "/workspaces/current/subscription")
        self.assertEqual(payload["subscription"]["status"], "past_due")
        # the SUBSCRIPTION remembers what was bought; the PLAN does not grant it
        self.assertEqual(payload["plan"]["capabilities"], [])

    def test_a_renewal_failing_later_is_addressed_to_the_workspace(self) -> None:
        """The shape a real provider sends: months after the purchase, against
        the SUBSCRIPTION, with no checkout in sight. This was expressible only
        by replaying the purchase — the one delivery that must not be replayed."""
        self._deliver("payment_succeeded")
        status, _ = self.api.call(
            "POST", "/billing/webhook",
            {"workspace_id": "default", "event": "subscription_canceled"},
            token=None, headers={"X-Billing-Secret": SECRET})
        self.assertEqual(status, 200)
        _, payload = self.api.call("GET", "/workspaces/current/subscription")
        self.assertEqual(payload["subscription"],
                         {"plan_id": "team_2026", "status": "canceled"})
        self.assertEqual(payload["plan"]["capabilities"], [])


class TestANewTenantStartsOnTheFreeTier(unittest.TestCase):
    """A provisioned workspace got DEFAULT_PLAN: unlimited, every capability —
    while its own subscription read `free`. The entitlement gate never fired,
    so there was nothing a plan could sell."""

    def setUp(self) -> None:
        self.api = _Server()
        _, self.tenant = self.api.call("POST", "/workspaces", {"name": "Acme"})

    def tearDown(self) -> None:
        self.api.close()

    def test_the_plan_matches_the_subscription_it_ships_with(self) -> None:
        self.assertEqual(self.tenant["subscription"]["plan_id"], "free")
        self.assertEqual(self.tenant["plan"]["name"], "Free")
        self.assertEqual(self.tenant["plan"]["capabilities"], [])
        self.assertEqual(self.tenant["plan"]["max_suites"], 3)

    def test_a_gated_capability_answers_402(self) -> None:
        status, body = self.api.call(
            "POST", "/hosted-runtimes", {"label": "x"}, token=self.tenant["token"])
        self.assertEqual(status, 402)
        self.assertIn("hosted_execution", body["error"])

    def test_an_explicit_plan_is_still_honoured(self) -> None:
        """An operator provisioning a comped tenant passes the plan outright."""
        _, comped = self.api.call(
            "POST", "/workspaces",
            {"name": "Comped", "plan": dict(CATALOG["team_2026"])})
        self.assertEqual(comped["plan"]["capabilities"], ["private_registry"])


class TestAnonymousTrials(unittest.TestCase):
    """`/guest-session` is the ONE route a deployment exposes with no credential
    at all. Each call held a Workspace and two in-memory stores for half an
    hour, nothing capped the count, and eviction happened only when that guest's
    own token came back — so a session nobody returned to was never reaped."""

    def setUp(self) -> None:
        self.api = _Server(guest_sessions=True)
        self.api.workspaces.MAX_LIVE_GUESTS = 4

    def tearDown(self) -> None:
        self.api.close()

    def _mint(self):
        return self.api.call("POST", "/guest-session", {}, token=None)

    def test_a_trial_needs_no_credential(self) -> None:
        status, session = self._mint()
        self.assertEqual(status, 201)
        self.assertTrue(session["token"])

    def test_minting_stops_at_the_ceiling(self) -> None:
        codes = [self._mint()[0] for _ in range(7)]
        self.assertEqual(codes, [201, 201, 201, 201, 429, 429, 429])

    def test_an_abandoned_trial_is_reclaimed(self) -> None:
        """The common case: the tab closes and the token is never presented
        again, so nothing triggered the old lazy eviction."""
        for _ in range(4):
            self._mint()
        self.assertEqual(self.api.workspaces.live_guests(), 4)
        for ws_id in list(self.api.workspaces._guest_expiry):  # noqa: SLF001
            self.api.workspaces._guest_expiry[ws_id] = time.time() - 1  # noqa: SLF001
        self.assertEqual(self._mint()[0], 201)
        self.assertEqual(self.api.workspaces.live_guests(), 1)

    def test_an_expired_token_no_longer_authenticates(self) -> None:
        _, session = self._mint()
        self.api.workspaces._guest_expiry[str(session["workspace_id"])] = (  # noqa: SLF001
            time.time() - 1)
        status, _ = self.api.call("GET", "/home", token=str(session["token"]))
        self.assertEqual(status, 401)


class TestEveryRouteIsGated(unittest.TestCase):
    def test_only_the_two_deliberately_public_routes_are_ungated(self) -> None:
        """A route that forgets `_require_control` is not a bug you notice —
        it answers 200 for anyone. Read the handler and check, rather than
        trusting that the next one added remembers."""
        import re
        from pathlib import Path

        source = Path("lab_server/runtime_jobs.py").read_text().splitlines()
        gates = ("_require_control", "_require_role", "_runtime_ref",
                 "billing_webhook_secret", "X-Billing-Secret")
        method: str | None = None
        blocks: list[tuple[str, str, int]] = []
        for index, line in enumerate(source):
            named = re.match(r"\s+def (do_(GET|POST|PUT|DELETE))\(", line)
            if named:
                method = named.group(2)
                continue
            if method is None:
                continue
            route = re.match(r'\s+if path == "([^"]+)":', line)
            if route:
                blocks.append((method, route.group(1), index))
                continue
            matched = re.match(r"\s+(?:m = )?(\w*_RE)\.match\(path\)", line)
            if matched:
                blocks.append((method, matched.group(1), index))
        bounds = [b[2] for b in blocks] + [len(source)]
        ungated = [
            f"{meth} {route}"
            for i, (meth, route, start) in enumerate(blocks)
            if not any(g in "\n".join(source[start:bounds[i + 1]]) for g in gates)
        ]
        self.assertGreater(len(blocks), 50, "the scan found no routes — it broke")
        self.assertEqual(
            sorted(ungated),
            # the auth probe answers what the login screen must know before it
            # has a credential; guest-session mints one
            ["GET /auth/status", "POST /guest-session"],
        )


if __name__ == "__main__":
    unittest.main()
