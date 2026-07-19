"""Canonical JSON serialization and content hashing.

Reference approximation of JCS/RFC 8785: sorted keys, minimal separators,
UTF-8, no NaN. Every content hash in a bundle is computed over this
serialization so hashes are stable across serializers (bundle.schema
`canonicalization`).
"""

from __future__ import annotations

import hashlib
import json

HASH_PREFIX = "sha256:"


def canonical_json(obj: object) -> str:
    """Serialize ``obj`` deterministically."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(obj: object) -> str:
    """``sha256:<hex>`` over the canonical serialization."""
    digest = hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def condition_config_hash(kernel: str, policy: dict[str, object] | None) -> str:
    """The reproducibility anchor: sha256 over normalized (kernel + policy)."""
    return content_hash({"kernel": kernel, "policy": policy or {}})
