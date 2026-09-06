#!/usr/bin/env python3
"""Extract & validate the vertical-slice examples. This IS acceptance-test #1.

The validation itself is `lab_contracts.validate_artifact` — the schema subset
plus the semantic layer (trace referential integrity, ledger unambiguity) that
plain JSON Schema cannot express.

This used to run against `validate.py` next door, a third implementation of the
same thing: its own schema loader, its own subset validator, its own copy of
`trace_semantics`. It was written first and `lab_contracts` was ported from it,
after which the two drifted — the port grew maxLength, typed refs and fail-
closed type matching that the original never got. The examples were being
checked by the weaker of the two, which is the wrong way round for the file
that calls itself acceptance-test #1.

Three of the nine schemas belong to axor-core now, so the loader has to reach
into an installed package to find them; a script that globs a directory cannot.
"""
import json
import sys

from lab_contracts import validate_artifact

examples = json.load(open("examples/slice-examples.json"))
fails = 0
for name, (schema, obj) in examples.items():
    errs = validate_artifact(obj, schema)
    status = "PASS" if not errs else f"FAIL ({len(errs)})"
    print(f"\n{name}  [{schema}]  {status}")
    for er in errs[:12]:
        print("   -", er)
    fails += bool(errs)
print(f"\n{'=' * 40}\n{fails} example(s) failing")
sys.exit(1 if fails else 0)
