#!/usr/bin/env python3
"""Minimal validator for Axor Lab contracts: JSON-Schema subset (type/required/enum/oneOf/const/pattern)
plus semantic checks (referential integrity) that plain JSON Schema can't express.
No network deps. Run: python3 validate.py"""
import json, re, sys, glob, os

def load(p): return json.load(open(p))
_files = glob.glob('schemas/*.json') + glob.glob('schemas/_shared_from_axor_core/*.json')
SCHEMAS = {os.path.basename(f).replace('.schema.json',''): load(f) for f in _files}

class V:
    def __init__(s): s.errs=[]
    def err(s, path, msg): s.errs.append(f"{path}: {msg}")

def check(v, node, schema, path, root_schema):
    # resolve local $ref/$defs
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/$defs/"):
            schema = root_schema["$defs"][ref.split("/")[-1]]
        elif ".schema.json" in ref:
            name = ref.split(".schema.json")[0].split("/")[-1]
            frag = ref.split("#")[-1] if "#" in ref else ""
            rs = SCHEMAS.get(name)
            if rs is None: v.err(path, f"unknown schema ref {ref}"); return
            if frag.startswith("/$defs/"): schema = rs["$defs"][frag.split("/")[-1]]; root_schema = rs
            else: schema = rs; root_schema = rs
    if "const" in schema and node != schema["const"]:
        v.err(path, f"const mismatch: want {schema['const']!r} got {node!r}")
    if "enum" in schema and node not in schema["enum"]:
        v.err(path, f"not in enum {schema['enum']}: {node!r}")
    if "oneOf" in schema:
        n = sum(1 for sub in schema["oneOf"] if _matches(node, sub, root_schema))
        if n != 1: v.err(path, f"oneOf matched {n} branches (must be exactly 1)")
    if "anyOf" in schema:
        if not any(_matches(node, sub, root_schema) for sub in schema["anyOf"]):
            v.err(path, "anyOf matched 0 branches")
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(node, x) for x in types):
            v.err(path, f"type {t}, got {type(node).__name__}"); return
    if "pattern" in schema and isinstance(node, str):
        if not re.search(schema["pattern"], node): v.err(path, f"pattern {schema['pattern']} no match")
    if isinstance(node, dict):
        if "minProperties" in schema and len(node) < schema["minProperties"]:
            v.err(path, f"minProperties {schema['minProperties']}, got {len(node)}")
        if "maxProperties" in schema and len(node) > schema["maxProperties"]:
            v.err(path, f"maxProperties {schema['maxProperties']}, got {len(node)}")
    if isinstance(node, dict) and (schema.get("type")=="object" or "properties" in schema):
        for r in schema.get("required", []):
            if r not in node: v.err(path, f"missing required '{r}'")
        props = schema.get("properties", {})
        ap = schema.get("additionalProperties", True)
        for k, val in node.items():
            if k in props: check(v, val, props[k], f"{path}.{k}", root_schema)
            elif ap is False: v.err(path, f"additional property '{k}' not allowed")
            elif isinstance(ap, dict): check(v, val, ap, f"{path}.{k}", root_schema)
    if isinstance(node, list) and "items" in schema:
        for i, it in enumerate(node): check(v, it, schema["items"], f"{path}[{i}]", root_schema)

def _is_type(node, t):
    return {"object":dict,"array":list,"string":str,"number":(int,float),"integer":int,"boolean":bool}.get(t, object) and \
        isinstance(node, {"object":dict,"array":list,"string":str,"number":(int,float),"integer":int,"boolean":bool}[t]) if t in ("object","array","string","number","integer","boolean") else True

def _matches(node, sub, root):
    tmp = V(); check(tmp, node, sub, "", root); return not tmp.errs

def validate(obj, schema_name):
    v = V(); check(v, obj, SCHEMAS[schema_name], schema_name, SCHEMAS[schema_name]); return v.errs

# ---- semantic: referential integrity for traces ----
def trace_semantics(tr):
    e=[]; ids={x["value_id"] for x in tr.get("values",[])}
    for ev in tr.get("events",[]):
        for a,vid in (ev.get("arg_bindings") or {}).items():
            if vid not in ids: e.append(f"arg_bindings.{a} -> unknown value_id {vid}")
        for vid in ev.get("produces_value_ids",[]) or []:
            if vid not in ids: e.append(f"produces_value_ids -> unknown value_id {vid}")
        d=ev.get("decision")
        if d and d.get("driving_value_id") not in ids:
            e.append(f"decision.driving_value_id -> unknown {d.get('driving_value_id')}")
    for val in tr.get("values",[]):
        for dv in val.get("derived_from",[]) or []:
            if dv not in ids: e.append(f"value {val['value_id']}.derived_from -> unknown {dv}")
    return e

if __name__=="__main__":
    print("schemas loaded:", ", ".join(sorted(SCHEMAS)))
    print("(examples validated by validate_slice.py)")
