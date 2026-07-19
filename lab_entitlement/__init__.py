"""lab_entitlement — the Private Lab commercial rung (plan block B9).

One Ed25519-signed license, both modules (Private Lab + Control Plane) as
flags. The two lines as code: safety is free forever (never consults a
license); organizational use is paid (a non-expired license flagging the
private_lab module). Expiry degrades org features to read-only; it never
disables a safety feature.
"""

from .errors import CryptoUnavailable, EntitlementError, LicenseError
from .gate import ORG_FEATURES, SAFETY_FEATURES, FeatureGate
from .license import License, parse_license_fields

__all__ = [
    "CryptoUnavailable",
    "EntitlementError",
    "FeatureGate",
    "License",
    "LicenseError",
    "ORG_FEATURES",
    "SAFETY_FEATURES",
    "parse_license_fields",
]
