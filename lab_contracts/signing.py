"""Optional Ed25519 detached signatures over a bundle's content hashes.

The bundle schema already carries a `signature` field — "detached signature
over content_hashes". This module signs/verifies exactly that: the canonical
JSON of `content_hashes`. A signature from a KNOWN author key upgrades a
publication's integrity axis `hash_verified → signed` — without changing
`origin` (a signed local bundle is still `origin=local`, never `lab_infra`).

Same crypto as the CP license (`cp-monetization.md` §4). Optional dependency:
importing lab_contracts never requires PyNaCl; only signing/verification does.
Content-hash verification itself (the integrity spine) is pure and always on.
"""

from __future__ import annotations

from .canonical import canonical_json
from .errors import ContractsError


class SignatureUnavailable(ContractsError):
    """Ed25519 signing/verification requested but PyNaCl is not installed."""


class SignatureInvalid(ContractsError):
    """A detached signature does not verify against the given public key."""


def _signed_bytes(content_hashes: dict[str, str]) -> bytes:
    return canonical_json(content_hashes).encode("utf-8")


def sign_bundle(bundle: dict[str, object], author_privkey_hex: str) -> str:
    """Return a detached hex signature over the bundle's content_hashes."""
    try:
        from nacl.signing import SigningKey  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - only without PyNaCl
        raise SignatureUnavailable("PyNaCl not installed; `pip install axor-lab[crypto]`") from exc
    key = SigningKey(bytes.fromhex(author_privkey_hex))
    content_hashes: dict[str, str] = bundle["content_hashes"]  # type: ignore[assignment]
    return key.sign(_signed_bytes(content_hashes)).signature.hex()


def verify_bundle_signature(
    bundle: dict[str, object], signature_hex: str, author_pubkey_hex: str
) -> None:
    """Raise SignatureInvalid if the signature does not verify."""
    try:
        from nacl.exceptions import BadSignatureError  # noqa: PLC0415
        from nacl.signing import VerifyKey  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise SignatureUnavailable("PyNaCl not installed; `pip install axor-lab[crypto]`") from exc
    content_hashes: dict[str, str] = bundle["content_hashes"]  # type: ignore[assignment]
    try:
        VerifyKey(bytes.fromhex(author_pubkey_hex)).verify(
            _signed_bytes(content_hashes), bytes.fromhex(signature_hex)
        )
    except (BadSignatureError, ValueError) as exc:
        raise SignatureInvalid("bundle signature does not verify") from exc
