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
# the confidentiality label whose value is redacted in the trace — the ONLY case
# in which a value may omit its decision_value (mirrors lab_runner.ledger)
LABEL_SENSITIVE = "sensitive"
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
        _check_predicate(scenario[name], name, tool_ids, inputs, errors, manifests)  # type: ignore[arg-type]

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


_NUMERIC_TYPES = frozenset({"number", "integer"})
_ARG_NAME_RE = re.compile(r"^(?:args|prov\(args)\.([A-Za-z0-9_]+)")


def _check_predicate(
    predicate: dict[str, object],
    name: str,
    tool_ids: set[str],
    inputs: dict[str, object],
    errors: list[str],
    manifests: dict[str, dict[str, object]],
) -> None:
    for sub in _walk_event_matches(predicate):
        event = sub.get("event")
        if event not in EVALUATOR_EVENTS:
            errors.append(
                f"[validating] {name}: event '{event}' is defined in the schema but not "
                f"supported by the runtime evaluator (supported: {sorted(EVALUATOR_EVENTS)})"
            )
        # `count` IS evaluated at runtime (cardinality min/max); validate its
        # shape here instead of rejecting it (the old validator forbade a feature
        # the runtime implements — capability-matrix drift, review r6)
        if "count" in sub:
            count = sub.get("count")
            lo = count.get("min") if isinstance(count, dict) else None
            hi = count.get("max") if isinstance(count, dict) else None
            if not isinstance(count, dict):
                errors.append(f"[validating] {name}: 'count' must be an object with min/max")
            elif lo is None and hi is None:
                # an empty {} is a tautology (the evaluator's no-count default is
                # "at least one match"), so it reads as a bound but constrains
                # nothing — reject it rather than silently no-op (review r7)
                errors.append(
                    f"[validating] {name}: 'count' must set min and/or max; "
                    "an empty {} constrains nothing"
                )
            else:
                if lo is not None and int(lo) < 0:  # type: ignore[arg-type]
                    errors.append(f"[validating] {name}: count.min {lo} must be >= 0")
                if hi is not None and int(hi) < 0:  # type: ignore[arg-type]
                    errors.append(f"[validating] {name}: count.max {hi} must be >= 0")
                if lo is not None and hi is not None and int(lo) > int(hi):  # type: ignore[arg-type]
                    errors.append(f"[validating] {name}: count.min {lo} > count.max {hi}")
        tool = sub.get("tool")
        if tool is not None and tool not in tool_ids:
            errors.append(f"[validating] {name}: predicate names unknown tool '{tool}'")
        arg_props = _args_properties(manifests.get(str(tool))) if tool in manifests else None
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
            # type-check the field against the tool's args_schema (review §3.3)
            _type_check_field(name, tool, field, op, arg_props, errors)


def _args_properties(manifest: dict[str, object] | None) -> dict[str, dict[str, object]] | None:
    if manifest is None:
        return None
    args_schema: dict[str, object] = manifest.get("args_schema", {})  # type: ignore[assignment]
    props = args_schema.get("properties")
    return props if isinstance(props, dict) else None


def _type_check_field(
    name: str,
    tool: object,
    field: str,
    op: str,
    arg_props: dict[str, dict[str, object]] | None,
    errors: list[str],
) -> None:
    match = _ARG_NAME_RE.match(field)
    if match is None or arg_props is None:
        return
    arg_name = match.group(1)
    if arg_name not in arg_props:
        errors.append(
            f"[validating] {name}: '{field}' references arg '{arg_name}' absent from "
            f"tool '{tool}' args_schema"
        )
        return
    arg_type = arg_props[arg_name].get("type")
    types = {arg_type} if isinstance(arg_type, str) else set(arg_type or ())
    if op in ("gt", "lt") and types and not (types & _NUMERIC_TYPES):
        errors.append(f"[validating] {name}: matcher '{op}' on non-numeric arg '{arg_name}' ({arg_type})")
    if op == "matches" and types and "string" not in types:
        errors.append(f"[validating] {name}: matcher 'matches' on non-string arg '{arg_name}' ({arg_type})")


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
    """Referential integrity + ledger unambiguity of a trace (contracts/validate.py).

    Beyond referential integrity (every id a binding/derivation names exists),
    this enforces the invariants the schema cannot (review r13):
      - value_id is UNIQUE — a duplicate makes the ledger ambiguous, and replay /
        EvidenceCase build `{value_id: value}` last-wins, so which 'v1' is real
        depends on array order and the consumer;
      - canonical_value_hash is present and, when a decision_value is present,
        equals content_hash(decision_value) — so a value cannot claim a hash of
        one payload while carrying another; only a `sensitive` value may omit the
        decision_value (its serialized form is redacted);
      - per node, seq is strictly increasing in array order and a call_id appears
        on at most one intent and one decision — so "ordered by seq" and "ordered
        by array position" agree and a call_id is not reused for two intents.
    """
    from .canonical import content_hash

    errors: list[str] = []
    values = trace.get("values", [])  # type: ignore[assignment]
    ids: set[str] = set()
    for value in values:  # type: ignore[union-attr]
        vid = str(value["value_id"])
        if vid in ids:
            errors.append(f"duplicate value_id {vid!r} — the ledger is ambiguous")
        ids.add(vid)
        if "canonical_value_hash" not in value:
            errors.append(f"value {vid}: missing canonical_value_hash")
        elif "decision_value" in value:
            expected = content_hash(value["decision_value"])
            if str(value["canonical_value_hash"]) != expected:
                errors.append(
                    f"value {vid}: canonical_value_hash does not match "
                    "content_hash(decision_value)"
                )
        elif LABEL_SENSITIVE not in (value.get("labels") or []):
            errors.append(
                f"value {vid}: decision_value omitted but the value is not labelled "
                "'sensitive' (only a redacted sensitive value may omit it)"
            )
    last_seq: dict[str, object] = {}
    intent_call_ids: set[str] = set()
    decision_call_ids: set[str] = set()
    for event in trace.get("events", []):  # type: ignore[union-attr]
        for arg, vid in (event.get("arg_bindings") or {}).items():
            if vid not in ids:
                errors.append(f"arg_bindings.{arg} -> unknown value_id {vid}")
        for vid in event.get("produces_value_ids") or []:
            if vid not in ids:
                errors.append(f"produces_value_ids -> unknown value_id {vid}")
        decision = event.get("decision")
        if decision:
            dvid = decision.get("driving_value_id")
            # a fail-closed DENY has NO provenance value → driving_value_id is null
            # and a typed driving_unresolved says why (review r14). Otherwise it
            # must reference a real ledger value.
            if dvid is None:
                # null is honest only when a typed driving_unresolved says WHY no
                # provenance value exists (a fail-closed DENY, or an observe-only
                # ALLOW over a sink with no driving args — review r14)
                if "driving_unresolved" not in decision:
                    errors.append(
                        "decision.driving_value_id is null without a driving_unresolved reason"
                    )
            elif dvid not in ids:
                errors.append(f"decision.driving_value_id -> unknown {dvid}")
        node = str(event.get("node", "root"))
        if "seq" in event:
            prev = last_seq.get(node)
            if prev is not None and event["seq"] <= prev:  # type: ignore[operator]
                errors.append(
                    f"event seq {event['seq']} in node {node!r} is not strictly increasing "
                    f"in array order (previous {prev})"
                )
            last_seq[node] = event["seq"]
        cid = event.get("call_id")
        if cid is not None:
            if event.get("type") == "tool_call_intent":
                if str(cid) in intent_call_ids:
                    errors.append(f"duplicate tool_call_intent call_id {cid!r}")
                intent_call_ids.add(str(cid))
            elif event.get("type") == "gate_decision":
                if str(cid) in decision_call_ids:
                    errors.append(f"duplicate gate_decision call_id {cid!r}")
                decision_call_ids.add(str(cid))
    for value in values:  # type: ignore[union-attr]
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
