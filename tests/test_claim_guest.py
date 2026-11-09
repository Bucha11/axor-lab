"""A guest's work follows them into their account.

Lab opens on a guest session now, so the moment a visitor signs in is the
moment they have something worth keeping — and it used to be the moment it was
lost: the account is a different workspace and the guest's stores lived only in
memory. `POST /workspaces/current/claim-guest` moves it across.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import RuntimeJobStore, _Job, make_runtime_server
from lab_server.screens import ScreenStore
from lab_server.workspaces import Workspaces
from lab_suite import builtin_registry


def _suite(suite_id: str, name: str = "Mine") -> dict:
    return {**builtin_registry().get("budget").manifest(),
            "id": suite_id, "name": name, "origin": "workspace"}


def _job(job_id: str, state: str) -> _Job:
    return _Job(job_id=job_id, runtime_ref="rt", assignment={}, planned=(), state=state)


class TestClaimGuest(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Workspaces()
        self.ws.ensure_org_workspace("org_acme", "Acme")
        self.guest = self.ws.create_guest()
        self.token = str(self.guest["token"])
        self.guest_id = str(self.guest["workspace_id"])

    def _guest_stores(self) -> tuple[RuntimeJobStore, ScreenStore]:
        return self.ws.stores(self.guest_id)

    def test_suites_and_finished_runs_move_into_the_account(self) -> None:
        jobs, shelf = self._guest_stores()
        shelf.put("suite", _suite("mine"))
        jobs._jobs["run_done"] = _job("run_done", "completed")
        jobs._jobs["run_cancelled"] = _job("run_cancelled", "cancelled")

        result = self.ws.claim_guest(self.token, "org_acme")

        self.assertEqual(result["documents"], {"suite": 1})
        self.assertEqual(result["runs"], 2)
        self.assertEqual(result["left_running"], 0)
        account_jobs, account_shelf = self.ws.stores("org_acme")
        self.assertEqual(account_shelf.get("suite", "mine")["name"], "Mine")
        self.assertEqual(sorted(account_jobs._jobs), ["run_cancelled", "run_done"])

    def test_a_run_still_in_flight_is_reported_not_split(self) -> None:
        jobs, _shelf = self._guest_stores()
        jobs._jobs["run_live"] = _job("run_live", "running")
        result = self.ws.claim_guest(self.token, "org_acme")
        self.assertEqual(result["runs"], 0)
        self.assertEqual(result["left_running"], 1)
        account_jobs, _ = self.ws.stores("org_acme")
        self.assertNotIn("run_live", account_jobs._jobs)

    def test_the_account_keeps_its_own_document_on_an_id_clash(self) -> None:
        _jobs, shelf = self._guest_stores()
        shelf.put("suite", _suite("shared", name="guest copy"))
        _ajobs, account_shelf = self.ws.stores("org_acme")
        account_shelf.put("suite", _suite("shared", name="account copy"))
        result = self.ws.claim_guest(self.token, "org_acme")
        self.assertEqual(result["documents"], {})
        self.assertEqual(account_shelf.get("suite", "shared")["name"], "account copy")

    def test_the_guest_ends_and_cannot_be_claimed_twice(self) -> None:
        self.ws.claim_guest(self.token, "org_acme")
        self.assertIsNone(self.ws.resolve_token(self.token))
        with self.assertRaises(KeyError):
            self.ws.claim_guest(self.token, "org_acme")

    def test_only_a_guest_can_be_claimed_and_only_by_an_account(self) -> None:
        other = self.ws.create_guest()
        with self.assertRaises(ValueError):
            self.ws.claim_guest(self.token, str(other["workspace_id"]))
        with self.assertRaises(KeyError):
            self.ws.claim_guest("not-a-token", "org_acme")


def _serve(**kwargs) -> tuple[object, str]:  # noqa: ANN003
    server = make_runtime_server(port=0, **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _call(base: str, method: str, path: str, token: str | None = None,
          body: object = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class TestClaimGuestRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.server, self.base = _serve(control_token="ctl", guest_sessions=True)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        _s, guest = _call(self.base, "POST", "/guest-session")
        self.guest = guest["token"]

    def test_a_suite_saved_as_a_guest_is_there_after_signing_in(self) -> None:
        status, _ = _call(self.base, "POST", "/suites", self.guest,
                          {"suite": _suite("kept")})
        self.assertEqual(status, 201)
        status, result = _call(self.base, "POST", "/workspaces/current/claim-guest",
                               "ctl", {"guest_token": self.guest})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["documents"], {"suite": 1})
        status, suites = _call(self.base, "GET", "/suites", "ctl")
        self.assertIn("kept", [s["id"] for s in suites["suites"]])
        # the guest session is over
        status, _ = _call(self.base, "GET", "/suites", self.guest)
        self.assertEqual(status, 401)

    def test_it_needs_the_account_credential(self) -> None:
        status, _ = _call(self.base, "POST", "/workspaces/current/claim-guest",
                          None, {"guest_token": self.guest})
        self.assertEqual(status, 401)

    def test_an_unknown_guest_token_is_404(self) -> None:
        status, _ = _call(self.base, "POST", "/workspaces/current/claim-guest",
                          "ctl", {"guest_token": "nope"})
        self.assertEqual(status, 404)

    def test_a_guest_cannot_claim_a_guest(self) -> None:
        _s, other = _call(self.base, "POST", "/guest-session")
        status, _ = _call(self.base, "POST", "/workspaces/current/claim-guest",
                          other["token"], {"guest_token": self.guest})
        self.assertEqual(status, 400)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
