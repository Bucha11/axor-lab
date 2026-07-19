"""Endpoint safety: SSRF / private-network / DNS-rebinding guards
(endpoint-protocol.md §Endpoint safety, threat-model §3).

A pure check over a URL: reject non-HTTP(S) schemes, loopback, private,
link-local, and unspecified addresses. Literal-IP hosts are checked directly;
named hosts must be resolved by the caller and each resolved address
re-checked (a DNS-rebinding guard means never trusting a name whose A record
can flip — resolve-and-pin at connect time).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from .errors import UnsafeEndpoint

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def ssrf_check(url: str, resolved_ips: list[str] | None = None) -> None:
    """Raise UnsafeEndpoint if the URL targets a non-public destination.

    `resolved_ips` (from the caller's DNS resolution) are each re-checked — the
    rebinding guard: a name that resolves to a private address is rejected even
    if the name itself looks public.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeEndpoint(f"scheme {parsed.scheme!r} not allowed (http/https only)")
    host = parsed.hostname
    if not host:
        raise UnsafeEndpoint("endpoint URL has no host")

    candidates: list[str] = list(resolved_ips or [])
    literal = _as_ip(host)
    if literal is not None:
        candidates.append(host)
    for candidate in candidates:
        address = _as_ip(candidate)
        if address is None:
            raise UnsafeEndpoint(f"cannot classify address {candidate!r}")
        if not address.is_global or address.is_loopback or address.is_private:
            raise UnsafeEndpoint(
                f"endpoint resolves to a non-public address {candidate!r} "
                "(SSRF / private-network / rebinding guard)"
            )
    if not candidates:
        # a named host with no resolution provided: the caller MUST resolve and
        # re-check; we refuse to pass an unresolved name to a fetcher
        raise UnsafeEndpoint(
            f"host {host!r} not resolved; resolve and pass resolved_ips for the rebinding guard"
        )


def _as_ip(value: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None
