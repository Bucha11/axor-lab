"""Effect-class resolution: operation + args → READ/WRITE/EXPORT/EXEC.

The effect class is resolved per call from the manifest's ordered `resolve`
rules (first match wins), falling back to `default_class` — one tool can be
WRITE to a known IBAN and EXPORT to an unknown one (tool-manifest.schema).
"""

from __future__ import annotations

from .inputs import expand_list, resolve_operand

EGRESS_CLASSES = frozenset({"EXPORT", "EXEC"})


def resolve_effect_class(
    manifest: dict[str, object],
    args: dict[str, object],
    inputs: dict[str, object],
) -> str:
    effect: dict[str, object] = manifest["effect"]  # type: ignore[assignment]
    for rule in effect.get("resolve", []):  # type: ignore[union-attr]
        if _matches(rule["when"], args, inputs):
            return str(rule["class"])
    return str(effect["default_class"])


def _matches(when: dict[str, dict[str, object]], args: dict[str, object], inputs: dict[str, object]) -> bool:
    for arg_name, matcher in when.items():
        if arg_name not in args:
            return False
        actual = args[arg_name]
        (op, operand), = matcher.items()
        if op == "equal":
            if actual != resolve_operand(operand, inputs):
                return False
        elif op == "not_equal":
            if actual == resolve_operand(operand, inputs):
                return False
        elif op == "in":
            if actual not in expand_list(list(operand), inputs):  # type: ignore[arg-type]
                return False
        elif op == "not_in":
            if actual in expand_list(list(operand), inputs):  # type: ignore[arg-type]
                return False
        else:
            return False
    return True
