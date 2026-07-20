"""Canonical JSON serialization and content hashing.

Canonicalization (`axor-jcs`): lexicographically sorted object keys, minimal
separators (no whitespace), UTF-8, `ensure_ascii=False`, and NaN/Infinity
rejected. On the float-FREE JSON subset — objects, arrays, strings, integers,
booleans, null — this is **byte-identical to RFC 8785 / JCS**, verified against
the production `axor_core.kernel.canonicalize` (the same canonicalizer the
Control Plane signs commands with) by tests/test_canonicalization_vectors.py,
which also pins golden (bytes, sha256) vectors so any reimplementation
(TS/Rust/CP) can prove byte-equality.

Numbers: integers are emitted in the RFC 8785 form. Floats (which appear only
in aggregate statistics, never in signed/hashed governance artifacts —
conditions, traces, config hashes, licenses are float-free) use Python's
shortest round-trippable `repr`; this is stable within CPython but is the one
place a cross-language reimplementation must match Python's float formatting.
Signed and replayed artifacts are float-free, so this caveat never touches an
integrity- or verdict-bearing hash.
"""

from __future__ import annotations

import hashlib
import json

HASH_PREFIX = "sha256:"


def canonical_json(obj: object) -> str:
    """Serialize ``obj`` deterministically (axor-jcs; RFC 8785 on float-free JSON)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(obj: object) -> str:
    """``sha256:<hex>`` over the canonical serialization."""
    digest = hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def condition_config_hash(kernel: str, policy: dict[str, object] | None) -> str:
    """The reproducibility anchor: sha256 over normalized (kernel + policy)."""
    return content_hash({"kernel": kernel, "policy": policy or {}})


def world_digest(inputs: dict[str, object], fixtures: dict[str, object] | None) -> str:
    """The ONE definition of a trace's `inputs_digest` — the exact world a trace
    was produced in: the scenario's declared inputs AND its tool fixtures.

    Every producer (local runner, HTTP gateway, in-process instrumented SDK) and
    the bundle verifier compute it identically, so a conformant instrumented
    trace binds to its scenario the same way a runner trace does. A prior split
    (runner hashed inputs+fixtures, the endpoints hashed inputs only) let a
    verifier reject conformant endpoint traces for a scenario with fixtures
    (review r9)."""
    return content_hash({"inputs": inputs, "fixtures": fixtures or {}})
