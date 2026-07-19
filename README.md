# axor-lab

Axor Lab — standalone research surface for the Axor governance stack: bring an agent, run attack scenarios ungoverned/governed on simulated tools, investigate single trials (EvidenceCase), replay governance verdicts exactly, and publish reproducible bundles.

- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — the production-ready implementation plan (phases, reuse map, milestones, definition of done).
- **[contracts/](contracts/)** — the engineering contract: 9 JSON Schemas, statistics/claims/provenance semantics, lifecycle, threat model, MVP contract, vertical slice, acceptance tests. Where prose and a contract disagree, the contract wins. Validate: `cd contracts && python3 validate.py && python3 validate_slice.py`.
- **[docs/design/](docs/design/)** — product narrative (spec-lab v0.3), packaging/economics, bench format guide, UI mocks.

## Executable acceptance suite

`contracts/acceptance-tests.md` §1–10 runs as code, today, against **`lab_ref/`** — a
minimal stdlib-only reference implementation of the vertical slice (value ledger with
conservative-join provenance, the pure `decide` shared by runner and replay, simulated
tools with `$injection` fixtures, a scripted agent standing in for the model layer,
Wilson/McNemar/bootstrap statistics, bundle hashing, typed claims, regression pins).

```
python -m unittest discover -s tests -t .      # 74 tests, no dependencies
```

- `tests/test_acceptance_01…10_*.py` — one file per acceptance criterion.
- `tests/test_slice_e2e.py` — the golden path: banking-exfil-01 compare run →
  paired aggregates → bundle → publication → bit-identical replay → regression pin,
  with every produced artifact validated against the real schemas in `contracts/`.

`lab_ref` is scaffolding for Phase 0–2 of the plan: the real packages replace it
module by module; the acceptance tests stay and must keep passing.
