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

from .canonical import canonical_json, content_hash
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


def signed_ref(bundle: dict[str, object]) -> str:
    """The content hash of the EXACT bytes a bundle signature commits to — the
    whole bundle minus its `signature` field. A receipt names this so a verifier
    knows precisely what a signature covers, distinct from the publication's
    `bundle_ref` (content_hash of the bundle INCLUDING any signature field)."""
    return content_hash({k: v for k, v in bundle.items() if k != "signature"})


def build_receipt(
    bundle: dict[str, object],
    *,
    integrity: str,
    author: str | None = None,
    key_id: str | None = None,
    signature: str | None = None,
) -> dict[str, object]:
    """A PORTABLE verification receipt for a downloaded bundle (review r14).

    It carries everything a reader needs to verify offline WITHOUT trusting the
    server: the content-addressed `signed_ref` (always), and — when the
    publication earned integrity=signed — the author, key_id, and detached
    signature. For a hash_verified publication the receipt still pins `signed_ref`
    so the reader can confirm the bytes, with no signature to check."""
    return {
        "algorithm": "ed25519" if signature else "sha256-content-hash",
        "integrity": integrity,
        "signed_ref": signed_ref(bundle),
        "author": author,
        "key_id": key_id if key_id is not None else author,
        "signature": signature,
    }


_HASH_ALGORITHM = "sha256-content-hash"
_SIGN_ALGORITHM = "ed25519"


def verify_receipt(
    bundle: dict[str, object],
    receipt: dict[str, object],
    author_pubkey_hex: str | None = None,
    *,
    expected_author: str | None = None,
) -> None:
    """Verify a downloaded receipt against its bundle, offline, as a STRICT state
    machine (review r15). Integrity is NOT authenticity — the two are checked
    separately and neither is allowed to imply the other by omission.

    1. `signed_ref` must always match the bundle bytes (integrity).
    2. A `hash_verified` receipt must use the content-hash algorithm and carry NO
       signature/author/key_id — a hash-only claim cannot smuggle authenticity.
    3. A `signed` receipt MUST carry a non-empty ed25519 signature, author, and
       key_id, and the signature MUST verify against a supplied public key. A
       `signed` receipt with the signature stripped is rejected (not a silent
       pass); a `signed` receipt with no key to check it against is
       SignatureUnavailable (unverifiable), NOT success.

    `expected_author` binds the caller's trust anchor: when set, the receipt's
    author must equal it, so a signature that merely verifies against *some* key
    is not accepted as *the* author's."""
    expected = signed_ref(bundle)
    if str(receipt.get("signed_ref")) != expected:
        raise SignatureInvalid(
            f"receipt signed_ref {receipt.get('signed_ref')!r} does not match the bundle {expected!r}"
        )
    integrity = str(receipt.get("integrity", ""))
    algorithm = str(receipt.get("algorithm", ""))
    signature = receipt.get("signature")
    author = receipt.get("author")
    key_id = receipt.get("key_id")
    if integrity == "hash_verified":
        if algorithm != _HASH_ALGORITHM:
            raise SignatureInvalid(
                f"hash_verified receipt must use algorithm {_HASH_ALGORITHM!r}, got {algorithm!r}"
            )
        if signature or author or key_id:
            raise SignatureInvalid(
                "hash_verified receipt must not carry a signature/author/key_id "
                "(integrity is not authenticity)"
            )
        return  # signed_ref matched; nothing to authenticate
    if integrity == "signed":
        if algorithm != _SIGN_ALGORITHM:
            raise SignatureInvalid(
                f"signed receipt must use algorithm {_SIGN_ALGORITHM!r}, got {algorithm!r}"
            )
        if not signature or not author or not key_id:
            raise SignatureInvalid(
                "signed receipt must carry a non-empty signature, author, and key_id"
            )
        if expected_author is not None and str(author) != str(expected_author):
            raise SignatureInvalid(
                f"receipt author {author!r} is not the expected trust anchor {expected_author!r}"
            )
        if not author_pubkey_hex:
            raise SignatureUnavailable(
                "signed receipt but no author public key was supplied to verify it"
            )
        verify_bundle_signature(bundle, str(signature), author_pubkey_hex)
        return
    raise SignatureInvalid(f"unknown receipt integrity {integrity!r}")
