"""A signed license must still be well-formed (review r11 P1/P2).

A signature proves the vendor authored the bytes, not that the fields are
sane. parse_license_fields used bool(v) and int(...), so a string "false"
became a True flag (bool("false") is True), an unknown tier or a negative
ceiling or a bogus "never" expiry all slipped through, and malformed input
leaked KeyError/TypeError instead of LicenseError. Now every field is validated.
"""

from __future__ import annotations

import unittest

from lab_entitlement.errors import LicenseError
from lab_entitlement.license import parse_license_fields

GOOD = {
    "organization": "acme", "workspace_tier": "security",
    "modules": {"private_lab": True}, "governed_node_ceiling": 20,
    "self_hosted_runner": True, "expires_at": "2027-01-01", "features": [],
}


class TestLicenseValidation(unittest.TestCase):
    def _bad(self, **override):
        fields = {**GOOD, **override}
        with self.assertRaises(LicenseError):
            parse_license_fields(fields)

    def test_good_license_parses(self) -> None:
        lic = parse_license_fields(GOOD)
        self.assertTrue(lic.modules["private_lab"])

    def test_string_false_is_not_a_true_boolean(self) -> None:
        # the headline bug: bool("false") == True would have ENABLED the module
        self._bad(modules={"private_lab": "false"})

    def test_string_self_hosted_runner_rejected(self) -> None:
        self._bad(self_hosted_runner="false")

    def test_unknown_workspace_tier_rejected(self) -> None:
        self._bad(workspace_tier="platinum")

    def test_negative_ceiling_rejected(self) -> None:
        self._bad(governed_node_ceiling=-5)

    def test_boolean_ceiling_rejected(self) -> None:
        self._bad(governed_node_ceiling=True)  # bool is an int subclass — exclude it

    def test_non_iso_expiry_rejected(self) -> None:
        self._bad(expires_at="never")

    def test_short_date_rejected(self) -> None:
        self._bad(expires_at="2027-1-1")

    def test_features_must_be_list_of_strings(self) -> None:
        self._bad(features=[1, 2, 3])
        self._bad(features="private_lab")

    def test_missing_organization_rejected(self) -> None:
        fields = {k: v for k, v in GOOD.items() if k != "organization"}
        with self.assertRaises(LicenseError):
            parse_license_fields(fields)

    def test_empty_expiry_is_allowed_indefinite(self) -> None:
        lic = parse_license_fields({**GOOD, "expires_at": ""})
        self.assertFalse(lic.is_expired("2099-01-01"))


if __name__ == "__main__":
    unittest.main()
