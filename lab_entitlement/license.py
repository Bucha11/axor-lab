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
        "organization": license_fields["organization"],
        "workspace_tier": license_fields["workspace_tier"],
        "modules": license_fields.get("modules", {}),
        "governed_node_ceiling": license_fields.get("governed_node_ceiling", 0),
        "self_hosted_runner": license_fields.get("self_hosted_runner", False),
        "expires_at": license_fields.get("expires_at", ""),
        "features": license_fields.get("features", []),
    }
    return canonical_json(body).encode("utf-8")


def parse_license_fields(fields: dict[str, object]) -> License:
    return License(
        organization=str(fields["organization"]),
        workspace_tier=str(fields["workspace_tier"]),
        modules={str(k): bool(v) for k, v in dict(fields.get("modules", {})).items()},  # type: ignore[arg-type]
        governed_node_ceiling=int(fields.get("governed_node_ceiling", 0)),  # type: ignore[arg-type]
        self_hosted_runner=bool(fields.get("self_hosted_runner", False)),
        expires_at=str(fields.get("expires_at", "")),
        features=tuple(str(f) for f in fields.get("features", [])),  # type: ignore[union-attr]
    )
