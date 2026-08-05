"""Minting untrusted values from a tool result, per its manifest.

Provenance, not governance: a manifest declaring `untrusted_fields` says where
attacker-influenceable content enters, and the value ledger records it. That
happens on EVERY run — a single-arm suite with no kernel arm still needs its
trace to say which values came from outside — so it belongs in the spine.

It lived in the paired `.axl` runner, which moved into the governance
capability, and `lab_runner/loop.py` reached back into that module for it. The
general loop importing the capability is exactly the direction
`tests/test_capability_boundary.py` forbids; the import survived only because it
was buried in a function body, which is the reason that test reads the AST.
"""

from __future__ import annotations

import re

from .ledger import ValueLedger


def mint_untrusted_fields(
    ledger: ValueLedger, manifest: dict[str, object], tool_id: str, result: object
) -> list[str]:
    """Mint an external_read value for every untrusted-field instance present.

    A field also declared in the manifest's `sensitive_fields` is redacted in
    the trace (review §7.4)."""
    sensitive_patterns = {
        _norm_field(str(p)) for p in manifest.get("sensitive_fields", [])  # type: ignore[union-attr]
    }
    produced: list[str] = []
    for pattern in manifest.get("untrusted_fields", []):  # type: ignore[union-attr]
        path = str(pattern)
        path = path[len("result."):] if path.startswith("result.") else path
        is_sensitive = _norm_field(str(pattern)) in sensitive_patterns
        for concrete, value in _expand_field(result, path):
            produced.append(
                ledger.mint_external_read(
                    value, f"tool_result:{tool_id}:{concrete}",
                    sensitive=is_sensitive, produced_by=tool_id,
                )
            )
    return produced


def _norm_field(pattern: str) -> str:
    p = pattern[len("result."):] if pattern.startswith("result.") else pattern
    return re.sub(r"\[\d*\]", "[]", p)


def _expand_field(node: object, path: str) -> list[tuple[str, object]]:
    """Expand a field pattern like `transactions[].description` into concrete
    (path, typed value) instances present in the result. The value is kept
    typed (not stringified) so the ledger stores the exact decision_value."""
    if not path:
        return [("", node)] if isinstance(node, (str, int, float, bool)) else []
    head, _, rest = path.partition(".")
    if head.endswith("[]"):
        key = head[:-2]
        items = node.get(key, []) if isinstance(node, dict) else []
        out: list[tuple[str, object]] = []
        for i, item in enumerate(items):
            for sub, value in _expand_field(item, rest):
                suffix = f".{sub}" if sub else ""
                out.append((f"{key}[{i}]{suffix}", value))
        return out
    if isinstance(node, dict) and head in node:
        return [
            (f"{head}.{sub}" if sub else head, value)
            for sub, value in _expand_field(node[head], rest)
        ]
    return []
