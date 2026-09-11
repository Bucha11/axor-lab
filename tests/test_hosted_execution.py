"""Hosted execution — the platform provisions runtimes (feature 4/7).

RFC §16 commercial: instead of the customer connecting their own wrapped agent,
the platform manages a runtime pool. This is the tenant-facing contract for it —
the API, the lifecycle, and the entitlement gate (the hosted_execution capability
plus a plan pool size). The compute backend that actually claims a hosted
runtime's jobs is a deployment concern; a provisioned runtime is a real,
gated, listed record with its own ingest key.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.workspaces import Workspace, Workspaces

ADMIN = "admin-token"


class HostedTestCase(unittest.TestCase):
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

    def _tenant(self, plan: dict) -> str:
        _, created = self.call("POST", "/workspaces", {"name": "T", "plan": plan})
        return created["token"]


class TestProvisioning(HostedTestCase):
    def test_a_capable_plan_provisions_a_hosted_runtime(self) -> None:
        token = self._tenant({"capabilities": ["hosted_execution"],
                              "max_hosted_runtimes": 2})
        status, conn = self.call("POST", "/hosted-runtimes", {"model": "gpt"},
                                 token=token)
        self.assertEqual(status, 201, conn)
        self.assertTrue(conn["ingest_key"] and conn["runtime_ref"])

    def test_it_is_listed_as_hosted_and_kept_apart_from_connected(self) -> None:
        token = self._tenant({"capabilities": ["hosted_execution"],
                              "max_hosted_runtimes": 5})
        self.call("POST", "/hosted-runtimes", {"model": "gpt"}, token=token)
        self.call("POST", "/runtimes/connect", {"model": "byo"}, token=token)
        _, hosted = self.call("GET", "/hosted-runtimes", token=token)
        _, allrt = self.call("GET", "/runtimes", token=token)
        self.assertEqual(len(hosted["hosted_runtimes"]), 1)
        self.assertTrue(hosted["hosted_runtimes"][0]["hosted"])
        self.assertEqual(len(allrt["runtimes"]), 2)  # both, hosted + connected

    def test_a_hosted_runtime_can_claim_and_complete_like_any_runtime(self) -> None:
        token = self._tenant({"capabilities": ["hosted_execution"],
                              "max_hosted_runtimes": 1})
        conn = self.call("POST", "/hosted-runtimes", {"model": "gpt"}, token=token)[1]
        # it dispatches and the hosted runtime's key polls its jobs
        self.call("POST", "/suites/budget/dispatch",
                  {"runtime_ref": conn["runtime_ref"]}, token=token)
        status, jobs = self.call("GET", "/runtime/jobs", token=conn["ingest_key"])
        self.assertEqual(status, 200)
        self.assertTrue(jobs["jobs"])


class TestGating(HostedTestCase):
    def test_a_plan_without_the_capability_is_refused(self) -> None:
        token = self._tenant({"capabilities": [], "max_hosted_runtimes": 5})
        status, payload = self.call("POST", "/hosted-runtimes", {"model": "gpt"},
                                    token=token)
        self.assertEqual(status, 402, payload)
        self.assertIn("hosted_execution", payload["error"])

    def test_the_pool_size_is_enforced(self) -> None:
        token = self._tenant({"capabilities": ["hosted_execution"],
                              "max_hosted_runtimes": 1})
        self.assertEqual(
            self.call("POST", "/hosted-runtimes", {"model": "a"}, token=token)[0], 201)
        status, payload = self.call("POST", "/hosted-runtimes", {"model": "b"},
                                    token=token)
        self.assertEqual(status, 402, payload)
        self.assertIn("max_hosted_runtimes", payload["error"])


if __name__ == "__main__":
    unittest.main()
