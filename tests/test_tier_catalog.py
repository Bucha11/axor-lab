"""The real tier catalog and the governance execution gate.

`docs/pricing/axor-plans.json` maps the product tiers to entitlements. An
identity tier reaches a workspace as its plan; a suite that DECLARES the
governance capability may only run where the plan grants `governance`.
"""
from __future__ import annotations

import json
import pathlib
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.workspaces import (
    ALL_CAPABILITIES,
    EntitlementError,
    Workspace,
    Workspaces,
    has_capability,
    require_capability,
)

CATALOG_PATH = pathlib.Path(__file__).resolve().parent.parent / "docs" / "pricing" / "axor-plans.json"
CATALOG = json.loads(CATALOG_PATH.read_text())


class TestCatalogShape(unittest.TestCase):
    def test_capabilities_are_known(self) -> None:
        for plan in CATALOG.values():
            for cap in plan["capabilities"]:
                self.assertIn(cap, ALL_CAPABILITIES)

    def test_the_tier_ladder(self) -> None:
        self.assertEqual(CATALOG["free"]["capabilities"], [])
        self.assertIn("hosted_execution", CATALOG["team"]["capabilities"])
        self.assertIn("governance", CATALOG["security"]["capabilities"])
        self.assertIn("control_plane", CATALOG["enterprise"]["capabilities"])
        # governance is Security+, NOT Team
        self.assertNotIn("governance", CATALOG["team"]["capabilities"])
        # a lapse fallback must exist
        self.assertIn("free", CATALOG)

    def test_governance_and_control_plane_are_capabilities(self) -> None:
        self.assertIn("governance", ALL_CAPABILITIES)
        self.assertIn("control_plane", ALL_CAPABILITIES)


class TestTierMapsToPlan(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Workspaces(plan_catalog=CATALOG)

    def test_identity_tier_selects_the_catalog_plan(self) -> None:
        team = self.ws.ensure_org_workspace("org_t", tier="team")
        self.assertTrue(has_capability(team, "hosted_execution"))
        self.assertFalse(has_capability(team, "governance"))
        sec = self.ws.ensure_org_workspace("org_s", tier="security")
        self.assertTrue(has_capability(sec, "governance"))

    def test_community_tier_is_the_free_fallback(self) -> None:
        # "community" is not a catalog key; it fails closed to free
        ws = self.ws.ensure_org_workspace("org_c", tier="community")
        self.assertEqual(ws.plan["capabilities"], [])

    def test_require_capability_gate(self) -> None:
        team = self.ws.ensure_org_workspace("org_t", tier="team")
        with self.assertRaises(EntitlementError):
            require_capability(team, "governance")


ADMIN = "ctl"


class GovernanceDispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Workspaces(plan_catalog=CATALOG)
        self.ws.add(Workspace(id="team", name="Team", token="team-tok"))
        self.ws.add(Workspace(id="sec", name="Sec", token="sec-tok"))
        self.ws.apply_plan("team", "team")
        self.ws.apply_plan("sec", "security")
        self.server = make_runtime_server(port=0, control_token=ADMIN, workspaces=self.ws)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, token: str, body: object = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _runtime(self, token: str) -> str:
        # `agentdojo` is a built-in suite that DECLARES the governance capability
        _, conn = self.call("POST", "/runtimes/connect", token, {"runtime_label": "a"})
        return str(conn["runtime_ref"])

    def test_a_team_plan_cannot_run_a_governance_suite(self) -> None:
        ref = self._runtime("team-tok")
        status, payload = self.call("POST", "/suites/agentdojo/dispatch", "team-tok",
                                    {"runtime_ref": ref})
        self.assertEqual(status, 402, payload)
        self.assertIn("governance", payload["error"])

    def test_a_security_plan_may_run_a_governance_suite(self) -> None:
        ref = self._runtime("sec-tok")
        status, payload = self.call("POST", "/suites/agentdojo/dispatch", "sec-tok",
                                    {"runtime_ref": ref})
        # the governance gate PASSES for a Security plan (the run then starts;
        # whatever else it does, it is not a 402-governance refusal)
        self.assertNotEqual(status, 402, payload)


if __name__ == "__main__":
    unittest.main()
