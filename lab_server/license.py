"""Offline Ed25519 entitlement for the Lab workspace (axor-packaging.md §4).

The SAME signed license the Control Plane verifies (`axor_backend.ee.license`),
read here so a hosted Private Lab knows its workspace tier and modules. One
license carries the whole ladder: `workspace_tier` (community | team | security)
gates the paid Lab workspace features, and `modules.private_lab` records that the
paid Lab module is active. Community (free, local/public) needs no license — the
absence of one simply means the community tier.

Byte-identity with the Control Plane is by CONSTRUCTION, not coincidence: the
signed payload is canonicalized by the very same `axor_core.kernel.canonicalize`
the Control Plane signs with, over the same field set. A license issued by
`axor-license` therefore verifies here unchanged. Verification needs the crypto
and kernel extras (`pip install axor-lab[crypto] axor-core`); without them a Lab
runs as community, never with a silently-unchecked license.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# The modules the ladder recognizes (axor-packaging.md §0), same order as the
# Control Plane's license module — the signed `modules` object uses these keys.
KNOWN_MODULES = ("private_lab", "control_plane")
_TIER_ORDER = {"community": 0, "team": 1, "security": 2}


class LicenseError(Exception):
    """License missing, malformed, badly signed, or from an untrusted vendor key."""


class LicenseRequired(Exception):
    """A paid workspace feature was reached without a license that entitles it —
    the seam a hosted Lab endpoint turns into a 402 (never a safety feature)."""


def _enabled_modules(raw: object) -> tuple[str, ...]:
    """Enabled module names from a ``{name: bool}`` object or a list of names,
    normalized to the canonical order, unknown names dropped."""
    if isinstance(raw, dict):
        return tuple(m for m in KNOWN_MODULES if bool(raw.get(m)))
    if isinstance(raw, (list, tuple)):
        named = {str(x) for x in raw}
        return tuple(m for m in KNOWN_MODULES if m in named)
    return ()


def _modules_payload(raw: object) -> dict[str, bool]:
    """The canonical fixed-key ``{module: bool}`` object that gets signed — a
    stable key set so the signature never depends on which modules the issuer
    happened to spell out. Identical to the Control Plane's construction."""
    enabled = _enabled_modules(raw)
    return {m: (m in enabled) for m in KNOWN_MODULES}


@dataclass(frozen=True)
class License:
    organization: str
    workspace_tier: str  # "community" | "team" | "security"
    modules: tuple[str, ...]  # enabled module names (subset of KNOWN_MODULES)
    governed_node_ceiling: int
    expires_at: str  # ISO date; compared lexicographically against `today`
    self_hosted_runner: bool = False
    features: tuple[str, ...] = ()  # granular EE flags (e.g. sso, compliance_exports)

    def is_expired(self, today: str) -> bool:
        return today > self.expires_at

    def has_module(self, module: str) -> bool:
        """Whether this license enables a product module (private_lab /
        control_plane). A module a license does not carry stays locked."""
        return module in self.modules

    def tier_at_least(self, tier: str) -> bool:
        """Whether the workspace tier is at least `tier` (community < team <
        security) — the workspace-feature gate for the paid Lab."""
        return _TIER_ORDER.get(self.workspace_tier, -1) >= _TIER_ORDER.get(tier, 99)

    def allows_nodes(self, count: int) -> bool:
        return count <= self.governed_node_ceiling

    def enables(self, feature: str, today: str) -> bool:
        """A granular feature is enabled only under a non-expired license that
        lists it. After expiry EE degrades to read-only (safety never checks a
        license)."""
        return feature in self.features and not self.is_expired(today)


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    """RFC 8785 canonical bytes via the SAME canonicalizer the Control Plane
    signs with — so the two products agree byte for byte and one issued license
    verifies in both. Requires axor-core; its absence is a clear error, never a
    silent fallback to a divergent canonicalizer."""
    try:
        from axor_core.kernel import canonicalize  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment without axor-core
        raise LicenseError(
            "license verification needs the axor-core kernel canonicalizer "
            "(pip install axor-core) so it stays byte-identical to the Control Plane"
        ) from exc
    return canonicalize(payload)


def _license_payload(lic: dict) -> bytes:
    # the EXACT field set the Control Plane signs (axor_backend.ee.license) — keep
    # the two in lockstep; a mismatch here silently rejects valid cross-issued
    # licenses. (Eventually both should import one shared signer.)
    return _canonical_bytes({
        "organization": lic["organization"],
        "workspace_tier": lic["workspace_tier"],
        "modules": _modules_payload(lic.get("modules", {})),
        "governed_node_ceiling": lic["governed_node_ceiling"],
        "self_hosted_runner": bool(lic.get("self_hosted_runner", False)),
        "expires_at": lic["expires_at"],
        "features": lic.get("features", []),
    })


def verify_license(license_json: str, vendor_pubkey_hex: str) -> License:
    """Parse and verify a license file against the vendor's Ed25519 public key.

    Raises LicenseError on any failure. Uses the same PyNaCl Ed25519 the Control
    Plane verifies with; the vendor public key is operator-pinned
    (AXOR_VENDOR_PUBKEY). A license the vendor did not sign is rejected."""
    try:
        from nacl.exceptions import BadSignatureError  # noqa: PLC0415
        from nacl.signing import VerifyKey  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment without pynacl
        raise LicenseError(
            "license verification needs the crypto extra (pip install axor-lab[crypto])"
        ) from exc

    try:
        data = json.loads(license_json)
        sig_hex = data["sig"]
        lic = data["license"]
        message = _license_payload(lic)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LicenseError(f"malformed license: {exc}") from exc

    try:
        VerifyKey(bytes.fromhex(vendor_pubkey_hex)).verify(message, bytes.fromhex(sig_hex))
    except (BadSignatureError, ValueError) as exc:
        raise LicenseError("license signature invalid (not signed by the vendor)") from exc

    try:
        return License(
            organization=str(lic["organization"]),
            workspace_tier=str(lic["workspace_tier"]),
            modules=_enabled_modules(lic.get("modules", {})),
            governed_node_ceiling=int(lic["governed_node_ceiling"]),
            expires_at=str(lic["expires_at"]),
            self_hosted_runner=bool(lic.get("self_hosted_runner", False)),
            features=tuple(lic.get("features", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseError(f"malformed license fields: {exc}") from exc


def sign_license(lic: dict, vendor_privkey_hex: str) -> str:
    """Vendor-side helper (kept here so the cross-product test vector is
    reproducible): sign a license dict, return the license-file JSON."""
    from nacl.signing import SigningKey  # noqa: PLC0415

    key = SigningKey(bytes.fromhex(vendor_privkey_hex))
    sig = key.sign(_license_payload(lic)).signature.hex()
    return json.dumps({"license": lic, "sig": sig}, sort_keys=True)


def require_workspace_tier(license: License | None, tier: str) -> None:
    """Gate seam for a paid workspace feature: raise LicenseRequired unless an
    active license is at least `tier`. `None` means community. A hosted endpoint
    turns the exception into a 402 that names what to buy; a safety feature never
    calls this."""
    if license is None or not license.tier_at_least(tier):
        raise LicenseRequired(
            f"this workspace feature needs the {tier} tier or higher "
            "(axor-packaging.md) — activate a license"
        )


def require_module(license: License | None, module: str) -> None:
    """Gate seam for a module (private_lab / control_plane): raise LicenseRequired
    unless an active license enables it."""
    if license is None or not license.has_module(module):
        raise LicenseRequired(f"this feature needs the {module} module — activate a license")
