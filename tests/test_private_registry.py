"""Private registries — an org-shared suite catalog (feature 6/7).

RFC §16: a company's workspaces share a private registry, isolated from other
orgs. Building on feature 2's per-workspace isolation, this adds an ORG tier: a
workspace publishes a suite to its org registry (gated by the private_registry
capability), and every workspace in the SAME org sees it — but no other org does.
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


class RegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspaces = Workspaces()
        self.workspaces.add(Workspace(id="default", name="Admin", token=ADMIN,
                                      is_admin=True))
        # two workspaces in org "acme", one in org "globex"
        caps = {"capabilities": ["private_registry"]}
        self.workspaces.add(Workspace(id="acme-a", name="Acme A", token="acme-a-t",
                                      plan=caps, org="acme"))
        self.workspaces.add(Workspace(id="acme-b", name="Acme B", token="acme-b-t",
                                      plan=caps, org="acme"))
        self.workspaces.add(Workspace(id="globex", name="Globex", token="globex-t",
                                      plan=caps, org="globex"))
        self.workspaces.add(Workspace(id="lone", name="Lone", token="lone-t",
                                      plan=caps, org=None))
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

    def _publish(self, token: str) -> tuple[int, dict]:
        # each tenant first saves its own suite, then publishes it to the org
        suite = {**builtin_registry().get("budget").manifest(),
                 "id": "shared-suite", "name": "Shared", "origin": "workspace"}
        self.call("POST", "/suites", {"suite": suite}, token=token)
        return self.call("POST", "/suites/shared-suite/publish", None, token=token)


class TestPublishAndShare(RegistryTestCase):
    def test_a_published_suite_is_visible_across_the_org(self) -> None:
        status, _ = self._publish("acme-a-t")
        self.assertEqual(status, 201)
        # the OTHER acme workspace sees it in the org registry
        _, registry = self.call("GET", "/registry/suites", token="acme-b-t")
        self.assertIn("shared-suite", [s["id"] for s in registry["suites"]])

    def test_another_org_does_not_see_it(self) -> None:
        self._publish("acme-a-t")
        _, globex = self.call("GET", "/registry/suites", token="globex-t")
        self.assertEqual(globex["suites"], [])

    def test_a_workspaces_own_private_suite_is_not_in_the_org_registry_until_published(self) -> None:
        suite = {**builtin_registry().get("budget").manifest(),
                 "id": "unpublished", "origin": "workspace"}
        self.call("POST", "/suites", {"suite": suite}, token="acme-a-t")
        _, registry = self.call("GET", "/registry/suites", token="acme-b-t")
        self.assertNotIn("unpublished", [s["id"] for s in registry["suites"]])


class TestGating(RegistryTestCase):
    def test_publish_without_the_capability_is_refused(self) -> None:
        self.workspaces.add(Workspace(id="nocap", name="NoCap", token="nocap-t",
                                      plan={"capabilities": []}, org="acme"))
        suite = {**builtin_registry().get("budget").manifest(),
                 "id": "x", "origin": "workspace"}
        self.call("POST", "/suites", {"suite": suite}, token="nocap-t")
        status, payload = self.call("POST", "/suites/x/publish", None, token="nocap-t")
        self.assertEqual(status, 402, payload)
        self.assertIn("private_registry", payload["error"])

    def test_a_workspace_with_no_org_cannot_publish(self) -> None:
        suite = {**builtin_registry().get("budget").manifest(),
                 "id": "y", "origin": "workspace"}
        self.call("POST", "/suites", {"suite": suite}, token="lone-t")
        status, payload = self.call("POST", "/suites/y/publish", None, token="lone-t")
        self.assertEqual(status, 409, payload)
        self.assertIn("no org", payload["error"])

    def test_a_lone_workspace_sees_an_empty_registry(self) -> None:
        _, registry = self.call("GET", "/registry/suites", token="lone-t")
        self.assertEqual(registry["suites"], [])


if __name__ == "__main__":
    unittest.main()


class TestSharedScenarios(RegistryTestCase):
    """The org tier for the scenarios `scenario_refs` resolves against.

    Suites already had it; scenarios did not have a registry at all, so the
    Builder's `scenario_refs` field could only ever break a suite. Sharing them
    the same way is what makes a ref worth writing: one team authors a scenario,
    every workspace in the org can build a suite on it.
    """

    def _shared_scenario(self, token: str, name: str = "shared-note") -> tuple[int, dict]:
        manifest = builtin_registry().get("blank").manifest()
        tools = {t["id"]: t for t in manifest["environment"]["tools"]}
        scenario = {**manifest["scenarios"][0], "name": name}
        status, body = self.call(
            "POST", "/scenarios", {"scenario": scenario, "manifests": tools}, token=token)
        self.assertEqual(status, 201, body)
        return self.call("POST", f"/scenarios/{name}/publish", None, token=token)

    def test_a_published_scenario_is_reffable_from_a_sibling_workspace(self) -> None:
        self.assertEqual(self._shared_scenario("acme-a-t")[0], 201)
        # acme-b never saved it, but can build a suite that refs it
        suite = {**builtin_registry().get("blank").manifest(),
                 "id": "b-suite", "name": "B", "scenario_refs": ["shared-note"]}
        self.assertEqual(
            self.call("POST", "/suites/validate", {"suite": suite}, token="acme-b-t")[1],
            {"ok": True, "errors": []},
        )
        status, plan = self.call("POST", "/suites/plan", {"suite": suite}, token="acme-b-t")
        self.assertEqual((status, plan["trials"][-1]), (200, "shared-note:ungoverned:0"))

    def test_another_org_cannot_ref_it(self) -> None:
        self._shared_scenario("acme-a-t")
        suite = {**builtin_registry().get("blank").manifest(),
                 "id": "g-suite", "scenario_refs": ["shared-note"]}
        self.assertEqual(
            self.call("POST", "/suites/validate", {"suite": suite}, token="globex-t")[1],
            {"ok": False,
             "errors": ["[suite] scenario_ref 'shared-note' resolves to nothing"]},
        )

    def test_an_unpublished_scenario_stays_in_its_own_workspace(self) -> None:
        manifest = builtin_registry().get("blank").manifest()
        tools = {t["id"]: t for t in manifest["environment"]["tools"]}
        self.call("POST", "/scenarios",
                  {"scenario": {**manifest["scenarios"][0], "name": "private-one"},
                   "manifests": tools}, token="acme-a-t")
        self.assertEqual(
            [s["name"] for s in self.call("GET", "/scenarios", token="acme-a-t")[1]["scenarios"]],
            ["private-one"],
        )
        self.assertEqual(
            self.call("GET", "/scenarios", token="acme-b-t")[1], {"scenarios": []})

    def test_a_local_scenario_wins_over_the_orgs(self) -> None:
        """Same layering as a saved suite over its built-in."""
        self._shared_scenario("acme-a-t")
        manifest = builtin_registry().get("blank").manifest()
        tools = {t["id"]: t for t in manifest["environment"]["tools"]}
        local = {**manifest["scenarios"][0], "name": "shared-note",
                 "task": "The local version."}
        self.call("POST", "/scenarios", {"scenario": local, "manifests": tools},
                  token="acme-b-t")
        self.assertEqual(
            self.call("GET", "/scenarios/shared-note", token="acme-b-t")[1]["task"],
            "The local version.",
        )
        self.assertEqual(
            self.call("GET", "/scenarios/shared-note", token="acme-a-t")[1]["task"],
            "Record a note.",
        )

    def test_publishing_needs_the_capability_and_an_org(self) -> None:
        self.workspaces.add(Workspace(id="nocap2", name="No cap", token="nocap2-t",
                                      plan={"capabilities": []}, org="acme"))
        self.assertEqual(
            self.call("POST", "/scenarios/x/publish", None, token="nocap2-t")[0], 402)
        self.assertEqual(
            self.call("POST", "/scenarios/x/publish", None, token="lone-t")[0], 409)

