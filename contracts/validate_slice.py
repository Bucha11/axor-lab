#!/usr/bin/env python3
"""Extract & validate the vertical-slice examples. This IS acceptance-test #1."""
import json, sys
from validate import validate, trace_semantics

# the examples as the slice currently states them (pre-fix), to reproduce the reviewer's reds
examples = json.load(open("examples/slice-examples.json"))
fails=0
for name, (schema, obj) in examples.items():
    errs = validate(obj, schema)
    if schema=="trace": errs += ["[sem] "+x for x in trace_semantics(obj)]
    status = "PASS" if not errs else f"FAIL ({len(errs)})"
    print(f"\n{name}  [{schema}]  {status}")
    for er in errs[:12]: print("   -", er)
    fails += bool(errs)
print(f"\n{'='*40}\n{fails} example(s) failing")
sys.exit(1 if fails else 0)
