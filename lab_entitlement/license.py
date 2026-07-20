"""The Axor license — one file, both modules (axor-packaging.md §4).

Mirrors the Control Plane's Ed25519-signed, offline-verifiable license
(`cp-monetization.md` §4): modules are FLAGS, not separate licenses, so the
same file carries Private Lab and Control Plane. The signed payload is a
JCS-subset canonical JSON, the same approach the plane signs commands with —
one crypto stack.

The pure decision logic here (expiry, module/feature enablement) never touches
crypto and is fully testable offline. Signature verification is optional
(PyNaCl), exactly like the CP license verifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import LicenseError

WORKSPACE_TIERS = ("community", "team", "security", "enterprise")


@dataclass(frozen=True)
class License:
    """A parsed license. `is_expired`/`enables` are pure — no crypto, no I/O."""

    organization: str
    workspace_tier: str
    modules: dict[str, bool] = field(default_factory=dict)
    governed_node_ceiling: int = 0
    self_hosted_runner: bool = False
    expires_at: str = ""  # ISO date; compared lexicographically
    features: tuple[str, ...] = ()

    def is_expired(self, today: str) -> bool:
        """Expiry is lexicographic on ISO dates (same as CP)."""
        return bool(self.expires_at) and today > self.expires_at

    def module_enabled(self, name: str, today: str) -> bool:
        """A module is active only under a non-expired license that flags it.

        Expiry degrades org modules to off (read-only EE) — but this is NEVER
        consulted for safety features (see gate.py Line 1)."""
        return bool(self.modules.get(name)) and not self.is_expired(today)

    def enables(self, feature: str, today: str) -> bool:
        return feature in self.features and not self.is_expired(today)

    def allows_nodes(self, count: int, today: str, module: str = "private_lab") -> bool:
        """Governed-node scaling is an ORG capability: it is allowed only under a
        non-expired license that flags the module AND within the ceiling.

        The ceiling alone was a bypass — an expired (or module-less) license kept
        returning True for any count under the number, so a caller using this
        method standalone would grant org scaling it no longer holds."""
        return self.module_enabled(module, today) and count <= self.governed_node_ceiling


def canonical_payload(license_fields: dict[str, object]) -> bytes:
    """The exact bytes signed/verified — a JCS-subset canonical JSON of the
    license body (sorted keys, compact, UTF-8). Kept separate so the signing
    helper and the verifier agree byte-for-byte."""
    from lab_contracts import canonical_json

    body = {
        "organization": license_fields.get("organization", ""),
        "workspace_tier": license_fields.get("workspace_tier", ""),
        "modules": license_fields.get("modules", {}),
        "governed_node_ceiling": license_fields.get("governed_node_ceiling", 0),
        "self_hosted_runner": license_fields.get("self_hosted_runner", False),
        "expires_at": license_fields.get("expires_at", ""),
        "features": license_fields.get("features", []),
    }
    return canonical_json(body).encode("utf-8")


def parse_license_fields(fields: dict[str, object]) -> License:
    """Parse AND strictly validate a license body, raising LicenseError on any
    ill-formed field. A signature proves the vendor authored the bytes; it does
    NOT prove they are well-formed, so a typo'd vendor license (a string
    "false", an unknown tier, a negative ceiling, a bogus date) must be rejected
    rather than silently coerced into enabled capabilities (review r11). Every
    boolean means a JSON boolean — never bool("false") == True."""
    if not isinstance(fields, dict):
        raise LicenseError("license body must be a JSON object")

    org = fields.get("organization")
    if not isinstance(org, str) or not org:
        raise LicenseError("organization must be a non-empty string")

    tier = fields.get("workspace_tier")
    if tier not in WORKSPACE_TIERS:
        raise LicenseError(f"workspace_tier {tier!r} is not one of {WORKSPACE_TIERS}")

    modules_raw = fields.get("modules", {})
    if not isinstance(modules_raw, dict):
        raise LicenseError("modules must be an object")
    modules: dict[str, bool] = {}
    for name, value in modules_raw.items():
        if not isinstance(value, bool):
            raise LicenseError(
                f"module {name!r} must be a JSON boolean, got {type(value).__name__}"
            )
        modules[str(name)] = value

    ceiling = fields.get("governed_node_ceiling", 0)
    # bool is a subclass of int — exclude it explicitly so `true` isn't a ceiling
    if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 0:
        raise LicenseError(f"governed_node_ceiling must be an integer >= 0, got {ceiling!r}")

    self_hosted = fields.get("self_hosted_runner", False)
    if not isinstance(self_hosted, bool):
        raise LicenseError("self_hosted_runner must be a JSON boolean")

    expires = fields.get("expires_at", "")
    if expires:
        if not isinstance(expires, str) or not _is_iso_date(expires):
            raise LicenseError(f"expires_at must be a YYYY-MM-DD date, got {expires!r}")

    features_raw = fields.get("features", [])
    if not isinstance(features_raw, list) or not all(isinstance(f, str) for f in features_raw):
        raise LicenseError("features must be a list of strings")

    return License(
        organization=org,
        workspace_tier=tier,
        modules=modules,
        governed_node_ceiling=ceiling,
        self_hosted_runner=self_hosted,
        expires_at=str(expires),
        features=tuple(features_raw),
    )


def _is_iso_date(value: str) -> bool:
    """Strict YYYY-MM-DD. This is what makes the lexicographic expiry compare
    sound — for a fixed-width ISO date, lexicographic order IS chronological, so
    a bogus 'never' can't behave like an indefinite license."""
    import datetime

    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10  # reject shortened forms fromisoformat now tolerates
