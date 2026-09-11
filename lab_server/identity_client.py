"""Verify axor-identity access tokens — the vendored client.

This is a copy of `axor_identity.verify` kept in the Lab so lab_server has no
package dependency on the identity service: the two share a token FORMAT, not a
library. A hosted lab_server that authenticates humans passes the identity
JWKS to `make_runtime_server`; each request's bearer token is checked here,
locally, against that public key. The open-core runner never calls this.

pyjwt + cryptography are imported lazily (the `axor-lab[identity]` extra), so
importing this module costs nothing when identity login is not configured.
"""
from __future__ import annotations

import base64
import json
import urllib.request
from dataclasses import dataclass
from typing import Any

ISSUER = "axor-identity"
ALGORITHM = "EdDSA"


class IdentityError(Exception):
    """An access token was missing, malformed, expired, or not trusted."""


@dataclass(frozen=True)
class Claims:
    user_id: str
    email: str
    org: str
    role: str
    tier: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Claims":
        try:
            return cls(user_id=payload["sub"], email=payload["eml"],
                       org=payload["org"], role=payload["role"],
                       tier=payload["tier"])
        except KeyError as exc:
            raise IdentityError(f"token missing claim {exc}") from exc


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _public_key_from_jwk(jwk: dict[str, str]) -> Any:  # Ed25519PublicKey
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise IdentityError("unsupported JWK: expected an Ed25519 OKP key")
    return Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"]))


def verify_access_token(token: str, jwks: dict[str, Any], *, issuer: str = ISSUER,
                        leeway: int = 30) -> Claims:
    """Validate `token` against `jwks` and return its Claims, or raise
    IdentityError. `jwks` is the parsed `/.well-known/jwks.json` document."""
    import jwt  # lazy: only a hosted, identity-configured server needs it

    keys = {k.get("kid"): k for k in jwks.get("keys", [])}
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise IdentityError(f"malformed token: {exc}") from exc
    jwk = keys.get(kid) or (jwks["keys"][0] if len(jwks.get("keys", [])) == 1 else None)
    if jwk is None:
        raise IdentityError(f"no verifying key for kid {kid!r}")
    try:
        payload = jwt.decode(
            token, _public_key_from_jwk(jwk), algorithms=[ALGORITHM],
            issuer=issuer, leeway=leeway,
            options={"require": ["exp", "iss", "sub"]})
    except jwt.PyJWTError as exc:
        raise IdentityError(str(exc)) from exc
    return Claims.from_payload(payload)


def fetch_jwks(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch a JWKS document over HTTP (std-lib only). A server fetches once at
    boot and caches; the kid tells it when a refetch is due."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())
