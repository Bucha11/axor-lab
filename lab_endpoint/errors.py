"""Error hierarchy for lab_endpoint."""

from __future__ import annotations


class EndpointError(Exception):
    """Base class for every lab_endpoint error."""


class UnsafeEndpoint(EndpointError):
    """An endpoint URL fails an SSRF / private-network / rebinding check."""


class GovernanceUnavailable(EndpointError):
    """Governance was requested on a black-box endpoint (impossible: no lineage)."""
