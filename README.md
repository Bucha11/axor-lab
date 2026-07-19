# axor-lab

Axor Lab — standalone research surface for the Axor governance stack: bring an agent, run attack scenarios ungoverned/governed on simulated tools, investigate single trials (EvidenceCase), replay governance verdicts exactly, and publish reproducible bundles.

- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — the production-ready implementation plan (phases, reuse map, milestones, definition of done).
- **[contracts/](contracts/)** — the engineering contract: 9 JSON Schemas, statistics/claims/provenance semantics, lifecycle, threat model, MVP contract, vertical slice, acceptance tests. Where prose and a contract disagree, the contract wins. Validate: `cd contracts && python3 validate.py && python3 validate_slice.py`.
- **[docs/design/](docs/design/)** — product narrative (spec-lab v0.3), packaging/economics, bench format guide, UI mocks.

## Packages (plan Phases 0–3, stdlib-only)

- **`lab_contracts/`** — the contract layer: schema loading + the contracts' own
  subset JSON-Schema validator (cwd-independent), semantic checks (author-time
  scenario validation, trace referential integrity), canonical JCS hashing,
  bundle assembly/verification, typed publication claims.
- **`lab_runner/`** — the execution engine + CLI: value ledger with
  conservative-join provenance, the single pure `decide` shared by live runs and
  replay, simulated tools with `$injection` fixtures, predicate evaluation,
  trial/suite runner (scripted agent behind a pluggable `AgentAdapter`), exact
  replay, EvidenceCase, regression pinning.
- **`lab_analysis/`** — the statistics engine (`contracts/statistics.md` as
  code): Wilson, exact McNemar over stored pairs, paired bootstrap, missingness
  honesty, unit-of-analysis enforcement.

## CLI quickstart (`axor-lab`, or `python -m lab_runner`)

```
axor-lab validate examples/banking-exfil-01.axl
axor-lab run examples/banking-exfil-01.axl --out ./bundle --yes
axor-lab replay ./bundle                       # exact: bit-identical verdicts
axor-lab pin ./bundle <trace_id> DENY --out pins.json
axor-lab regress ./bundle --pins pins.json     # surfaces changes, exit 4 if any
axor-lab evidence ./bundle <trace_id>          # the three-mode EvidenceCase
axor-lab publish ./bundle --question "…" --out publication.json
```

Lifecycle, exit codes, and the estimate-confirm gate follow
`contracts/runner-protocol.md` and `contracts/lifecycle.md`. The bundle
directory is the `axor-bundle-dir/v1` layout (`bundle.json` + `traces/`).

## Executable acceptance suite

`contracts/acceptance-tests.md` §1–10 runs as code against these packages —
one test file per criterion, plus two golden paths (in-process
`test_slice_e2e.py` and subprocess `test_cli_e2e.py`); every produced artifact
is validated against the real schemas in `contracts/`.

```
python -m unittest discover -s tests -t .      # 84 tests, no dependencies
```
