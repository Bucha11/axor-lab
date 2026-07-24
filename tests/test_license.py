"""The Lab workspace entitlement (axor-packaging.md §4).

Three layers: the License semantics + gate seams (no crypto), the
GET /api/license/status surface (no crypto — the License is constructed
directly), and the crypto path (sign/verify/tamper) plus the cross-product
guarantee that a license issued by the Control Plane's `axor-license` verifies
here byte-for-byte. The crypto layers skip cleanly without pynacl / axor-core.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lab_server import make_server
from lab_server.license import (
    License,
    LicenseError,
    LicenseRequired,
    require_module,
    require_workspace_tier,
    sign_license,
    verify_license,
)


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


HAS_CRYPTO = _has("nacl") and _has("axor_core")  # verify needs both
HAS_CP = _has("axor_backend")  # the Control Plane license module, for cross-checks

_LIC = {
    "organization": "Acme",
    "workspace_tier": "security",
    "modules": {"private_lab": True, "control_plane": True},
    "governed_node_ceiling": 20,
    "self_hosted_runner": True,
    "expires_at": "2999-01-01",
    "features": ["sso"],
}


class TestLicenseSemantics(unittest.TestCase):
    """No crypto: the dataclass and gate seams."""

    def _lic(self, **over: object) -> License:
        base = dict(
            organization="Acme", workspace_tier="team", modules=("private_lab",),
            governed_node_ceiling=5, expires_at="2999-01-01",
        )
        base.update(over)
        return License(**base)  # type: ignore[arg-type]

    def test_tier_ordering(self) -> None:
        sec = self._lic(workspace_tier="security")
        self.assertTrue(sec.tier_at_least("team"))
        self.assertTrue(sec.tier_at_least("security"))
        team = self._lic(workspace_tier="team")
        self.assertTrue(team.tier_at_least("team"))
        self.assertFalse(team.tier_at_least("security"))

    def test_modules_and_expiry(self) -> None:
        lic = self._lic(modules=("private_lab",))
        self.assertTrue(lic.has_module("private_lab"))
        self.assertFalse(lic.has_module("control_plane"))
        expired = self._lic(expires_at="2000-01-01", features=("sso",))
        self.assertTrue(expired.is_expired("2026-01-01"))
        self.assertFalse(expired.enables("sso", "2026-01-01"))  # read-only after expiry

    def test_gate_seams(self) -> None:
        team = self._lic(workspace_tier="team", modules=("private_lab",))
        require_workspace_tier(team, "team")  # ok
        require_module(team, "private_lab")  # ok
        with self.assertRaises(LicenseRequired):
            require_workspace_tier(team, "security")
        with self.assertRaises(LicenseRequired):
            require_module(team, "control_plane")
        with self.assertRaises(LicenseRequired):
            require_workspace_tier(None, "team")  # None == community


class TestLicenseStatusHTTP(unittest.TestCase):
    """No crypto: /api/license/status for community (no license) and an active
    (directly-constructed) License."""

    def _serve(self, license_obj: License | None) -> tuple[object, str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = make_server(
            Path(tmp.name) / "store", host="127.0.0.1", port=0, license_obj=license_obj,
        )
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, base: str) -> dict[str, object]:
        with urllib.request.urlopen(base + "/api/license/status") as resp:
            return json.loads(resp.read())

    def test_community_without_a_license(self) -> None:
        _, base = self._serve(None)
        body = self._get(base)
        self.assertEqual(body, {"active": False, "workspace_tier": "community"})

    def test_active_license_reports_tier_and_modules(self) -> None:
        lic = License(
            organization="Acme", workspace_tier="security",
            modules=("private_lab", "control_plane"), governed_node_ceiling=10,
            expires_at="2999-01-01", self_hosted_runner=True, features=("sso",),
        )
        _, base = self._serve(lic)
        body = self._get(base)
        self.assertEqual(body["active"], True)
        self.assertEqual(body["organization"], "Acme")
        self.assertEqual(body["workspace_tier"], "security")
        self.assertEqual(body["modules"], {"private_lab": True, "control_plane": True})
        self.assertEqual(body["governed_node_ceiling"], 10)
        self.assertEqual(body["self_hosted_runner"], True)


@unittest.skipUnless(HAS_CRYPTO, "license verify needs pynacl + axor-core")
class TestLicenseCrypto(unittest.TestCase):
    def _keypair(self) -> tuple[str, str]:
        from nacl.signing import SigningKey
        key = SigningKey.generate()
        return bytes(key).hex(), key.verify_key.encode().hex()

    def test_sign_then_verify(self) -> None:
        priv, pub = self._keypair()
        lic = verify_license(sign_license(_LIC, priv), pub)
        self.assertEqual(lic.organization, "Acme")
        self.assertEqual(lic.workspace_tier, "security")
        self.assertEqual(lic.modules, ("private_lab", "control_plane"))
        self.assertEqual(lic.governed_node_ceiling, 20)
        self.assertTrue(lic.self_hosted_runner)

    def test_tampered_ceiling_rejected(self) -> None:
        priv, pub = self._keypair()
        signed = sign_license(_LIC, priv)
        tampered = signed.replace('"governed_node_ceiling": 20',
                                  '"governed_node_ceiling": 9999')
        with self.assertRaises(LicenseError):
            verify_license(tampered, pub)

    def test_wrong_vendor_key_rejected(self) -> None:
        priv, _ = self._keypair()
        _, other_pub = self._keypair()
        with self.assertRaises(LicenseError):
            verify_license(sign_license(_LIC, priv), other_pub)


@unittest.skipUnless(HAS_CRYPTO and HAS_CP, "cross-product check needs axor-backend too")
class TestCrossProductLicense(unittest.TestCase):
    """The variant-2 guarantee: ONE license issued by the Control Plane's
    axor-license verifies here unchanged, and vice versa — the two verifiers do
    not diverge because they canonicalize with the same axor-core kernel over the
    same field set."""

    def _keypair(self) -> tuple[str, str]:
        from nacl.signing import SigningKey
        key = SigningKey.generate()
        return bytes(key).hex(), key.verify_key.encode().hex()

    def test_cp_signed_verifies_in_lab(self) -> None:
        from axor_backend.ee.license import sign_license as cp_sign
        priv, pub = self._keypair()
        lic = verify_license(cp_sign(_LIC, priv), pub)  # CP-signed, Lab-verified
        self.assertEqual(lic.workspace_tier, "security")
        self.assertTrue(lic.has_module("control_plane"))

    def test_lab_signed_verifies_in_cp(self) -> None:
        from axor_backend.ee.license import verify_license as cp_verify
        priv, pub = self._keypair()
        lic = cp_verify(sign_license(_LIC, priv), pub)  # Lab-signed, CP-verified
        self.assertEqual(lic.workspace_tier, "security")
        self.assertEqual(lic.governed_node_ceiling, 20)


if __name__ == "__main__":
    unittest.main()
