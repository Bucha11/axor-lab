"""The two lines as code (cp-monetization.md §1, lab-economics.md §3).

Line 1 — safety is free forever: anything that makes an agent safer is never
gated. These features NEVER consult a license, so they work with no license,
an expired license, or air-gapped.

Line 2 — organizational use is paid: anything whose value appears only when an
organization uses the product requires a non-expired license flagging the
Private Lab module. Expiry degrades these to read-only; it never touches Line 1.

The trigger for paid is organizational use, never a safety feature and never
hobby-scale privacy.
"""

from __future__ import annotations

from .license import License

# Line 1 — free forever (Public Lab + local individual use + ALL safety).
SAFETY_FEATURES = frozenset({
    "gates",                 # governance enforcement
    "replay",                # exact verdict replay
    "evidencecase_capture",  # building EvidenceCases
    "local_regressions",     # local regression pinning
    "local_byok_run",        # local runs with the researcher's own key
    "public_publish",        # publishing PUBLIC research
    "reproduce_public",      # reproducing someone's public run
    "local_private_project", # a small local private drawer
})

# Line 2 — paid (organizational use). Each requires the private_lab module.
ORG_FEATURES = frozenset({
    "private_workspace",     # hosted private org workspace
    "private_incident",      # production-incident workflow
    "scheduled_ci",          # scheduled regression CI + history
    "approvals",             # approval workflow
    "retention",             # long retention / legal hold
    "sso",                   # SSO/RBAC
    "compliance_export",     # compliance/audit exports
    "shared_scenarios",      # team-shared scenarios
})

_PRIVATE_LAB = "private_lab"


class FeatureGate:
    """Decides whether a feature is allowed under a (possibly absent) license."""

    def __init__(self, license: License | None) -> None:
        self._license = license

    def is_allowed(self, feature: str, today: str) -> bool:
        # Line 1: safety features are ALWAYS allowed — never consult the license.
        if feature in SAFETY_FEATURES:
            return True
        if feature in ORG_FEATURES:
            # Line 2: needs a non-expired license with the private_lab module,
            # and the feature must be listed (tier-scoped) OR the module grants
            # the whole workspace bundle.
            if self._license is None:
                return False
            if not self._license.module_enabled(_PRIVATE_LAB, today):
                return False
            # a feature is granted if explicitly listed, else by workspace tier
            return (
                feature in self._license.features
                or self._workspace_grants(feature)
            )
        # unknown features default to paid-denied (conservative), never to free
        return False

    def _workspace_grants(self, feature: str) -> bool:
        """Tier bundles: team unlocks the core workspace; security adds the
        heavier org features (axor-packaging.md §1)."""
        if self._license is None:
            return False
        tier = self._license.workspace_tier
        team_bundle = {"private_workspace", "private_incident", "shared_scenarios"}
        security_bundle = team_bundle | {
            "scheduled_ci", "approvals", "retention", "compliance_export"
        }
        if tier == "team":
            return feature in team_bundle
        if tier in ("security", "enterprise"):
            return feature in security_bundle or feature == "sso"
        return False
