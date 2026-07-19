"""Resolution of `$inputs.x` / `{"input_ref": "x"}` references against
scenario inputs — the structured ground truth predicates and effect rules
compare against (never scraped from prompt text)."""

from __future__ import annotations

from .errors import UnresolvedInputError

INPUTS_PREFIX = "$inputs."


def resolve_operand(operand: object, inputs: dict[str, object]) -> object:
    """Resolve a predicate operand: literal, `{"input_ref": k}`, or `$inputs.k`."""
    if isinstance(operand, dict):
        if set(operand) == {"input_ref"}:
            return _lookup(str(operand["input_ref"]), inputs)
        raise UnresolvedInputError(f"malformed operand {operand!r}")
    if isinstance(operand, str) and operand.startswith(INPUTS_PREFIX):
        return _lookup(operand[len(INPUTS_PREFIX):], inputs)
    return operand


def expand_list(items: list[object], inputs: dict[str, object]) -> list[object]:
    """Expand an `in`/`not_in` operand list; a `$inputs.x` naming a list splices."""
    out: list[object] = []
    for item in items:
        resolved = resolve_operand(item, inputs)
        if isinstance(resolved, list) and item != resolved:
            out.extend(resolved)
        else:
            out.append(resolved)
    return out


def _lookup(key: str, inputs: dict[str, object]) -> object:
    if key not in inputs:
        raise UnresolvedInputError(f"$inputs.{key} does not resolve")
    return inputs[key]
