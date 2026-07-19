# Axor Lab — Production-Ready Implementation Plan (v1)

> **Status (2026-07-19):** the MVP spine is implemented and covered by the
> executable acceptance suite (102 tests, stdlib-only):
> - **Phase 0** — `lab_contracts` (schema loader + subset validator, semantic
>   checks, canonical hashing, bundle, typed claims).
> - **Phases 1–2** — `lab_runner` (ledger/kernel/simulator/predicates/runner/
>   replay/EvidenceCase/regression, `AgentAdapter` with a scripted default,
>   `.axl` resolution, `axor-bundle-dir/v1` I/O) + the runner-protocol CLI.
> - **Phase 3** — `lab_analysis` (Wilson/McNemar/bootstrap/missingness).
> - **MVP item 2** — `lab_adapters`: the curated AgentDojo banking suite →
>   `scenario/v1`, with `axor-lab import-agentdojo`.
> - **Phase 4 + minimal Phase 5** — `lab_server`: publish handshake (schema +
>   hash + safe replay verification, `origin=local`), append-only attestations,
>   escaped HTML catalog / publication / EvidenceCase pages, three-axis
>   provenance; `axor-lab publish --server`. Plus a terminology lint.
>
> All ten acceptance criteria pass on the vertical slice, locally and over the
> hosted publish path — the MVP readiness criterion (domain-model.md) is met.
> Still open (post-MVP, per §5): a BYOK model-backed `AgentAdapter`, the
> production-grade server (Postgres/object storage/OAuth of Phase 6), the
> richer web app, and every Vision item (cloud code, endpoints, games).

Derived from the `lab/` design archive (spec-lab v0.3 + `contracts/` v1). The
archive is treated as the authoritative product/engineering contract; where this
plan and a contract disagree, the contract wins. Scope discipline follows
`contracts/mvp-contract.md` exactly: **the MVP is the vertical slice
(`contracts/vertical-slice.md`) productized — twelve items, nothing more.**
The definition of done is `contracts/acceptance-tests.md` (all ten, running
locally on the banking-exfil-01 slice).

---

## 1. What is being built

Axor Lab is a standalone research product (`lab.useaxor.net`): a researcher
brings an agent (code / traces; endpoints post-MVP), authors or imports attack
scenarios, runs them **ungoverned / governed / compare** on **simulated tools**,
and gets:

- **trace/v1** with per-value provenance (`explicit_flow_tracked` lineage),
- **EvidenceCase** — the single-trial investigation surface (three modes),
- **exact replay** of governance verdicts (`axor lab replay`, bit-identical),
- honest **statistics** (Wilson / McNemar / bootstrap, per `statistics.md`),
- a **bundle/v1 → publication/v1** publishing path with typed claims
  (exactly_replayable vs statistically_reproducible),
- **regression pinning** (trace + expected verdict; changes surfaced, not
  auto-failed).

MVP executes **no untrusted code on Lab servers** — the runner is local-only.
The server only validates schemas/hashes, re-runs safe deterministic replay,
and serves the catalog. This keeps the sandbox (the most expensive subsystem)
off the critical path.

## 2. Reuse map — what already exists in the Axor repos

The plan is grounded in code that already ships; Lab is assembled around
`axor-core`, not built in parallel with it.

| Need (contract) | Existing asset | Reuse plan |
|---|---|---|
| Governance verdicts, gates, policy profiles | `axor-core` (`kernel/`, `policy/`, `governor.py`, `profiles.py`) | **Target integration.** Today Lab ships `reference_taint_floor_kernel` (one gate, one pure `decide` shared by runner+replay) and records `condition.kernel = axor-core@X.Y.Z` as intent metadata; loading the real versioned axor-core `decide` is the integration tracked in POST_MVP_PLAN.md. The version string is not yet a loaded historical kernel (review P0.2). |
| Deterministic replay | `axor_core.kernel.replay` (pure-gate re-evaluation, first-divergence rule, adjudicator exception) | `axor lab replay` is a thin driver over it, folding `trace/v1` events |
| Per-value taint / provenance | `axor_core.taint` (ledger, causal_root, engine) | Extend with the Lab `model_extraction` constructor (conservative join, `provenance-semantics.md` §2); over-taint never under-taint |
| Trace collection | `axor_core.trace` (collector, events, guard) | Adapter emits `trace/v1` (schema in contracts) from kernel events + value ledger |
| EvidenceCase concept, fault scenarios | `axor-eval` (EvidenceCase artifact, runner, replay recorder/player) | Lab EvidenceCase = view over `trace/v1`; reuse recorder patterns, not the eval-specific verdict logic |
| AgentDojo integration | `axor-eval/experiments/agentdojo/*` (bridge, claims, protocol) | Source for the curated AgentDojo → `scenario/v1` adapter |
| Out-of-process tool execution (later, real side effects) | `axor-daemon` | Post-MVP: opt-in real execution path behind daemon isolation |
| Backend/proxy/frontend platform patterns | `axor-control-plane` (`packages/axor-backend`, `axor-proxy`, `frontend`) | Server + web app follow the same stack/conventions; CP-handoff export targets its config format |
| Contract schemas + validator | archive `lab/contracts/` (9 schemas, `validate.py`, slice examples, 8/8 green) | Imported verbatim into this repo as the `lab-contracts` package (Phase 0) |

## 3. Repository layout (this repo)

```
axor-lab/
  contracts/            # imported verbatim from the archive — source of truth
    schemas/*.schema.json
    examples/           # slice examples (golden fixtures)
    *.md                # statistics, claims, provenance-semantics, lifecycle, …
  packages/
    lab_contracts/      # Python: schema loading, validation (real jsonschema),
                        #   semantic checks (referential integrity), typed models
    lab_runner/         # CLI `axor lab …`: resolve→estimate→execute→gate→analyze→bundle
    lab_analysis/       # statistics engine (Wilson, McNemar, bootstrap, missingness)
    lab_server/         # API: publish, catalog, publication pages, replay verification
    lab_adapters/       # agentdojo import; framework shims (langchain/mcp) post-slice
  frontend/             # lab web app (landing/catalog, results, EvidenceCase, publication)
  docs/
  tests/                # acceptance suite = acceptance-tests.md §1–10
```

Licensing per `cp-monetization.md` §3: this repo is Apache-2.0 (it underpins
the paper's reproducibility story). Org/paid features (private workspaces,
scheduled CI) land later under the platform's `/ee` pattern, **not** here in v1.

## 4. Phases

Ordering rule (from `mvp-contract.md`): build the spine first — everything
outward from the vertical slice; no phase starts on a subsystem the slice
doesn't need.

### Phase 0 — Contracts as code (foundation)
*Everything else compiles against this.*

- Import `contracts/` from the archive verbatim; wire `validate.py` +
  `validate_slice.py` into CI (must stay 8/8 green).
- `lab_contracts` package: typed models (pydantic/dataclasses) for all nine
  schemas — trace, scenario, tool-manifest, predicate, condition, experiment,
  bundle, publication, attestation; real `jsonschema` validation plus the
  semantic checks JSON Schema can't express (value_id referential integrity,
  `$inputs` resolution, fixture `injection_placement` targets an
  `untrusted_fields` entry, predicate tool references exist, egress sink
  present).
- Canonical content-hashing (JCS-style canonical JSON — reuse
  `axor_core.kernel.jcs`) for bundle hashes.
- **Exit:** acceptance test 1 (validation rejects bad scenarios, with
  stage-specific errors) passes as a unit suite.

### Phase 1 — Local runner + simulated tools + provenance traces
*MVP items 1, 4, 5, 6, 7.*

- `axor lab run experiment.axl` implementing the runner protocol:
  `resolve → estimate (trials × model, confirm) → execute → gate → analyze →
  bundle`, with the **local** lifecycle plan
  (`validating → waiting_for_runner → running_local → uploading_artifacts →
  analyzing → completed|failed|cancelled`) and its rules: cancel keeps
  completed trials, retry targets the failed subset only, idempotent trial
  replacement on (scenario, condition, seed, repeat).
- Tool simulator layer: fixtures with `$injection` placement, `ledger_stub`,
  reset strategies (`fixture`, `snapshot_restore`). `side_effecting` tools
  **never** execute for real in v1 (threat-model §1); the opt-in path
  (`isolated_test_account` + `resource_allowlist` + `dry_run_confirmed`) is
  schema-present but unimplemented-and-rejected.
- Wrapped local runtime: model call boundary + tool I/O pass through a value
  ledger. Implements the **conservative join**: any model-emitted value gets
  `labels=[untrusted_derived]`, `derived_from = all untrusted context values`,
  `transformations=[model_extraction]` when untrusted values were live in
  context. Producer stamps `mode=wrapped_code`,
  `provenance_fidelity=explicit_flow_tracked`.
- Conditions: `ungoverned` (enforcement off — **observation stays on**) and
  `governed` (pinned `axor-core` version + policy profile + `config_hash`).
  Gate decisions recorded in-trace as `gate_decision` events.
- Predicate evaluator: the MVP DSL subset — `event_match` + `matcher_map` +
  `equal / not_equal / in / not_in / matches / provenance_is` + `$inputs`
  refs; boolean composition (`all/any/not/sequence`) supported by the
  evaluator even though the authoring UI defers it.
- BYOK inference: provider key from env/config; pre-run cost estimate printed
  before any live trial.
- **Exit:** banking-exfil-01 runs end-to-end locally under both conditions;
  acceptance tests 2 (simulation safety) and 3 (trace lineage) pass;
  governed trial reproduces the §4 slice trace shape exactly.

### Phase 2 — Replay, EvidenceCase, regression
*MVP items 8, 9, 12.*

- `axor lab replay ./bundle`: fold frozen `trace/v1` events through the pinned
  kernel's pure gates (drive `axor_core.kernel.replay`); output verdicts
  bit-identical, offline, no model calls. CI job runs replay twice on two
  runners and diffs byte-for-byte (acceptance test 5).
- EvidenceCase renderer (CLI/JSON first, web in Phase 3): the three modes from
  `claims.md` — *observed: ungoverned*, *counterfactual: policy replay*
  (labeled counterfactual, verdict-only), *observed: governed live twin*
  (only when a governed run exists; never faked). Renders the full chain:
  injection → provenance (value ledger) → gated call → verdict, with the
  explicit-flow boundary caveat surfaced.
- RegressionCase: pin `{trace_id, expected_verdict}`; `axor lab regress`
  re-runs pinned traces under the current kernel and **surfaces** any change
  ("differs from pinned expected") for the user to label regression vs
  approved baseline update — never a silent pass, never a hard "must DENY
  forever" (acceptance tests 4, 10).
- **Exit:** acceptance tests 4, 5, 10.

### Phase 3 — Statistics engine
*The honesty layer; small but heavily specified (`statistics.md`).*

- `lab_analysis`: aggregates carry `{metric, estimate,
  interval:{method,low,high}, n, unit_of_analysis, test}` — computed at run
  time, **never** at render time.
- Binary metrics: Wilson 95% CI; paired McNemar over **stored per-pair
  outcomes** (the bundle stores the pairing, not just marginals); unpaired
  Fisher/χ². Continuous: mean + paired bootstrap (resample runs, never
  rounds); Wilcoxon; effect sizes.
- Guardrails as code: unit_of_analysis = trial/run only (reject "round");
  n<10 → "inconclusive", significance suppressed; missingness reported as
  denominator + excluded count with reasons, non-random missingness flags the
  aggregate potentially-biased; Holm–Bonferroni when >2 comparisons;
  significance is the test result, never CI-overlap; no CI ever attaches to a
  replayed verdict.
- **Exit:** acceptance test 6 (including "CI narrows from n=10 → n=100"
  property test).

### Phase 4 — Bundle, publish, server, catalog
*MVP items 10, 11; runner-protocol handshake; first hosted surface.*

- Bundle writer: `bundle/v1` with the full load-bearing manifest — schema
  version, scenario+bench, conditions (+`config_hash`), tool manifests,
  **kernel version**, model provider/id/params, seeds, trials (incl.
  `failure_reason`), traces, verdicts, aggregates, content hashes, author,
  license, redaction manifest.
- `lab_server` (stack follows `axor-control-plane/packages/axor-backend`):
  - `POST /publications` (publish handshake): schema-validate, verify content
    hashes → `integrity=hash_verified` (or `signed` with a known Ed25519 key —
    same crypto as CP licensing), re-run **replay only** to confirm published
    verdicts match traces, assign immutable `publication_id`. `origin=local`
    always for runner uploads; the server never claims `lab_infra` for them
    and never executes live runs.
  - Publication page `/e/{id}`: immutable record — question, result with CI,
    methodology + pinned kernel/config-hash, limitations, the two typed claim
    blocks (exactly replayable ≠ statistically reproducible, never merged),
    reproduce commands (`replay` vs `run`, labeled with their distinct
    meanings), fork, citation, reproduction list (typed: exact_replay |
    fresh_live | changed_model | changed_kernel).
  - Catalog: published experiments with the **three-axis** provenance display
    (origin × integrity × reproductions — never collapsed to one badge).
  - Payload hygiene (threat-model §4): schema validation, all strings
    rendered as untrusted (no HTML injection), size limits, content hashes,
    redaction manifest enforced, takedown path.
- Public/unlisted visibility; private bundles stored hash-verified.
- **Exit:** acceptance tests 7, 8, 9 (typed claims; bundle round-trip
  download → replay → identical verdicts; multidimensional provenance).

### Phase 5 — Web app + AgentDojo import + launch polish
*MVP items 2, 3 (authoring subset); the archive `mocks/*.jsx` are the visual
contract.*

- Frontend (per mocks): landing + catalog (three descending-barrier entries:
  explore → reproduce → bring your agent), scenario author (MVP predicate
  subset only), run progress (explicit lifecycle states — never a bare
  spinner; stage-specific failures), results (renders stored aggregates
  verbatim; "not computed" over placeholder numbers), EvidenceCase (the
  differentiating screen), publication page.
- Curated AgentDojo adapter (`lab_adapters`): one suite materialized as
  `scenario/v1` objects per `bench-format.md` — tasks → tasks, injections →
  fixture placements, benign goal → `task_success`, attack goal →
  `violation`; "undefended/defended" maps to ungoverned/governed conditions
  (the word "undefended" survives only as AgentDojo's term).
- Terminology lint: UI copy uses only ungoverned/governed/compare;
  "deterministic" never attaches to a live run (enforced by a copy-check test
  over the frontend strings).
- Seed content: the paper's experiments as published bundles + one authored
  benchmark + `template.axl`.
- **Exit:** all ten acceptance tests green end-to-end through the UI paths;
  vertical-slice readiness criterion met → **launch with the paper**.

### Phase 6 — Production hardening (pre-GA checklist)

Not features — the "-ready" in production-ready:

- **Infra:** containerized `lab_server` + frontend; Postgres (publications,
  catalog, users) + object storage for bundles (content-addressed); CDN for
  public pages; backups + restore drill; migrations.
- **AuthN/Z:** GitHub OAuth (as CP free tier); publications owned by
  accounts; unlisted = capability URL; rate limits on publish.
- **Observability:** structured logs, error tracking, metrics on
  publish/replay/verify latency; alerting.
- **CI/CD:** per-package tests, the acceptance suite as the release gate,
  schema-compat check (a schema change that breaks slice examples fails CI),
  pinned-kernel matrix (replay determinism across two OS runners).
- **Supply chain:** lockfiles, dependency audit, signed releases; PyPI
  packages `axor-lab` (runner) published like `axor-core`.
- **Legal/ops:** takedown workflow, license notice (Apache-2.0), privacy note
  ("observations only, never raw bodies" + redaction manifest).

> The post-MVP blocks below are planned in detail in
> **[POST_MVP_PLAN.md](POST_MVP_PLAN.md)** (BYOK adapter, Control Plane export,
> web app, hardening, then endpoints/sandbox/games/scale, plus the commercial
> track) — each with a contract anchor and an executable definition of done.

## 5. Post-MVP sequence (from the contracts — not in v1 scope)

- **Then:** full boolean predicate authoring UI · richer local tool binding ·
  BYOK inference in-app · Control Plane export (`control-plane-handoff.md`:
  reuse policy + `config_hash` + tool manifests + regression cases; add
  production bindings/credentials/topology — the "earned bridge", surfaced
  only after a result where governance changed an outcome) · cloud runner for
  trusted templates only.
- **Later:** instrumented-endpoint contract (`endpoint-protocol.md`: SSE
  events + tool gateway; black-box mode labeled evaluation-only everywhere,
  with full SSRF/DNS-rebinding/egress protections) · arbitrary cloud code
  behind the real sandbox (gVisor/Firecracker-class, per spec §9) ·
  real-side-effect opt-in via `axor-daemon` isolation · multi-agent games ·
  population scale.
- **Commercial track** (parallel, per `axor-packaging.md`): pricing page from
  day one; first revenue via the $7.5k Incident-to-Regression Pilot; Team/
  Security workspaces (hosted private Lab) only after public Lab launches.

## 6. Milestones & sequencing

Phases 1–2 are the critical path and depend on Phase 0. Phase 3 and Phase 4's
server work parallelize after Phase 1. Suggested milestone cut:

| Milestone | Contents | Demo artifact |
|---|---|---|
| **M1 — "the core exists"** | Phases 0–2 | vertical-slice readiness criterion: slice validates, runs locally on ledger_stub, trace carries lineage, EvidenceCase renders 3 modes, replay exact, regression pins |
| **M2 — "it publishes"** | Phases 3–4 | bundle → publish → `/e/{id}` → re-download → replay identical; honest stats |
| **M3 — "it launches"** | Phase 5 | AgentDojo import + web UI + seed catalog; all 10 acceptance tests via UI |
| **M4 — GA** | Phase 6 | hardened hosted deployment at lab.useaxor.net, shipped with the paper |

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Provenance join too coarse → everything tainted, utility collapses | It's the paper's own measured cost (over-taint by design); declared allowlists are the only recovery path — never heuristic un-tainting |
| Replay not actually bit-identical across machines/kernel builds | Pin kernel by exact version; canonical JSON (JCS); CI determinism matrix from Phase 2 day one |
| Statistics drift into decoration (render-time p-values, CI-overlap) | No render-time computation code path exists; UI renders stored aggregate fields verbatim or "not computed" |
| Scope creep toward Vision items (sandbox, endpoints, games) | `mvp-contract.md` NOT-list enforced in review; any Vision item needs its own contract first |
| Published-payload abuse (XSS, oversized, fake results) | Threat-model §4 controls in Phase 4; three-axis provenance keeps self-reported visually distinct from lab-executed |
| Claim over-reach in copy ("reproduce exactly", implicit flow) | claims.md phrasing table + terminology lint test; explicit-flow boundary stated in EvidenceCase UI |
| Kernel API drift between axor-core releases and pinned conditions | Conditions pin exact versions; regression suite re-runs pinned traces on kernel upgrades and surfaces verdict changes |

## 8. Definition of done (v1)

All ten items of `contracts/acceptance-tests.md` pass, end-to-end, on the
banking-exfil-01 vertical slice, locally and through the hosted publish path —
at which point every schema in `contracts/` has an executable referent, and
building outward (cloud, endpoints, games) rests on solid ground.
