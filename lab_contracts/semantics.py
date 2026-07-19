"""Semantic checks JSON Schema cannot express, plus the contract vocabulary.

Author-time scenario validation (acceptance test 1): every failure is a
specific, actionable message tied to the `validating` stage; a scenario
failing any check never reaches `queued` (lifecycle.md). Trace referential
integrity mirrors contracts/validate.py's trace_semantics.
"""

from __future__ import annotations

import re

from .errors import ScenarioValidationError
from .inputs import INPUTS_PREFIX
from .schemas import load_schemas
from .subset_validator import validate_against

# contract vocabulary (tool-manifest.schema effect classes, predicate matchers)
EGRESS_CLASSES = frozenset({"EXPORT", "EXEC"})
SINK_CLASSES = EGRESS_CLASSES | {"WRITE"}
KNOWN_MATCHERS = frozenset(
    {"equal", "not_equal", "in", "not_in", "matches", "provenance_is", "gt", "lt"}
)

_INDEX_RE = re.compile(r"\[\d+\]")
_SINK_CLASSES = SINK_CLASSES
_FIELD_RE = re.compile(r"^(args\.[A-Za-z0-9_]+|result\.[A-Za-z0-9_.\[\]]+|output\.[A-Za-z0-9_.\[\]]+|prov\(args\.[A-Za-z0-9_]+\))$")


def validate_scenario(scenario: dict[str, object], manifests: dict[str, dict[str, object]]) -> None:
    """Raise ScenarioValidationError with ALL failures, or return None."""
    errors: list[str] = []
    tool_ids = _declared_tool_ids(scenario, manifests, errors)
    inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]

    for name in ("violation", "task_success"):
        _check_predicate(scenario[name], name, tool_ids, inputs, errors)  # type: ignore[arg-type]

    _check_injection_vector(scenario, manifests, errors)
    _check_sink_exists(scenario, manifests, errors)
    _check_manifest_input_refs(scenario, manifests, inputs, errors)

    if errors:
        raise ScenarioValidationError(tuple(errors))


def _declared_tool_ids(
    scenario: dict[str, object], manifests: dict[str, dict[str, object]], errors: list[str]
) -> set[str]:
    ids: set[str] = set()
    for tool in scenario.get("tools", []):  # type: ignore[union-attr]
        tool_id = str(tool["$ref"]) if "$ref" in tool else str(tool.get("id"))
        ids.add(tool_id)
        if tool_id not in manifests:
            errors.append(f"[validating] tool '{tool_id}' has no manifest in the bundle")
    return ids


# The reference evaluator (lab_runner.predicates) supports a SUBSET of the
# predicate schema. Author-time validation rejects the rest so a scenario can
# never be schema-valid yet runtime-invalid (review §3.2). These sets track
# exactly what the evaluator runs; widen them only when the evaluator does.
EVALUATOR_EVENTS = frozenset({"tool_call"})
_ARG_ONLY_FIELD_RE = re.compile(r"^(args\.[A-Za-z0-9_]+|prov\(args\.[A-Za-z0-9_]+\))$")


def _check_predicate(
    predicate: dict[str, object],
    name: str,
    tool_ids: set[str],
    inputs: dict[str, object],
    errors: list[str],
) -> None:
    for sub in _walk_event_matches(predicate):
        event = sub.get("event")
        if event not in EVALUATOR_EVENTS:
            errors.append(
                f"[validating] {name}: event '{event}' is defined in the schema but not "
                f"supported by the runtime evaluator (supported: {sorted(EVALUATOR_EVENTS)})"
            )
        if "count" in sub:
            errors.append(
                f"[validating] {name}: 'count' is schema-defined but not evaluated at runtime; "
                "remove it or the test would silently not apply it"
            )
        tool = sub.get("tool")
        if tool is not None and tool not in tool_ids:
            errors.append(f"[validating] {name}: predicate names unknown tool '{tool}'")
        for field, matcher in sub.get("where", {}).items():  # type: ignore[union-attr]
            if not _ARG_ONLY_FIELD_RE.match(field):
                errors.append(
                    f"[validating] {name}: field address '{field}' is not supported by the "
                    "runtime evaluator (only args.<name> and prov(args.<name>))"
                )
            if not isinstance(matcher, dict) or len(matcher) != 1:
                errors.append(f"[validating] {name}: matcher for '{field}' must have exactly one operator")
                continue
            (op, operand), = matcher.items()
            if op not in KNOWN_MATCHERS:
                errors.append(f"[validating] {name}: unknown matcher '{op}' for '{field}'")
                continue
            for ref in _input_refs_of(operand):
                if ref not in inputs:
                    errors.append(f"[validating] {name}: $inputs.{ref} does not resolve")


def _walk_event_matches(predicate: dict[str, object]) -> list[dict[str, object]]:
    if "event" in predicate:
        return [predicate]
    for key in ("all", "any", "sequence"):
        if key in predicate:
            out: list[dict[str, object]] = []
            for sub in predicate[key]:  # type: ignore[union-attr]
                out.extend(_walk_event_matches(sub))
            return out
    if "not" in predicate:
        return _walk_event_matches(predicate["not"])  # type: ignore[arg-type]
    return []


def _input_refs_of(operand: object) -> list[str]:
    refs: list[str] = []
    if isinstance(operand, dict) and "input_ref" in operand:
        refs.append(str(operand["input_ref"]))
    elif isinstance(operand, str) and operand.startswith(INPUTS_PREFIX):
        refs.append(operand[len(INPUTS_PREFIX):])
    elif isinstance(operand, list):
        for item in operand:
            refs.extend(_input_refs_of(item))
    return refs


def _check_injection_vector(
    scenario: dict[str, object], manifests: dict[str, dict[str, object]], errors: list[str]
) -> None:
    """An injection needs a vector: some fixture must place $injection into a
    field the tool declares untrusted."""
    fixtures: dict[str, dict[str, object]] = scenario.get("fixtures", {})  # type: ignore[assignment]
    for tool_id, fixture in fixtures.items():
        placement: dict[str, object] = fixture.get("injection_placement", {})  # type: ignore[assignment]
        field = placement.get("field")
        if not field:
            continue
        manifest = manifests.get(tool_id)
        if manifest is None:
            continue
        untrusted = [_normalize_path(str(p)) for p in manifest.get("untrusted_fields", [])]  # type: ignore[union-attr]
        if _normalize_path(f"result.{field}") in untrusted:
            return
        errors.append(
            f"[validating] injection placed at '{tool_id}.result.{field}' which is not a "
            f"declared untrusted field"
        )
        return
    errors.append("[validating] no fixture places $injection into an untrusted field (injection has no vector)")


def _check_sink_exists(
    scenario: dict[str, object], manifests: dict[str, dict[str, object]], errors: list[str]
) -> None:
    for tool in scenario.get("tools", []):  # type: ignore[union-attr]
        tool_id = str(tool["$ref"]) if "$ref" in tool else str(tool.get("id"))
        manifest = manifests.get(tool_id)
        if manifest is None:
            continue
        effect: dict[str, object] = manifest["effect"]  # type: ignore[assignment]
        classes = {str(effect["default_class"])}
        classes.update(str(rule["class"]) for rule in effect.get("resolve", []))  # type: ignore[union-attr]
        if classes & _SINK_CLASSES:
            return
    errors.append("[validating] no WRITE/EXPORT/EXEC tool exists — nothing to breach")


def _check_manifest_input_refs(
    scenario: dict[str, object],
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
    errors: list[str],
) -> None:
    for tool in scenario.get("tools", []):  # type: ignore[union-attr]
        tool_id = str(tool["$ref"]) if "$ref" in tool else str(tool.get("id"))
        manifest = manifests.get(tool_id)
        if manifest is None:
            continue
        for rule in manifest["effect"].get("resolve", []):  # type: ignore[index, union-attr]
            for matcher in rule["when"].values():
                for operand in matcher.values():
                    for ref in _input_refs_of(operand):
                        if ref not in inputs:
                            errors.append(
                                f"[validating] tool '{tool_id}' effect rule: $inputs.{ref} does not resolve"
                            )


def _normalize_path(path: str) -> str:
    return _INDEX_RE.sub("[]", path)


def trace_semantics(trace: dict[str, object]) -> list[str]:
    """Referential integrity of a trace's value ledger (contracts/validate.py)."""
    errors: list[str] = []
    ids = {str(v["value_id"]) for v in trace.get("values", [])}  # type: ignore[union-attr]
    for event in trace.get("events", []):  # type: ignore[union-attr]
        for arg, vid in (event.get("arg_bindings") or {}).items():
            if vid not in ids:
                errors.append(f"arg_bindings.{arg} -> unknown value_id {vid}")
        for vid in event.get("produces_value_ids") or []:
            if vid not in ids:
                errors.append(f"produces_value_ids -> unknown value_id {vid}")
        decision = event.get("decision")
        if decision and decision.get("driving_value_id") not in ids:
            errors.append(
                f"decision.driving_value_id -> unknown {decision.get('driving_value_id')}"
            )
    for value in trace.get("values", []):  # type: ignore[union-attr]
        for derived in value.get("derived_from") or []:
            if derived not in ids:
                errors.append(f"value {value['value_id']}.derived_from -> unknown {derived}")
    return errors


def validate_artifact(obj: dict[str, object], schema_name: str) -> list[str]:
    """Schema validation plus the semantic layer for the schemas that have one."""
    errors = validate_against(obj, schema_name, load_schemas())
    if schema_name == "trace":
        errors += ["[sem] " + e for e in trace_semantics(obj)]
    return errors
