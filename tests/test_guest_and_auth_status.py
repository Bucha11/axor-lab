"""Open-mode detection and anonymous, ephemeral guest sessions.

Two product stories:
  - running locally needs no registration — the web asks GET /auth/status and,
    when the server is open, skips the login screen entirely;
  - a hosted deployment may offer a guest session: an anonymous, ephemeral
    workspace on the trial plan that persists nothing and expires.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import make_runtime_server
from lab_server.workspaces import TRIAL_PLAN, Workspaces


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


class TestAuthStatus(unittest.TestCase):
    def test_open_mode_reports_no_auth_required(self) -> None:
        server, base = _serve(control_token=None)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        status, payload = _call(base, "GET", "/auth/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["auth_required"])
        self.assertFalse(payload["guest"])

    def test_secured_mode_reports_auth_required_and_guest_flag(self) -> None:
        server, base = _serve(control_token="ctl", guest_sessions=True)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        # unauthenticated — the whole point is the web asks before it has a token
        status, payload = _call(base, "GET", "/auth/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["auth_required"])
        self.assertTrue(payload["guest"])


class TestGuestSessionHTTP(unittest.TestCase):
    def setUp(self) -> None:
        self.server, self.base = _serve(control_token="ctl", guest_sessions=True)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def test_guest_disabled_is_404(self) -> None:
        server, base = _serve(control_token="ctl")  # guest_sessions defaults off
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertEqual(_call(base, "POST", "/guest-session")[0], 404)

    def test_guest_token_runs_hosted_within_the_trial_limit(self) -> None:
        status, guest = _call(self.base, "POST", "/guest-session")
        self.assertEqual(status, 201, guest)
        token = guest["token"]
        # the guest is on the trial plan
        _, current = _call(self.base, "GET", "/workspaces/current", token)
        self.assertEqual(current["plan"]["name"], "trial")
        # trial grants ONE hosted runtime — a second is over the limit
        self.assertEqual(_call(self.base, "POST", "/hosted-runtimes", token,
                               {"runtime_label": "a"})[0], 201)
        self.assertEqual(_call(self.base, "POST", "/hosted-runtimes", token,
                               {"runtime_label": "b"})[0], 402)

    def test_guest_persists_nothing(self) -> None:
        _, guest = _call(self.base, "POST", "/guest-session")
        # max_artifacts 0 — a guest saves no artifacts
        self.assertEqual(TRIAL_PLAN["max_artifacts"], 0)
        # two guest sessions are isolated workspaces
        _, other = _call(self.base, "POST", "/guest-session")
        self.assertNotEqual(guest["workspace_id"], other["workspace_id"])


class TestGuestExpiry(unittest.TestCase):
    def test_an_expired_guest_no_longer_resolves(self) -> None:
        ws = Workspaces()
        guest = ws.create_guest(ttl_seconds=1800)
        self.assertIsNotNone(ws.resolve_token(guest["token"]))
        # a guest minted already-expired is reaped on the next resolve
        expired = ws.create_guest(ttl_seconds=-1)
        self.assertIsNone(ws.resolve_token(expired["token"]))
        # …and its in-memory workspace is gone
        self.assertIsNone(ws.get(expired["workspace_id"]))

    def test_guest_is_owner_of_its_own_workspace(self) -> None:
        ws = Workspaces()
        guest = ws.create_guest()
        self.assertEqual(ws.role_of(guest["token"]), "owner")
        self.assertEqual(ws.get(guest["workspace_id"]).plan["name"], "trial")


if __name__ == "__main__":
    unittest.main()
