"""JSON-Schema subset validator for the contract schemas.

A cwd-independent, typed port of `contracts/validate.py` — the contracts'
own no-dependency validator. Supports the subset the contract schemas use:
type / required / enum / const / pattern / oneOf / anyOf / properties /
additionalProperties / items / min-maxProperties, with local `#/$defs/...`
and cross-schema `<name>.schema.json[#/$defs/...]` references.

Deliberately not swapped for the `jsonschema` package: this validator is
part of the contract (same semantics as contracts/validate.py, which CI
keeps green against the slice examples), and it keeps the toolchain
dependency-free.
"""

from __future__ import annotations

import re

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


def validate_against(
    obj: object,
    schema_name: str,
    schemas: dict[str, dict[str, object]],
) -> list[str]:
    """Validate ``obj`` against the named contract schema; return error list."""
    errors: list[str] = []
    schema = schemas[schema_name]
    _check(obj, schema, schema_name, schema, schemas, errors)
    return errors


def _check(
    node: object,
    schema: dict[str, object],
    path: str,
    root: dict[str, object],
    schemas: dict[str, dict[str, object]],
    errors: list[str],
) -> None:
    resolved = _resolve_ref(schema, root, schemas, path, errors)
    if resolved is None:
        return
    schema, root = resolved

    if "const" in schema and node != schema["const"]:
        errors.append(f"{path}: const mismatch: want {schema['const']!r} got {node!r}")
    if "enum" in schema and node not in schema["enum"]:  # type: ignore[operator]
        errors.append(f"{path}: not in enum {schema['enum']}: {node!r}")
    if "oneOf" in schema:
        matched = sum(
            1 for sub in schema["oneOf"] if _matches(node, sub, root, schemas)  # type: ignore[union-attr]
        )
        if matched != 1:
            errors.append(f"{path}: oneOf matched {matched} branches (must be exactly 1)")
    if "anyOf" in schema:
        if not any(_matches(node, sub, root, schemas) for sub in schema["anyOf"]):  # type: ignore[union-attr]
            errors.append(f"{path}: anyOf matched 0 branches")

    declared = schema.get("type")
    if declared:
        types = declared if isinstance(declared, list) else [declared]
        if not any(_is_type(node, str(t)) for t in types):
            errors.append(f"{path}: type {declared}, got {type(node).__name__}")
            return
    if "pattern" in schema and isinstance(node, str):
        if not re.search(str(schema["pattern"]), node):
            errors.append(f"{path}: pattern {schema['pattern']} no match")
    if "minLength" in schema and isinstance(node, str) and len(node) < int(schema["minLength"]):  # type: ignore[arg-type]
        errors.append(f"{path}: minLength {schema['minLength']}, got {len(node)}")

    # numeric bounds (review §3.1: the schemas use `minimum` etc.)
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in schema and node < schema["minimum"]:  # type: ignore[operator]
            errors.append(f"{path}: minimum {schema['minimum']}, got {node}")
        if "maximum" in schema and node > schema["maximum"]:  # type: ignore[operator]
            errors.append(f"{path}: maximum {schema['maximum']}, got {node}")
        if "exclusiveMinimum" in schema and node <= schema["exclusiveMinimum"]:  # type: ignore[operator]
            errors.append(f"{path}: exclusiveMinimum {schema['exclusiveMinimum']}, got {node}")

    if isinstance(node, dict):
        if "minProperties" in schema and len(node) < int(schema["minProperties"]):  # type: ignore[arg-type]
            errors.append(f"{path}: minProperties {schema['minProperties']}, got {len(node)}")
        if "maxProperties" in schema and len(node) > int(schema["maxProperties"]):  # type: ignore[arg-type]
            errors.append(f"{path}: maxProperties {schema['maxProperties']}, got {len(node)}")
        if schema.get("type") == "object" or "properties" in schema:
            for required in schema.get("required", []):  # type: ignore[union-attr]
                if required not in node:
                    errors.append(f"{path}: missing required '{required}'")
            props: dict[str, dict[str, object]] = schema.get("properties", {})  # type: ignore[assignment]
            additional = schema.get("additionalProperties", True)
            for key, value in node.items():
                if key in props:
                    _check(value, props[key], f"{path}.{key}", root, schemas, errors)
                elif additional is False:
                    errors.append(f"{path}: additional property '{key}' not allowed")
                elif isinstance(additional, dict):
                    _check(value, additional, f"{path}.{key}", root, schemas, errors)

    if isinstance(node, list):
        if "minItems" in schema and len(node) < int(schema["minItems"]):  # type: ignore[arg-type]
            errors.append(f"{path}: minItems {schema['minItems']}, got {len(node)}")
        if "maxItems" in schema and len(node) > int(schema["maxItems"]):  # type: ignore[arg-type]
            errors.append(f"{path}: maxItems {schema['maxItems']}, got {len(node)}")
        if "items" in schema:
            for i, item in enumerate(node):
                _check(item, schema["items"], f"{path}[{i}]", root, schemas, errors)  # type: ignore[arg-type]


def _resolve_ref(
    schema: dict[str, object],
    root: dict[str, object],
    schemas: dict[str, dict[str, object]],
    path: str,
    errors: list[str],
) -> tuple[dict[str, object], dict[str, object]] | None:
    if "$ref" not in schema:
        return schema, root
    ref = str(schema["$ref"])
    if ref == "#":
        return root, root
    if ref.startswith("#/$defs/"):
        defs: dict[str, dict[str, object]] = root["$defs"]  # type: ignore[assignment]
        return defs[ref.rsplit("/", 1)[-1]], root
    if ".schema.json" in ref:
        name = ref.split(".schema.json")[0].rsplit("/", 1)[-1]
        target = schemas.get(name)
        if target is None:
            errors.append(f"{path}: unknown schema ref {ref}")
            return None
        fragment = ref.split("#", 1)[1] if "#" in ref else ""
        if fragment.startswith("/$defs/"):
            defs = target["$defs"]  # type: ignore[assignment]
            return defs[fragment.rsplit("/", 1)[-1]], target
        return target, target
    errors.append(f"{path}: unsupported $ref {ref}")
    return None


def _matches(
    node: object,
    schema: dict[str, object],
    root: dict[str, object],
    schemas: dict[str, dict[str, object]],
) -> bool:
    probe: list[str] = []
    _check(node, schema, "", root, schemas, probe)
    return not probe


def _is_type(node: object, type_name: str) -> bool:
    expected = _TYPE_MAP.get(type_name)
    if expected is None:
        return True
    if type_name in ("number", "integer") and isinstance(node, bool):
        return False
    return isinstance(node, expected)
