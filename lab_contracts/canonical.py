"""Canonical JSON serialization and content hashing.

Canonicalization (`axor-jcs`): object keys sorted by UTF-16 code units (the
ECMAScript string order, NOT Python's code-point order), minimal separators (no
whitespace), UTF-8, `ensure_ascii=False`, and NaN/Infinity rejected. This is
**RFC 8785 / JCS**, including RFC 8785 §3.2.2.3 number serialization (the
ECMAScript ``Number.prototype.toString`` algorithm).

Interoperability guards (review r14): object property names must be strings (a
coerced ``1``/``"1"`` would collide into one property name); an integer outside
±(2^53−1) is rejected (it is not exactly representable as a JS Number, so a JS
verifier would read a different value); and a lone surrogate is rejected (it has
no valid UTF-8/UTF-16 encoding). These keep a Python-produced hash byte-identical
to a JS/Rust RFC 8785 implementation on the values Lab actually serializes.

Two number regimes, and the distinction is load-bearing (review r13):

  - SIGNED GOVERNANCE ARTIFACTS — conditions, traces, config_hash, licenses —
    are float-FREE, and on that subset this canonicalizer is byte-identical to
    the production ``axor_core.kernel.canonicalize`` (which the Control Plane
    signs with, and which REJECTS floats outright). Verified by
    ``test_canonicalization_vectors.py`` against axor-core.

  - BUNDLE AGGREGATE STATISTICS carry floats (estimate, interval, p-values).
    axor-core would reject these, so a bundle is NOT canonicalized by the CP
    signer — it is signed by Lab's OWN Ed25519 over this RFC 8785 output. The
    prior code emitted floats with Python's ``repr`` (``0.0`` → ``"0.0"``,
    ``1e-7`` → ``"1e-07"``, ``1e16`` → ``"1e+16"``), which is NOT RFC 8785, so a
    TS/Rust verifier would compute a different hash and the bundle signature
    would fail cross-language. ``_rfc8785_number`` now emits the ECMAScript form
    (``"0"``, ``"1e-7"``, ``"10000000000000000"``), so any RFC 8785 consumer that
    permits numbers verifies the bundle hash byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal

HASH_PREFIX = "sha256:"


def _rfc8785_number(value: float) -> str:
    """Serialize a float per RFC 8785 §3.2.2.3 (ECMAScript Number::toString).

    Python's ``repr`` yields the shortest decimal digits that round-trip — the
    SAME digit selection ECMAScript uses — and this applies the ES formatting
    rules to those digits, so the output is byte-identical to a JCS number in
    any conformant implementation."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError("NaN/Infinity are not valid canonical JSON numbers")
    if value == 0.0:  # also collapses -0.0 → "0"
        return "0"
    if value < 0:
        return "-" + _rfc8785_number(-value)
    # exact decimal of the shortest round-trip repr → (significant digits, exp)
    _sign, digits, exp = Decimal(repr(value)).as_tuple()
    digs = list(digits)
    while len(digs) > 1 and digs[-1] == 0:  # minimal significand (no trailing zeros)
        digs.pop()
        exp += 1
    s = "".join(str(d) for d in digs)
    k = len(s)          # number of significant digits
    n = k + exp         # value == s x 10^(n-k); n-1 is the exponent of the lead digit
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + s
    e = n - 1
    mantissa = s if k == 1 else s[0] + "." + s[1:]
    return f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"


# RFC 8785 numbers are IEEE-754 doubles; an integer beyond 2^53-1 is not exactly
# representable as a JS Number, so canonicalizing it in one language and reading
# it in another would disagree. Reject it rather than silently emit a value a JS
# verifier can't reproduce (review r14).
_MAX_SAFE_INT = 2 ** 53 - 1


def _reject_lone_surrogate(s: str) -> None:
    """A lone surrogate (U+D800–U+DFFF not in a pair) has no valid UTF-8/UTF-16
    encoding, so it is not interoperable JSON — reject it (review r14)."""
    for ch in s:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise ValueError(f"string contains a lone surrogate U+{ord(ch):04X}; not valid JSON")


def _canonicalize(obj: object, out: list[str]) -> None:
    if obj is None:
        out.append("null")
    elif obj is True:
        out.append("true")
    elif obj is False:
        out.append("false")
    elif isinstance(obj, str):
        _reject_lone_surrogate(obj)
        out.append(json.dumps(obj, ensure_ascii=False))  # RFC 8785 string escaping
    elif isinstance(obj, int):  # bool already handled above
        if abs(obj) > _MAX_SAFE_INT:
            raise ValueError(
                f"integer {obj} exceeds the interoperable JSON range (±2^53-1); "
                "represent it as a string to keep the hash cross-language"
            )
        out.append(str(obj))
    elif isinstance(obj, float):
        out.append(_rfc8785_number(obj))
    elif isinstance(obj, dict):
        out.append("{")
        first = True
        # object property names MUST be strings, and RFC 8785 sorts them by UTF-16
        # code units (the ECMAScript string order) — NOT Python's code-point order,
        # which disagrees for supplementary-plane keys. `.encode("utf-16-be")`
        # yields exactly the UTF-16 code-unit byte sequence to compare (review r14).
        for key in sorted(obj.keys(), key=_utf16_sort_key):
            if not first:
                out.append(",")
            first = False
            _reject_lone_surrogate(key)
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _canonicalize(obj[key], out)
        out.append("}")
    elif isinstance(obj, (list, tuple)):
        out.append("[")
        for i, item in enumerate(obj):
            if i:
                out.append(",")
            _canonicalize(item, out)
        out.append("]")
    else:
        raise TypeError(f"object of type {type(obj).__name__} is not canonical-JSON serializable")


def _utf16_sort_key(key: object) -> bytes:
    # non-string property names are NOT allowed: coercing 1 and "1" to the same
    # "1" would produce two identical property names in one object — a
    # non-injective, ambiguous serialization (review r14)
    if not isinstance(key, str):
        raise TypeError(
            f"object property name of type {type(key).__name__} is not allowed; "
            "canonical JSON object keys must be strings"
        )
    _reject_lone_surrogate(key)
    return key.encode("utf-16-be")


def canonical_json(obj: object) -> str:
    """Serialize ``obj`` deterministically (axor-jcs / RFC 8785, floats included)."""
    out: list[str] = []
    _canonicalize(obj, out)
    return "".join(out)


def content_hash(obj: object) -> str:
    """``sha256:<hex>`` over the canonical serialization."""
    digest = hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def condition_config_hash(kernel: str, policy: dict[str, object] | None) -> str:
    """The reproducibility anchor: sha256 over normalized (kernel + policy)."""
    return content_hash({"kernel": kernel, "policy": policy or {}})


def executable_config_hash(
    kernel: str,
    policy: dict[str, object] | None,
    tool_manifests: list[dict[str, object]],
) -> str:
    """The hash of the FULL config the governor actually executes (review r15).

    condition_config_hash covers only kernel + policy, but the real governor's
    verdicts ALSO turn on the tool manifests — each sink's effect class, driving
    args, and effect-resolution rules. Two experiments with the same kernel and
    policy but different manifests (a different egress sink, different
    driving_args, an untrusted-source rule) govern differently, so the plain
    config_hash is not a sufficient carry-over identity for a production handoff.
    This hash binds the manifests too, so a Control Plane deploy keyed on it
    reproduces the exact executable config Lab measured."""
    tools = sorted(
        (
            {
                "id": str(m.get("id")),
                "effect": m.get("effect", {}),
                "args_schema": m.get("args_schema", {}),
            }
            for m in tool_manifests
        ),
        key=lambda t: t["id"],
    )
    return content_hash({"kernel": kernel, "policy": policy or {}, "tools": tools})


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
