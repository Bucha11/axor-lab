"""Optional Ed25519 license signing/verification (PyNaCl).

Same crypto as the Control Plane license (`cp-monetization.md` §4): Ed25519 over
a JCS-subset canonical payload. Optional dependency — importing lab_entitlement
never requires PyNaCl; only `sign_license`/`verify_license` do. The pure
entitlement logic (license.py, gate.py) needs no crypto and is tested offline.
"""

from __future__ import annotations

import json

from .errors import CryptoUnavailable, LicenseError
from .license import License, canonical_payload, parse_license_fields


def sign_license(fields: dict[str, object], vendor_privkey_hex: str) -> str:
    """Vendor-side: sign a license body, return the license-file JSON
    {license, sig}. Kept here so a test vector is reproducible."""
    key = _signing_key(vendor_privkey_hex)
    sig = key.sign(canonical_payload(fields)).signature.hex()
    return json.dumps({"license": fields, "sig": sig}, sort_keys=True)


def verify_license(license_json: str, vendor_pubkey_hex: str) -> License:
    """Parse and verify a license file against the vendor's Ed25519 public key.

    Raises LicenseError on any failure. Never disables safety — a rejected or
    absent license simply means no org features (Line 1 is untouched)."""
    verify_key_cls, bad_signature = _nacl()
    try:
        data = json.loads(license_json)
        fields = data["license"]
        sig_hex = data["sig"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LicenseError(f"malformed license: {exc}") from exc
    try:
        verify_key_cls(bytes.fromhex(vendor_pubkey_hex)).verify(
            canonical_payload(fields), bytes.fromhex(sig_hex)
        )
    except (bad_signature, ValueError) as exc:
        raise LicenseError("license signature invalid (not signed by the vendor)") from exc
    return parse_license_fields(fields)


def _signing_key(privkey_hex: str) -> object:
    try:
        from nacl.signing import SigningKey  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without PyNaCl
        raise CryptoUnavailable("PyNaCl not installed; `pip install axor-lab[byok]`") from exc
    return SigningKey(bytes.fromhex(privkey_hex))


def _nacl() -> tuple[object, type[BaseException]]:
    try:
        from nacl.exceptions import BadSignatureError  # noqa: PLC0415
        from nacl.signing import VerifyKey  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise CryptoUnavailable("PyNaCl not installed; `pip install axor-lab[byok]`") from exc
    return VerifyKey, BadSignatureError
