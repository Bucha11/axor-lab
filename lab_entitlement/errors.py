"""Error hierarchy for lab_entitlement."""

from __future__ import annotations


class EntitlementError(Exception):
    """Base class for every lab_entitlement error."""


class LicenseError(EntitlementError):
    """License missing, malformed, badly signed, or from an untrusted key."""


class CryptoUnavailable(EntitlementError):
    """Ed25519 verification was requested but PyNaCl is not installed."""
