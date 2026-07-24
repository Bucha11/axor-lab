"""The paid Security-Workspace features and the hosted-mode entitlement gate.

Self-hosted (hosted_mode off) is unlimited and ungated — history, approvals and
compliance export all work with no license. Hosted (hosted_mode on) enforces the
Security tier: the same endpoints answer 402 below it. The workflow spine is the
append-only audit log: an incident import, an approval and a report export are
each recorded, and the compliance report aggregates them.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lab_server import make_server
from lab_server.license import License
from tests.test_incidents_api import _incident_fixture, _package

WRITE_TOKEN = "wtok"


def _license(tier: str) -> License:
    return License(
        organization="Acme", workspace_tier=tier,
        modules=("private_lab",), governed_node_ceiling=0,
        expires_at="2999-01-01",
    )


class _ServerCase(unittest.TestCase):
    def _serve(self, *, hosted: bool, license_obj: License | None) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = make_server(
            Path(tmp.name) / "store", host="127.0.0.1", port=0,
            write_token=WRITE_TOKEN, license_obj=license_obj, hosted_mode=hosted,
        )
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, base: str, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _post(self, base: str, path: str, body: dict, token: str | None = WRITE_TOKEN) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            base + path, data=json.dumps(body).encode(), headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


class TestHostedGate(_ServerCase):
    def test_self_hosted_is_ungated(self) -> None:
        base = self._serve(hosted=False, license_obj=None)
        self.assertEqual(self._get(base, "/api/audit")[0], 200)
        self.assertEqual(self._get(base, "/api/compliance/report")[0], 200)

    def test_hosted_without_license_is_402(self) -> None:
        base = self._serve(hosted=True, license_obj=None)
        self.assertEqual(self._get(base, "/api/audit")[0], 402)
        self.assertEqual(self._get(base, "/api/compliance/report")[0], 402)
        self.assertEqual(self._get(base, "/api/regression")[0], 402)
        self.assertEqual(self._post(base, "/api/regression/run", {})[0], 402)

    def test_hosted_below_tier_is_402(self) -> None:
        base = self._serve(hosted=True, license_obj=_license("team"))
        code, body = self._get(base, "/api/audit")
        self.assertEqual(code, 402)
        self.assertIn("security", body["error"])

    def test_hosted_security_tier_ok(self) -> None:
        base = self._serve(hosted=True, license_obj=_license("security"))
        self.assertEqual(self._get(base, "/api/audit")[0], 200)
        self.assertEqual(self._get(base, "/api/compliance/report")[0], 200)


class TestWorkflowSpine(_ServerCase):
    def setUp(self) -> None:
        self.base = self._serve(hosted=False, license_obj=None)
        self.trace, self.scenario, self.manifests, self.condition = _incident_fixture()

    def _import(self) -> str:
        pkg = _package(self.trace, self.scenario, self.manifests, self.condition)
        code, body = self._post(self.base, "/api/incidents", pkg)
        self.assertEqual(code, 201, body)
        return body["incident_id"]

    def test_import_is_logged_and_approval_flows_to_audit(self) -> None:
        incident_id = self._import()
        # the import left an audit entry
        _, audit = self._get(self.base, "/api/audit")
        actions = [e["action"] for e in audit["events"]]
        self.assertIn("incident_imported", actions)

        # approve it
        code, body = self._post(
            self.base, f"/api/incidents/{incident_id}/approve",
            {"approver": "alice", "note": "looks real"},
        )
        self.assertEqual(code, 200, body)
        self.assertTrue(body["approved"])
        self.assertEqual(body["approval"]["actor"], "alice")

        # the approval is in the audit history and on the incident listing
        _, audit2 = self._get(self.base, "/api/audit")
        approvals = [e for e in audit2["events"] if e["action"] == "approval_granted"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["target"], incident_id)
        _, listing = self._get(self.base, "/api/incidents")
        row = next(i for i in listing["incidents"] if i["incident_id"] == incident_id)
        self.assertTrue(row["approved"])

    def test_compliance_report_aggregates_the_window(self) -> None:
        incident_id = self._import()
        self._post(self.base, f"/api/incidents/{incident_id}/approve", {"approver": "bob"})
        code, report = self._get(self.base, "/api/compliance/report")
        self.assertEqual(code, 200)
        self.assertEqual(report["schema_version"], "axor-lab-compliance/v1")
        self.assertEqual(report["action_counts"]["incident_imported"], 1)
        self.assertEqual(report["action_counts"]["approval_granted"], 1)
        row = next(i for i in report["incidents"] if i["incident_id"] == incident_id)
        self.assertTrue(row["approved"])
        self.assertEqual(row["approvals"][0]["actor"], "bob")

    def test_approve_unknown_incident_is_404(self) -> None:
        code, _ = self._post(self.base, "/api/incidents/i_missing/approve", {"approver": "x"})
        self.assertEqual(code, 404)

    def test_pin_then_regression_run_closes_the_chain(self) -> None:
        incident_id = self._import()
        # pin the incident's verdict into the corpus
        code, body = self._post(self.base, f"/api/incidents/{incident_id}/pin", {})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["pinned"])
        side = body["pin"]["side"]
        self.assertIn(side, ("must_block", "must_pass"))

        # it shows in the corpus and on the incident listing
        _, corpus = self._get(self.base, "/api/regression")
        self.assertEqual(len(corpus["pins"]), 1)
        _, listing = self._get(self.base, "/api/incidents")
        self.assertTrue(next(i for i in listing["incidents"] if i["incident_id"] == incident_id)["pinned"])

        # a regression run re-verifies it (recorded kernel → reproduces → held/passed)
        code, report = self._post(self.base, "/api/regression/run", {})
        self.assertEqual(code, 200, report)
        outcomes = {r["outcome"] for r in report["rows"]}
        self.assertTrue(outcomes <= {"held", "passed"}, report)
        self.assertTrue(report["safe_to_ship"])
        self.assertEqual(report["regressed"], 0)
        self.assertEqual(report["escaped"], 0)

        # the chain is now auditable: pin + run are in the log and the report
        _, audit = self._get(self.base, "/api/audit")
        actions = [e["action"] for e in audit["events"]]
        self.assertIn("incident_pinned", actions)
        self.assertIn("regression_run", actions)
        _, compliance = self._get(self.base, "/api/compliance/report")
        self.assertEqual(compliance["action_counts"]["incident_pinned"], 1)
        self.assertEqual(compliance["action_counts"]["regression_run"], 1)
        self.assertTrue(next(
            i for i in compliance["incidents"] if i["incident_id"] == incident_id
        )["pinned"])

    def test_pin_unknown_incident_is_404(self) -> None:
        code, _ = self._post(self.base, "/api/incidents/i_missing/pin", {})
        self.assertEqual(code, 404)

    def test_approve_requires_write_token(self) -> None:
        incident_id = self._import()
        code, _ = self._post(
            self.base, f"/api/incidents/{incident_id}/approve", {"approver": "x"}, token=None,
        )
        self.assertEqual(code, 401)


if __name__ == "__main__":
    unittest.main()
