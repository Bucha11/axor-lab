"""Endpoint safety: SSRF / private-network / DNS-rebinding guards
(endpoint-protocol.md §Endpoint safety, threat-model §3).

Two layers:

- `ssrf_check(url, resolved_ips)` — a pure ADDRESS VALIDATOR: reject non-HTTP(S)
  schemes and any address that is loopback/private/link-local/unspecified.
  Literal-IP hosts are checked directly; named hosts must be resolved and every
  resolved address re-checked. It does NOT connect, so on its own it cannot stop
  a rebinding attack — the IP it validated and the IP a later fetcher connects to
  are unrelated.
- `safe_open(url)` — the actual GUARD: it resolves DNS itself, validates every
  resolved address, CONNECTS to a validated IP (pinning it, so the HTTP library
  never re-resolves the name to a different address), preserves the original
  Host/SNI, and re-runs the whole check on every redirect. This is what closes
  the rebinding + redirect-to-private holes.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse

from .errors import UnsafeEndpoint

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


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


def _default_resolve(host: str, port: int) -> list[str]:
    import socket

    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    # de-dup while preserving order; sockaddr[0] is the numeric IP
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(str(info[4][0]), None)
    return list(seen)


def _default_connect(
    scheme: str, host: str, ip: str, port: int, path: str, timeout: float
) -> "object":  # pragma: no cover - needs real network
    """Open ONE request pinned to `ip`, but presenting `host` as Host + SNI so
    the certificate and virtual host still match the name — and the HTTP library
    never gets to resolve the name itself."""
    import http.client
    import socket
    import ssl

    raw = socket.create_connection((ip, port), timeout=timeout)
    if scheme == "https":
        ctx = ssl.create_default_context()
        sock: object = ctx.wrap_socket(raw, server_hostname=host)  # SNI + cert vs the NAME
    else:
        sock = raw
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = sock  # type: ignore[assignment]  # pin our socket; no re-resolution
    conn.request("GET", path or "/", headers={"Host": host})
    return conn.getresponse()


def safe_open(
    url: str,
    *,
    max_redirects: int = 3,
    timeout: float = 10.0,
    resolve: "object" = None,
    connect: "object" = None,
) -> "object":
    """Fetch `url` with the SSRF guard actually enforced end to end.

    On EVERY hop the guard (1) resolves the DNS name ITSELF, (2) rejects unless
    every resolved address is public (ssrf_check), (3) connects to a validated
    address and pins it so no independent re-resolution can rebind to a private
    host, (4) keeps the original Host/SNI, and (5) re-validates each redirect
    target. `resolve`/`connect` are injectable for testing; the defaults use the
    stdlib. Returns the final response object; raises UnsafeEndpoint on any
    non-public hop or too many redirects."""
    do_resolve = resolve or _default_resolve  # type: ignore[truthy-function]
    do_connect = connect or _default_connect  # type: ignore[truthy-function]
    current = url
    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise UnsafeEndpoint(f"scheme {parsed.scheme!r} not allowed (http/https only)")
        host = parsed.hostname
        if not host:
            raise UnsafeEndpoint("endpoint URL has no host")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        literal = _as_ip(host)
        # WE resolve (a literal IP resolves to itself); the fetcher never does
        ips = [host] if literal is not None else list(do_resolve(host, port))  # type: ignore[operator]
        if not ips:
            raise UnsafeEndpoint(f"host {host!r} did not resolve")
        ssrf_check(current, resolved_ips=ips)  # validate EVERY address (rebinding guard)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        response = do_connect(parsed.scheme, host, ips[0], port, path, timeout)  # type: ignore[operator]
        status = int(getattr(response, "status", 0))
        if status in _REDIRECT_STATUSES:
            location = response.getheader("Location") if hasattr(response, "getheader") else None
            if hasattr(response, "close"):
                response.close()
            if not location:
                raise UnsafeEndpoint(f"redirect {status} with no Location")
            current = urljoin(current, location)  # re-validated on the next iteration
            continue
        return response
    raise UnsafeEndpoint(f"too many redirects (> {max_redirects})")
