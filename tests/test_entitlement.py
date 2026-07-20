"""B9 — Private Lab entitlement (cp-monetization.md §1, lab-economics.md §3).

The two lines: safety is free forever (never consults a license); org use is
paid (a non-expired license flagging private_lab). Expiry degrades org
features to read-only but never touches safety.
"""

from __future__ import annotations

import importlib.util
import unittest

from lab_entitlement import FeatureGate, License, ORG_FEATURES, SAFETY_FEATURES
from lab_entitlement.license import parse_license_fields

TODAY = "2026-07-19"
FUTURE = "2027-01-01"
PAST = "2026-01-01"

_HAS_NACL = importlib.util.find_spec("nacl") is not None


def _security_license(expires_at: str = FUTURE) -> License:
    return parse_license_fields({
        "organization": "acme",
        "workspace_tier": "security",
        "modules": {"private_lab": True, "control_plane": True},
        "governed_node_ceiling": 20,
        "self_hosted_runner": True,
        "expires_at": expires_at,
        "features": [],
    })


class TestNodeCeilingRespectsExpiryAndModule(unittest.TestCase):
    """allows_nodes used to check only the numeric ceiling — an expired or
    module-less license kept allowing nodes (review r2, Patch 6)."""

    def test_valid_license_allows_within_ceiling(self) -> None:
        self.assertTrue(_security_license().allows_nodes(10, TODAY))

    def test_over_ceiling_is_denied(self) -> None:
        self.assertFalse(_security_license().allows_nodes(999, TODAY))

    def test_expired_license_denies_even_within_ceiling(self) -> None:
        self.assertFalse(_security_license(expires_at=PAST).allows_nodes(10, TODAY))

    def test_missing_module_denies(self) -> None:
        lic = parse_license_fields({
            "organization": "acme", "workspace_tier": "security",
            "modules": {"control_plane": True},  # no private_lab
            "governed_node_ceiling": 20, "expires_at": FUTURE, "features": [],
        })
        self.assertFalse(lic.allows_nodes(10, TODAY))


class TestSafetyIsFreeForever(unittest.TestCase):
    def test_safety_features_free_with_no_license(self) -> None:
        gate = FeatureGate(license=None)
        for feature in SAFETY_FEATURES:
            self.assertTrue(gate.is_allowed(feature, TODAY), feature)

    def test_safety_features_free_under_expired_license(self) -> None:
        gate = FeatureGate(_security_license(expires_at=PAST))
        for feature in SAFETY_FEATURES:
            self.assertTrue(gate.is_allowed(feature, TODAY), feature)

    def test_gates_and_replay_never_gated(self) -> None:
        # the two most load-bearing safety features
        gate = FeatureGate(license=None)
        self.assertTrue(gate.is_allowed("gates", TODAY))
        self.assertTrue(gate.is_allowed("replay", TODAY))
        self.assertTrue(gate.is_allowed("evidencecase_capture", TODAY))


class TestOrgUseIsPaid(unittest.TestCase):
    def test_org_feature_denied_without_license(self) -> None:
        gate = FeatureGate(license=None)
        for feature in ORG_FEATURES:
            self.assertFalse(gate.is_allowed(feature, TODAY), feature)

    def test_org_feature_allowed_under_security_license(self) -> None:
        gate = FeatureGate(_security_license())
        for feature in ("private_workspace", "scheduled_ci", "compliance_export", "sso"):
            self.assertTrue(gate.is_allowed(feature, TODAY), feature)

    def test_team_tier_unlocks_core_not_heavy_features(self) -> None:
        team = parse_license_fields({
            "organization": "acme", "workspace_tier": "team",
            "modules": {"private_lab": True}, "expires_at": FUTURE,
        })
        gate = FeatureGate(team)
        self.assertTrue(gate.is_allowed("private_workspace", TODAY))
        self.assertFalse(gate.is_allowed("scheduled_ci", TODAY))  # security-tier
        self.assertFalse(gate.is_allowed("sso", TODAY))

    def test_module_flag_required(self) -> None:
        # a license without the private_lab module grants no org features
        no_lab = parse_license_fields({
            "organization": "acme", "workspace_tier": "security",
            "modules": {"control_plane": True}, "expires_at": FUTURE,
        })
        gate = FeatureGate(no_lab)
        self.assertFalse(gate.is_allowed("private_workspace", TODAY))


class TestExpiryDegradesToReadOnly(unittest.TestCase):
    def test_expiry_disables_org_features_but_not_safety(self) -> None:
        gate = FeatureGate(_security_license(expires_at=PAST))
        self.assertFalse(gate.is_allowed("scheduled_ci", TODAY))  # org: off
        self.assertFalse(gate.is_allowed("private_workspace", TODAY))
        self.assertTrue(gate.is_allowed("gates", TODAY))          # safety: on
        self.assertTrue(gate.is_allowed("public_publish", TODAY))

    def test_is_expired_is_lexicographic(self) -> None:
        self.assertTrue(_security_license(expires_at=PAST).is_expired(TODAY))
        self.assertFalse(_security_license(expires_at=FUTURE).is_expired(TODAY))


class TestUnknownFeatureDefaultsPaidDenied(unittest.TestCase):
    def test_unknown_feature_is_denied_not_free(self) -> None:
        gate = FeatureGate(_security_license())
        self.assertFalse(gate.is_allowed("mystery_feature", TODAY))


@unittest.skipUnless(_HAS_NACL, "PyNaCl not installed (optional crypto)")
class TestSignedLicenseRoundTrip(unittest.TestCase):
    def test_sign_then_verify(self) -> None:
        from nacl.signing import SigningKey

        from lab_entitlement.signing import sign_license, verify_license

        key = SigningKey.generate()
        pub = bytes(key.verify_key).hex()
        fields = {
            "organization": "acme", "workspace_tier": "security",
            "modules": {"private_lab": True}, "governed_node_ceiling": 20,
            "self_hosted_runner": True, "expires_at": FUTURE, "features": [],
        }
        signed = sign_license(fields, bytes(key).hex())
        license = verify_license(signed, pub)
        self.assertEqual(license.organization, "acme")
        self.assertTrue(FeatureGate(license).is_allowed("scheduled_ci", TODAY))

    def test_tampered_license_is_rejected(self) -> None:
        import json

        from nacl.signing import SigningKey

        from lab_entitlement.errors import LicenseError
        from lab_entitlement.signing import sign_license, verify_license

        key = SigningKey.generate()
        pub = bytes(key.verify_key).hex()
        signed = sign_license(
            {"organization": "acme", "workspace_tier": "team",
             "modules": {"private_lab": True}, "expires_at": FUTURE}, bytes(key).hex()
        )
        data = json.loads(signed)
        data["license"]["workspace_tier"] = "enterprise"  # tamper post-signature
        with self.assertRaises(LicenseError):
            verify_license(json.dumps(data), pub)


if __name__ == "__main__":
    unittest.main()
