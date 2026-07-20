"""Canonical JSON serialization and content hashing.

Canonicalization (`axor-jcs`): lexicographically sorted object keys, minimal
separators (no whitespace), UTF-8, `ensure_ascii=False`, and NaN/Infinity
rejected. This is **RFC 8785 / JCS**, including RFC 8785 §3.2.2.3 number
serialization (the ECMAScript ``Number.prototype.toString`` algorithm).

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


def _canonicalize(obj: object, out: list[str]) -> None:
    if obj is None:
        out.append("null")
    elif obj is True:
        out.append("true")
    elif obj is False:
        out.append("false")
    elif isinstance(obj, str):
        out.append(json.dumps(obj, ensure_ascii=False))  # RFC 8785 string escaping
    elif isinstance(obj, int):  # bool already handled above
        out.append(str(obj))
    elif isinstance(obj, float):
        out.append(_rfc8785_number(obj))
    elif isinstance(obj, dict):
        out.append("{")
        first = True
        for key in sorted(obj.keys(), key=_key_str):
            if not first:
                out.append(",")
            first = False
            out.append(json.dumps(_key_str(key), ensure_ascii=False))
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


def _key_str(key: object) -> str:
    if isinstance(key, str):
        return key
    if key is True:
        return "true"
    if key is False:
        return "false"
    if key is None:
        return "null"
    if isinstance(key, float):
        return _rfc8785_number(key)
    if isinstance(key, int):
        return str(key)
    raise TypeError(f"object property name of type {type(key).__name__} is not allowed")


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
