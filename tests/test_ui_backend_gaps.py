"""Backend answers the web client was misreading.

Four of them:
  - /auth/status did not say whether identity login is configured, so the login
    screen offered an email/password form against a server that could never
    accept the token it produced;
  - POST /workspaces read `plan` only as a dict, and the client sends a catalog
    id — the named plan was dropped and the tenant landed on free;
  - a remote publish server's 401/403 was passed straight through, and the web
    client read it as its OWN session expiring and logged the user out;
  - GET /registry/suites/{id} was declared and had a client method, and no
    handler — an org-shared suite could be listed and never opened.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from lab_server.runtime_jobs import (
    RuntimeJobStore,
    _remote_publish_failure,
    make_runtime_server,
)
from lab_server.screens import ScreenStore
from lab_server.workspaces import Workspace, Workspaces

TOKEN = "ctl"
CATALOG = {
    "free": {"name": "Free", "price_usd": 0, "max_suites": 3,
             "max_artifacts": 10, "max_hosted_runtimes": 0, "capabilities": []},
    "team_2026": {"name": "Team", "price_usd": 99, "max_suites": 25,
                  "max_artifacts": 200, "max_hosted_runtimes": 2,
                  "capabilities": ["private_registry"]},
}


class _Server:
    def __init__(self, **kwargs: object) -> None:
        self.workspaces = Workspaces(plan_catalog=dict(CATALOG))
        self.workspaces.add(
            Workspace(id="default", name="Default", token=TOKEN, is_admin=True))
        self.server = make_runtime_server(
            port=0, control_token=TOKEN, store=RuntimeJobStore(),
            screens=ScreenStore(), workspaces=self.workspaces, **kwargs)  # type: ignore[arg-type]
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, method: str, path: str, body: object = None,
             token: str | None = TOKEN) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TestAuthStatusSaysWhetherIdentityIsConfigured(unittest.TestCase):
    def test_static_tokens_only(self) -> None:
        api = _Server()
        self.addCleanup(api.close)
        status, payload = api.call("GET", "/auth/status", token=None)
        self.assertEqual(status, 200)
        self.assertIs(payload["identity"], False)

    def test_identity_configured(self) -> None:
        api = _Server(identity_jwks={"keys": []})
        self.addCleanup(api.close)
        status, payload = api.call("GET", "/auth/status", token=None)
        self.assertEqual(status, 200)
        self.assertIs(payload["identity"], True)


class TestCreateWorkspaceTakesAPlanId(unittest.TestCase):
    def setUp(self) -> None:
        self.api = _Server()
        self.addCleanup(self.api.close)

    def test_a_named_plan_is_applied_and_subscribed(self) -> None:
        status, created = self.api.call(
            "POST", "/workspaces", {"name": "Acme", "plan": "team_2026"})
        self.assertEqual(status, 201, created)
        self.assertEqual(created["plan"]["capabilities"], ["private_registry"])
        self.assertEqual(created["subscription"]["plan_id"], "team_2026")
        # the token is shown once, in this response
        self.assertTrue(created["token"])

    def test_no_plan_is_free(self) -> None:
        _, created = self.api.call("POST", "/workspaces", {"name": "Acme"})
        self.assertEqual(created["subscription"]["plan_id"], "free")
        self.assertEqual(created["plan"]["capabilities"], [])

    def test_an_unknown_plan_is_refused_not_dropped(self) -> None:
        status, payload = self.api.call(
            "POST", "/workspaces", {"name": "Acme", "plan": "Team"})
        self.assertEqual(status, 400)
        self.assertIn("unknown plan", payload["error"])


class TestRemotePublishAuthIsNotThisSessionsAuth(unittest.TestCase):
    def test_remote_401_and_403_become_502(self) -> None:
        for upstream in (401, 403):
            status, message = _remote_publish_failure(
                upstream, json.dumps({"error": "bad write token"}))
            self.assertEqual(status, 502)
            self.assertIn("bad write token", message)
            self.assertIn(str(upstream), message)

    def test_other_rejections_keep_their_status_and_read_the_error(self) -> None:
        status, message = _remote_publish_failure(
            422, json.dumps({"error": "trace t1 does not replay"}))
        self.assertEqual((status, message), (422, "trace t1 does not replay"))

    def test_a_non_json_body_is_kept_verbatim(self) -> None:
        self.assertEqual(_remote_publish_failure(None, "<html>"), (502, "<html>"))
        self.assertEqual(
            _remote_publish_failure(500, ""),
            (500, "the publish handshake was rejected"))

    def test_the_route_answers_502_for_a_remote_401(self) -> None:
        """Through the real route: the artifact exists, the handshake is refused
        upstream with 401, and this server does NOT answer 401."""
        import importlib.util

        if importlib.util.find_spec("axor_wrap") is None:
            self.skipTest("axor-wrap not installed")
        from lab_service import Outcome, UploadResult
        from tests.test_export_from_the_hosted_face import _Face

        face = _Face()
        self.addCleanup(face.close)
        artifact_id = face.dispatch_and_collect()
        refused = UploadResult(outcome=Outcome.FAILURE, status=401,
                               error=json.dumps({"error": "invalid token"}))
        with mock.patch("lab_service.upload_publication", return_value=refused) as up:
            status, _, body = face.call(
                "POST", f"/artifacts/{artifact_id}/publish",
                {"question": "q?", "server": "http://remote.invalid",
                 "token": "remote-write-token"})
        self.assertEqual(status, 502, body)
        self.assertIn("invalid token", json.loads(body)["error"])
        # the server write token travels to the remote handshake
        self.assertEqual(up.call_args.kwargs["token"], "remote-write-token")


class TestOneOrgRegistrySuiteOpens(unittest.TestCase):
    """GET /registry/suites/{id} was declared and never answered."""

    def test_open_isolated_and_missing(self) -> None:
        from tests.test_private_registry import RegistryTestCase

        case = RegistryTestCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.assertEqual(case._publish("acme-a-t")[0], 201)
        # a sibling workspace in the same org opens it
        status, suite = case.call("GET", "/registry/suites/shared-suite", token="acme-b-t")
        self.assertEqual(status, 200, suite)
        self.assertEqual(suite["name"], "Shared")
        # another org's registry does not have it
        self.assertEqual(
            case.call("GET", "/registry/suites/shared-suite", token="globex-t")[0], 404)
        # no org at all: nothing to read, not someone else's
        self.assertEqual(
            case.call("GET", "/registry/suites/shared-suite", token="lone-t")[0], 404)


if __name__ == "__main__":
    unittest.main()
