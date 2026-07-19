"""Optional Ed25519 detached signatures over a WHOLE bundle.

A signature covers the entire canonical bundle with its own `signature` field
removed — so it protects every field (environment, trials, timestamps,
packaging, and the content_hashes map itself), not just the artifact hashes
(review P0.3). Because content_hashes now spans every field too, the signature
and the hash spine reinforce each other: editing any field after signing
breaks both. A signature from a KNOWN author key upgrades a publication's
integrity axis `hash_verified → signed` — without changing `origin` (a signed
local bundle is still `origin=local`, never `lab_infra`).

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


def _signed_bytes(bundle: dict[str, object]) -> bytes:
    """The canonical bytes signed: the whole bundle minus its `signature`."""
    body = {k: v for k, v in bundle.items() if k != "signature"}
    return canonical_json(body).encode("utf-8")


def sign_bundle(bundle: dict[str, object], author_privkey_hex: str) -> str:
    """Return a detached hex signature over the whole canonical bundle."""
    try:
        from nacl.signing import SigningKey  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - only without PyNaCl
        raise SignatureUnavailable("PyNaCl not installed; `pip install axor-lab[crypto]`") from exc
    key = SigningKey(bytes.fromhex(author_privkey_hex))
    return key.sign(_signed_bytes(bundle)).signature.hex()


def verify_bundle_signature(
    bundle: dict[str, object], signature_hex: str, author_pubkey_hex: str
) -> None:
    """Raise SignatureInvalid if the signature does not verify the whole bundle."""
    try:
        from nacl.exceptions import BadSignatureError  # noqa: PLC0415
        from nacl.signing import VerifyKey  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise SignatureUnavailable("PyNaCl not installed; `pip install axor-lab[crypto]`") from exc
    try:
        VerifyKey(bytes.fromhex(author_pubkey_hex)).verify(
            _signed_bytes(bundle), bytes.fromhex(signature_hex)
        )
    except (BadSignatureError, ValueError) as exc:
        raise SignatureInvalid("bundle signature does not verify") from exc
