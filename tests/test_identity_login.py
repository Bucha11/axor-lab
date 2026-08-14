"""Logging into a hosted lab_server with an axor-identity access token.

The server is handed the identity JWKS. A request may then authenticate with an
EdDSA token instead of a static workspace token: its `org` claim provisions and
selects a workspace, its `role` drives RBAC, its `tier` selects the plan. Static
tokens keep working. Malformed / expired / untrusted tokens are refused.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from lab_server.runtime_jobs import make_runtime_server  # noqa: E402
from lab_server.workspaces import Workspace, Workspaces  # noqa: E402

ADMIN = "static-admin-token"
KID = "k1"


def _jwk(public_key: Ed25519PrivateKey) -> dict:
    raw = public_key.public_bytes(serialization.Encoding.Raw,
                                  serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
            "alg": "EdDSA", "kid": KID}


_CATALOG = {
    "free": {"name": "free", "price_usd": 0, "max_suites": 1, "max_artifacts": 0,
             "max_hosted_runtimes": 0, "capabilities": []},
    "team": {"name": "team", "price_usd": 299, "max_suites": None,
             "max_artifacts": 100, "max_hosted_runtimes": 5,
             "capabilities": ["hosted_execution"]},
}


class IdentityLoginTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._priv = Ed25519PrivateKey.generate()
        self._jwks = {"keys": [_jwk(self._priv.public_key())]}
        self.workspaces = Workspaces(plan_catalog=_CATALOG)
        # a static workspace still exists alongside identity login
        self.workspaces.add(Workspace(id="static", name="Static", token=ADMIN,
                                      is_admin=True))
        self.server = make_runtime_server(
            port=0, control_token=ADMIN, workspaces=self.workspaces,
            identity_jwks=self._jwks)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def mint(self, **over: object) -> str:
        now = int(time.time())
        claims = {"iss": "axor-identity", "sub": "usr_1", "eml": "a@acme.io",
                  "org": "org_acme", "role": "owner", "tier": "team",
                  "iat": now, "exp": now + 900}
        claims.update(over)
        return jwt.encode(claims, self._priv, algorithm="EdDSA",
                          headers={"kid": KID})

    def call(self, method: str, path: str, token: str,
             body: object = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class TestLoginRouting(IdentityLoginTestCase):
    def test_a_valid_token_provisions_and_selects_the_org_workspace(self) -> None:
        status, payload = self.call("GET", "/workspaces/current", self.mint())
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["id"], "org_acme")   # workspace id == org
        self.assertEqual(payload["org"], "org_acme")
        self.assertEqual(payload["role"], "owner")

    def test_the_tier_claim_selects_the_plan(self) -> None:
        _, payload = self.call("GET", "/workspaces/current", self.mint(tier="team"))
        self.assertEqual(payload["subscription"], {"plan_id": "team", "status": "active"})
        self.assertEqual(payload["plan"]["name"], "team")

    def test_an_unknown_tier_fails_closed_to_free(self) -> None:
        _, payload = self.call("GET", "/workspaces/current",
                               self.mint(org="org_x", tier="platinum"))
        self.assertEqual(payload["plan"]["name"], "free")

    def test_two_logins_for_one_org_share_the_workspace(self) -> None:
        self.call("GET", "/workspaces/current", self.mint(sub="usr_1"))
        self.call("GET", "/workspaces/current", self.mint(sub="usr_2", role="member"))
        ids = {w.id for w in self.workspaces.list()}
        self.assertIn("org_acme", ids)
        self.assertEqual(sum(w.id == "org_acme" for w in self.workspaces.list()), 1)

    def test_the_static_token_still_works(self) -> None:
        status, payload = self.call("GET", "/workspaces/current", ADMIN)
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "static")


class TestRbacFromClaims(IdentityLoginTestCase):
    def test_a_viewer_token_cannot_mutate(self) -> None:
        status, _ = self.call("POST", "/runtimes/connect",
                              self.mint(role="viewer"), {"runtime_label": "x"})
        self.assertEqual(status, 403)

    def test_a_member_token_can_mutate(self) -> None:
        status, _ = self.call("POST", "/runtimes/connect",
                              self.mint(role="member"), {"runtime_label": "x"})
        self.assertEqual(status, 201)


class TestRejections(IdentityLoginTestCase):
    def test_a_tampered_token_is_401(self) -> None:
        bad = self.mint()[:-4] + "AAAA"
        self.assertEqual(self.call("GET", "/workspaces/current", bad)[0], 401)

    def test_a_wrong_issuer_token_is_401(self) -> None:
        self.assertEqual(
            self.call("GET", "/workspaces/current", self.mint(iss="evil"))[0], 401)

    def test_an_expired_token_is_401(self) -> None:
        past = int(time.time()) - 3600
        self.assertEqual(
            self.call("GET", "/workspaces/current",
                      self.mint(iat=past, exp=past + 60))[0], 401)

    def test_a_token_signed_by_another_key_is_401(self) -> None:
        attacker = Ed25519PrivateKey.generate()
        now = int(time.time())
        forged = jwt.encode(
            {"iss": "axor-identity", "sub": "u", "eml": "e@x.io", "org": "org_acme",
             "role": "owner", "tier": "team", "iat": now, "exp": now + 900},
            attacker, algorithm="EdDSA", headers={"kid": KID})
        self.assertEqual(self.call("GET", "/workspaces/current", forged)[0], 401)


if __name__ == "__main__":
    unittest.main()
