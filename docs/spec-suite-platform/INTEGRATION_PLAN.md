# Axor Lab — Suite Platform Integration Plan

**Status:** plan, not yet implemented.
**Authority:** the Experiment Suite Platform RFC in this directory is the
governing spec. Where it and `docs/spec-v0.3/` disagree, **the new spec wins**.
Where it and `contracts/` disagree, the new spec sets the *target* and
`contracts/` is migrated toward it (contracts stay authoritative for what the
code does *today* — a schema is only "wrong" once its replacement is landed and
green).

**One deliberate deviation from the spec:** the "remote endpoint" input (spec
§5) is **not adopted** — see §4.6. Every other spec-vs-repo conflict resolves in
the spec's favour.

Sources this plan reconciles:

| Source | Role |
|---|---|
| `Axor-Lab-RFC-Experiment-Suite-Platform.md` | product/platform concept — governing |
| `Axor-Lab-Web-UX-Home-RFC.md` | Home/Launchpad + navigation — governing |
| `early-concept-draft.md` | context only, superseded by the RFC |
| `design/axor-lab-dark-ui-design-system.png` | **current** visual direction (teal, dark-first) |
| `design/axor-lab-light-concept-and-design-system.png` | screen inventory (Suite Builder, Run Report, Trial, Evidence, Regression) |
| `design/axor-lab-dark-home-concept.png` | earlier purple concept — layout reference only |
| `docs/spec-v0.3/`, `contracts/`, the code | what exists today |

---

## 1. What the new spec actually changes

The repo today is a **governance-comparison instrument**: every experiment is a
paired ungoverned-vs-governed study, an EvidenceCase *is* an injection→provenance→
gate→verdict chain, and a regression *is* a pinned gate verdict.

The new spec inverts the centre of gravity:

> Axor Lab is a reproducible experiment platform for AI agents. It is **not**
> primarily a governance product. Governance is an optional capability that
> experiment suites may use.

Concretely, four load-bearing inversions:

1. **Experiment Suite becomes the core abstraction.** Today the closest thing is
   the `.axl` file (experiment + scenarios + tool manifests) plus an implicit
   "bench". The spec's suite additionally owns *evaluators, metrics,
   aggregations, artifact layout, regression rules, execution strategy* and ships
   with a **Suite SDK** (config schema, UI schema, validators, hooks, artifact
   renderer, regression extractor). None of that exists.
2. **Governance demotes from spine to plugin.** Today `experiment/v1` requires
   `conditions` with `minItems: 2` — you *cannot* express "just run my agent".
   The spec requires exactly that to be the default.
3. **EvidenceCase and Regression generalise.** EvidenceCase becomes any curated
   investigation (latency spike, hallucination, planner failure, budget
   overflow…); Regression becomes any executable invariant
   (`latency < threshold`, `budget <= limit`, `task_success == true`,
   `forbidden_tool_calls == 0`, custom evaluator).
4. **The product surface is a web app.** Home/Launchpad, Suite Catalog,
   Playground, Run, Run Report, Trial, EvidenceCase, Regression, Suite Builder.
   Today: zero frontend — nine `.jsx` mocks in `docs/` and three server-rendered
   HTML pages.

None of this invalidates the engine. The hardened parts — the value ledger,
canonical JCS hashing, exact replay, the typed predicate evaluator, bundle
verification, the publish handshake — are exactly what a general experiment
platform needs. The work is **re-seating them under a wider abstraction**, not
rewriting them.

---

## 2. Current state — audit

### 2.1 What exists (≈23k lines Python, stdlib-only core, no frontend)

| Package | Role | Verdict against the new spec |
|---|---|---|
| `lab_contracts/` | 9 JSON Schemas, subset validator, JCS canonical hashing, bundle assembly/verify, typed publication claims | **keep, extend.** Hashing + bundle verification are platform-grade. Schemas need the suite/evidence/regression/metrics layer. |
| `lab_runner/` | value ledger, `decide`, simulated tools, predicate evaluator, trial/suite runner, replay, EvidenceCase, regression pinning, CP export | **split.** ~60% is generic execution/replay; ~40% (`kernel.py`, `claims.py`, `cp_export.py`, the governance half of `evidence.py`/`regression.py`) is the governance capability. |
| `lab_analysis/` | Wilson, exact McNemar, paired bootstrap, missingness, unit-of-analysis | **keep, demote.** McNemar is a *comparison-suite* aggregation, not a platform default. |
| `lab_adapters/` | curated AgentDojo banking subset → `scenario/v1` | **becomes the first Suite SDK implementation.** |
| `lab_server/` | publish handshake, catalog/publication/EvidenceCase HTML, recompute, `runtime_jobs.py` (runtime pull API + UI control endpoints) | **keep the store, replace the surface.** `runtime_jobs.py` is the right shape and the right place to grow the screen API. |
| `lab_agent/` | BYOK model backend, cassettes, cost estimate | **keep** — becomes a suite execution backend. |
| `contracts/` | 11 contract docs + 9 schemas + validators + slice examples | **the migration lives here first** (repo is contract-first). |
| `tests/` (78 files) | 10 acceptance criteria + hardening suites | **keep green.** They are the governance capability's acceptance suite. |

Deleted in the v0.3 re-scope and relevant again: `lab_endpoint`, `lab_sandbox`,
`lab_games`, `lab_entitlement` (see §4, conflicts 6/7/8).

### 2.2 Concept mapping — new spec → repo today

| Spec concept | Repo today | Gap |
|---|---|---|
| Experiment Suite | `.axl` file + `experiment/v1` + implicit bench | **large** — no evaluators/metrics/aggregations/artifact-layout/regression-rules; no SDK; `conditions` mandatory |
| Experiment Run | `Run` / `run_experiment_suite()` | small — rename + drop governance assumptions |
| Trial | `trial` record in `bundle/v1` | **medium** — carries no metrics (no latency, tokens, cost, steps) |
| Observation / Trace | `trace/v1` + value ledger | **none** — richer than the spec asks |
| EvidenceCase | `lab_runner/evidence.py`, 3 governance modes | **large** — hardwired to the injection chain; no `kind` |
| Regression | `lab_runner/regression.py`, pinned verdict sequence | **large** — only gate verdicts; no metric/predicate invariants |
| Artifact | `bundle/v1` + `publication/v1` | **medium** — missing suite ref, agent identity block, metrics, embedded evidence/regressions, reproduce instructions |
| Governance (optional) | the spine | **inversion** — see §4.1 |
| Suite SDK | — | **absent** |
| Suite Builder | `docs/**/mocks/lab-builder.jsx` | **absent** (no frontend) |
| Home / Launchpad | server-rendered publication catalog | **absent** — different concept (past vs next action) |
| Nav: Home/Suites/Runs/Evidence/Regressions/Artifacts/Settings | 3 HTML pages | **absent** |
| Inputs: live / uploaded traces / recorded bundles / simulated envs (remote endpoint **dropped**, §4.6) | connected-runtime + local simulated runner; import partial | **small** — finish trace import; endpoint stays out |
| Multi-agent topologies | `lab_games` deleted | deferred — see §4.7 |
| Open core split | design-only; `lab_entitlement` deleted | deferred — see §4.8 |

### 2.3 The hard blockers *(all three cleared in Phase 1)*

Everything else is additive. These are structural and gated the rest:

- **`experiment.schema.json` → `conditions: minItems 2`.** The schema makes a
  single-arm, no-governance experiment *unrepresentable*. Nothing in the spec's
  primary workflow ("bring an agent → run a suite → inspect a trial") can be
  expressed until this changes.
- **No per-trial metrics.** The design's Run Report, Trial view and Regression
  screens are built on Duration / Steps / Tokens / Cost / Latency p95, and the
  spec's regression examples are `latency < threshold` and `budget <= limit`.
  The trial record has none of them. This blocks three screens and half the
  regression kinds.
- **`scenario.schema.json` requires `injection` and `violation`.** Found while
  writing the first governance-free example: a scenario with no attack model —
  every Budget, Performance and Reliability scenario — could not be expressed
  either. Same inversion, one level down.

---

## 3. Target architecture

```
                        Suite manifest (suite/v1)  ← one portable document
                                  │                   Basic / Advanced / YAML edit the same file
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
   Suite SDK                  Platform core            Capabilities (optional)
   (lab_suite/)               (lab_runner/,            (lab_capabilities/)
   · config schema             lab_contracts/)         · governance  ← today's spine
   · ui schema                · execution              · provenance
   · validators               · observation            · policy gates
   · execution hooks          · metrics                · deterministic verdict replay
   · metrics                  · investigation          · Control Plane export
   · artifact renderer        · reproducibility
   · regression extractor
   · evidence helpers
        │                         │                         │
        └─────────────────────────┼─────────────────────────┘
                                  │
                       Artifact (artifact/v1)
                    suite · config · environment · agent identity
                    observations · traces · metrics · evidence cases
                    regressions · reproduce · signatures · hashes
                                  │
                        lab_server (screen API)
                                  │
                        web app (Home → Builder)
```

Rules that keep this honest and are carried over unchanged from the current
contract:

- **Rendered, never computed.** A screen renders what an endpoint returns; the
  server recomputes statistics from traces and never trusts an uploaded
  aggregate.
- **Lab assigns, the runtime executes.** Lab does not dispatch tools, hold tool
  credentials, or act as a synchronous enforcement boundary
  (`contracts/architecture-boundary.md`). This is why the spec's "remote
  endpoint" input is dropped rather than reconciled (§4.6) — an agent behind an
  HTTP endpoint is reached by a runtime adapter beside it, never by Lab calling
  out.
- **One word per concept.** The terminology lint stays; its vocabulary widens.

---

## 4. Conflicts and their resolutions

Eight places where the new spec and the repo disagree. Resolution defaults to
the spec.

### 4.1 Governance: spine → plugin **(spec wins)**

- `experiment/v1`: `conditions` becomes **optional**, `minItems: 1` when present.
  Absent ⇒ one implicit single-arm condition (`enforcement: off`, no kernel).
- The runner must complete a trial with **no kernel resolution at all**. Today
  kernel resolution happens on every path (`resolve_kernel_for_trace` and
  friends); it becomes conditional on the suite declaring the governance
  capability.
- `lab_analysis`: McNemar/paired designs move behind "the suite declares a
  comparison design". A single-arm suite gets descriptive stats + Wilson
  intervals, never a paired test.
- The **earned Control Plane bridge, `config_provenance`, `runtime_config_hash`,
  `resolved_kernel_fingerprint`** and the r18–r21 correctness work stay exactly
  as they are — they simply become the governance capability's internals rather
  than platform invariants. Nothing is deleted.

### 4.2 EvidenceCase: security-specific → generic **(spec wins)**

New `evidence-case/v1`: `{ id, kind, trial_ref, summary, severity, status,
timeline[], attachments[], metrics{}, linked_regressions[], notes }`.

`kind` is an open string with registered built-ins matching the spec's list:
`prompt_injection`, `hallucination`, `latency_spike`, `planner_failure`,
`budget_overflow`, `consensus_failure`, `secret_leakage`, `unexpected_recovery`.
The current injection chain becomes `kind: prompt_injection`, produced by the
governance capability's extractor, with its three modes
(observed-ungoverned / counterfactual-policy-replay / observed-governed-twin)
preserved verbatim as `evidence.modes`.

Extraction is pluggable: a suite contributes `evidence_helpers` per the SDK.

### 4.3 Regression: pinned verdict → executable invariant **(spec wins)**

New `regression/v1`: `{ id, name, source_trial_ref, rule, expectation, status,
history[] }` where `rule` is one of:

| kind | Backed by | Status |
|---|---|---|
| `predicate` | `lab_runner/predicates.py` (already typed, already evaluates over traces) | **exists** — just needs exposing |
| `metric_threshold` | per-trial metrics block | **blocked on §2.3** |
| `verdict_sequence` | today's `RegressionPin` | **exists** — becomes one kind |
| `evaluator_outcome` | Suite SDK evaluator | new |

The predicate evaluator is the single biggest asset here: the spec's
"executable invariant" is *already implemented*, it is just only reachable via
`violation`/`task_success` inside a scenario.

### 4.4 Bundle → Artifact **(spec wins on the noun, not on the bytes)**

The spec's user-facing noun is **Artifact**. `bundle/v1` is content-addressed
and pinned by tests, canonicalization vectors and published hashes — renaming it
in place would invalidate every existing hash.

Resolution: introduce `artifact/v1` as `bundle/v1` **plus** the fields the spec
§14 names that are missing — `suite` (the manifest that produced it),
`agent_identity`, per-trial `metrics`, `evidence_cases[]`, `regressions[]`,
`reproduce` (the exact command + environment). `bundle/v1` remains readable; a
v1 bundle loads as an artifact with those fields empty. The UI says "Artifact"
everywhere; `bundle` survives as an internal/legacy term only.

### 4.5 Suite manifest is the single editable document **(spec wins)**

Spec §13: Basic / Advanced / YAML modes all edit the same portable manifest.
That forces `suite/v1` to be **complete** — every Builder field must have a home
in the manifest, or the Basic mode silently owns state the YAML mode can't see.
This is the main design constraint on Phase 1 and the reason the schema must
land before any Builder work.

### 4.6 "Remote endpoint" as an input — **not adopted** *(owner decision, deviates from the spec)*

Spec §5 lists "remote endpoint" among the inputs an experiment may consume.
**Dropped from scope by owner decision.** This is the one place this plan
knowingly does not follow the spec, recorded here so the deviation is traceable
rather than silent.

Rationale: v0.3 deleted `lab_endpoint` because *Lab must not dispatch tools,
hold tool credentials, or be a synchronous enforcement boundary*
(`contracts/architecture-boundary.md`). Reinstating an endpoint input reopens
that boundary along with its SSRF and credential-custody surface, and buys
nothing the four supported inputs don't already cover.

**Supported inputs are therefore four, not five:** live agent execution (via a
connected runtime), uploaded traces, recorded bundles, simulated environments.
An agent that lives behind an HTTP endpoint is reached the same way any other
agent is — a runtime adapter runs beside it and pushes traces. Lab never calls
it.

Consequences to hold to:
- `suite/v1` must **not** grow an endpoint/URL field in Phase 1.
- `TraceSource` stays `runtime | import | demo | offline_runner` — no `endpoint`
  member.
- If this is revisited, it is a separate RFC, not a schema patch.

### 4.7 Multi-agent **(spec wins, deferred)**

Spec §11: the platform is agent-count agnostic; only the suite changes. That is
satisfied by `suite.agents[]` being a list from day one, and a topology field
that Phase 1–4 accept and validate but only Phase 6 executes. `lab_games` is
**not** resurrected — its toy iterated-game runtime is not the abstraction the
spec describes.

### 4.8 Open core split **(spec wins, deferred)**

Spec §16 draws the line at: open = suite format, runner, replay, artifact
format, regression format, SDK; commercial = Suite Builder, hosted workspace,
collaboration, hosted execution, artifact registry. This is a packaging decision
that only bites once the Builder exists — Phase 5. Recorded now so Phase 1
schema work stays in the open half.

---

## 5. Design system — extracted tokens

The teal dark board is the current direction (pack README). Hex labels in the
board are generated text; values below are the labels cross-checked against
**sampled swatch pixels**, and agree to within gradient noise. Semantic colors
were illegible as text and are sampled only — re-derive before committing them.

```
accent      #00D4A4  primary     ·  #20B486  pressed  ·  #4EE0C2  soft/on-dark-text
neutrals    #080F14  background  ·  #11161D  surface  ·  #161C24  surface-raised
            #1D2430  border      ·  #2A313C  border-strong
            #3A4455  muted-fg    ·  #E6EBF1  foreground
semantic    success ~#21A852 · warning ~#E6891D · danger ~#E34437
            info ~#2879EF · purple/special ~#7E55F0     (sampled — verify)
type        Inter — H1 32/40 bold · H2 24/32 semibold · H3 20/28 semibold
            H4 18/24 medium · body-lg 16/24 · body 14/20 · body-sm 12/16
            caption 11/16 · weights 400/500/600/700
radius      4 · 8 · 12 · 16 · 20
spacing     8pt grid — 8 16 24 32 40 48 64
icons       Lucide
components  Button (primary/secondary/ghost × default/hover/pressed/disabled)
            Input (label/default/hover/focus) · Card (default/hover/active)
            Tag (default/success/warning/danger/info) · Progress · Tabs
```

A light theme exists on the earlier board with a full parallel ramp; treat
dark as the default and light as a second theme built from the same token
names, not a separate design.

### Screen inventory the boards define

| Screen | Board | Backing endpoint (Phase 3) |
|---|---|---|
| Home / Launchpad | dark + light | `GET /home` |
| Suite Catalog | dark | `GET /suites` |
| Playground — One Trial | dark | `POST /playground/trial` |
| Run (live progress) | dark + light | `GET /runs/{id}` + `SSE /runs/{id}/events` |
| Run Report | dark + light | `GET /runs/{id}/report` |
| Trial detail | dark + light | `GET /runs/{id}/trials/{tid}` |
| EvidenceCase | dark + light | `GET /evidence/{id}` |
| Regression | dark + light | `GET /regressions/{id}` |
| Suite Builder (6 tabs) | light | `GET/PUT /suites/{id}` + `POST /suites/validate` |

Nav (Web UX RFC): Home · Suites · Runs · Evidence · Regressions · Artifacts ·
Playground · Integrations · Settings. Home is the default landing page and is
**about the next action, not the past**.

---

## 6. Phased plan

Each phase is independently shippable and leaves the test suite green.

### Phase 0 — Freeze the spec, record decisions *(done by this commit)*

- Spec pack + design boards land in `docs/spec-suite-platform/`.
- This plan records the eight conflict resolutions.
- `docs/spec-v0.3/` is marked **superseded as product narrative**; its contract
  docs stay authoritative until Phase 1 replaces them.
- README positioning line updated: research surface for the governance stack →
  reproducible experiment platform, governance optional.

**Exit:** a reader knows which document wins. No code change.

### Phase 1 — Domain re-model (contracts first) · ~1 week — **schemas landed**

**Status: the schema + contract layer is done and green** (626 tests, both
validators, 14/14 slice examples). What remains in this phase is the
`lifecycle.md` / `ui-backend-contract.md` / `mvp-contract.md` rewrites, which
describe surfaces Phase 3 builds.

A third blocker of the same family surfaced while writing the first
governance-free example and is fixed: **`scenario/v1` required `injection` and
`violation`**, so a Budget or Performance scenario — one with no attack model at
all — was as unrepresentable as a run with no conditions. Both are now optional
and travel together; `task_success` stays required (a scenario that cannot say
what success means is a prompt, not an experiment), and declaring a `violation`
with no `injection` is now an explicit authoring error rather than a breach
criterion that can never fire.


This repo is contract-first; schemas lead, code follows.

New schemas:
- `suite.schema.json` (`suite/v1`) — agents[], scenarios[], environment, tools,
  execution (strategy, repeats, seeds, concurrency, budgets), evaluators[],
  metrics[], aggregations[], artifact layout, regressions[], capabilities[]
  (governance opt-in lives here), ui_schema hints.
- `evidence-case.schema.json` (`evidence-case/v1`) — §4.2.
- `regression.schema.json` (`regression/v1`) — §4.3.
- `artifact.schema.json` (`artifact/v1`) — §4.4.

Changed schemas:
- `experiment.schema.json` — `conditions` optional, `minItems: 1`; add
  `suite_ref`. **Unblocks §2.3 blocker 1.**
- `bundle.schema.json` — trial gains a `metrics` object
  (`duration_ms`, `steps`, `tokens_in`, `tokens_out`, `cost_usd`, extensible).
  **Unblocks §2.3 blocker 2.**

Docs: rewrite `contracts/domain-model.md` around Suite→Run→Trial→Observation→
EvidenceCase→Regression→Artifact; update `lifecycle.md` (five input modes),
`ui-backend-contract.md` (new screen table), `mvp-contract.md`.

**Exit:** `contracts/validate.py` + `validate_slice.py` green with new slice
examples including a **governance-free single-arm suite**; full test suite green;
a v1 bundle still loads.

### Phase 2 — Suite SDK + governance as a capability · ~2–3 weeks

- New `lab_suite/`: the `Suite` protocol per spec §12 (config schema, ui schema,
  validators, execution hooks, metrics, artifact renderer, regression extractor,
  evidence helpers) + a registry + manifest load/save/round-trip.
- Built-in suites — **launch set is three** (owner decision): **Blank**
  (proves the SDK is authorable from nothing), **AgentDojo** (port
  `lab_adapters/agentdojo.py` — proves an import path), **Budget** (proves the
  new metrics layer, since it is nothing *but* metrics). Together they exercise
  every SDK surface once. **Prompt Injection**, **Performance** and
  **Reliability** trail into Phase 4/5 — the design's catalog shows six cards,
  so the Suite Catalog screen must render a "coming soon" state rather than
  pretend the other three exist.
- New `lab_capabilities/governance/`: move `kernel.py`, condition resolution,
  the gate EvidenceCase extractor, verdict pins, `cp_export.py`, and the
  McNemar/earned-bridge path out of the spine. Public behaviour unchanged.
- Runner: generic metrics collection (wall-clock, steps, tokens, cost) on every
  trial; governance-free execution path.
- Generic evidence + regression engines over the existing predicate evaluator.

**Exit:** `axor-lab run` completes a suite with **zero conditions** and produces
an artifact with metrics; a `latency < threshold` regression passes and fails
correctly; all 10 acceptance criteria still green under the governance
capability.

### Phase 3 — Screen API · ~2 weeks

Extend `lab_server/runtime_jobs.py` + `store.py` into the screen API from §5,
keeping "rendered, never computed":

```
GET  /home                              launchpad payload: onboarding step,
                                        quick actions, built-in suites, recent activity
GET  /suites  ·  GET /suites/{id}       catalog + one suite (built-in | mine | community)
POST /suites  ·  PUT /suites/{id}       Builder save
POST /suites/validate                   { ok, errors[] }  (Builder + YAML mode)
POST /playground/trial                  single-trial preview debugger (spec §13)
POST /runs · GET /runs/{id} · SSE /runs/{id}/events
GET  /runs/{id}/report                  Run Report tiles + charts
GET  /runs/{id}/trials/{tid}            Trial detail (timeline, tools, events, metrics)
GET  /evidence · GET /evidence/{id} · POST /evidence
GET  /regressions · GET /regressions/{id} · POST /regressions · POST /regressions/{id}/run
GET  /artifacts · GET /artifacts/{id}
GET  /integrations · GET /runtimes …    (existing runtime endpoints unchanged)
```

**Exit:** every screen in §5 has a named endpoint returning a schema-conforming
payload; contract tests assert the binding.

### Phase 4 — Web app · ~4–6 weeks

New `web/` (React + Vite + TypeScript). Design tokens from §5 as CSS variables,
dark default. Ship order = risk order, each screen integrated against a real
endpoint with no inline fixtures:

1. Home / Launchpad (+ first-run "What do you want to do?" flow)
2. Suite Catalog
3. Playground — one trial *(highest learning-per-effort; validates the whole
   execution path in one screen)*
4. Run progress → Run Report
5. Trial detail
6. EvidenceCase
7. Regression
8. **Suite Builder** — six sections (Agents · Scenarios · Environment & Tools ·
   Execution · Evaluation · Artifact) × three modes (Basic / Advanced / YAML)
   over one manifest. Largest single item; do it last, when the manifest has
   stopped moving.

The nine existing `.jsx` mocks are reference, not a starting codebase — they
predate this spec.

**Exit:** the spec's workflow runs end-to-end in the browser:
Choose Suite → Configure → Preview Trial → Run → Inspect → Create EvidenceCase →
Pin Regression → Export Artifact.

### Phase 5 — Open core packaging · ~1 week

Split per §4.8; re-introduce an entitlement gate for the commercial half only if
Phase 4 actually ships hosted features.

### Phase 6 — Multi-agent · unscheduled

Execute `suite.agents[]` topologies (planner/workers, reviewer pipelines,
negotiations, attacker/defender). Only the suite and the runner's scheduling
change; artifact, evidence and regression formats are already agent-count
agnostic if Phase 1 does its job.

---

## 7. Migration and compatibility

- **Schemas are additive first.** New optional fields, then a version bump only
  where semantics change (`experiment/v1` `conditions` cardinality is the one
  genuine relaxation — relaxations are backward-compatible by construction).
- **Existing bundles stay valid and stay verifiable.** `artifact/v1` reads a
  `bundle/v1` body; content hashes over old bundles are untouched.
- **`.axl` stays the file extension**, its contents grow a `suite` block.
- **Terminology lint widens, not loosens.** ungoverned/governed/compare remain
  the only run-mode words *inside the governance capability*; Suite/Run/Trial/
  Observation/EvidenceCase/Regression/Artifact become the platform vocabulary;
  "bundle" is banned from new user-facing surfaces.
- **Nothing hardened is deleted.** Every r16–r21 correctness fix moves with its
  code into the governance capability.

## 8. Test strategy

- The 10 acceptance criteria stay green throughout; they are re-labelled as the
  governance capability's suite, not the platform's.
- New acceptance criteria for the platform half, each one test file:
  1. a governance-free single-arm suite runs and produces a valid artifact;
  2. per-trial metrics are recorded and survive the artifact round-trip;
  3. a `metric_threshold` regression passes, then fails on a regressed metric;
  4. a generic (non-injection) EvidenceCase renders from a trace;
  5. Basic → YAML → Basic manifest round-trip is lossless (guards §4.5);
  6. every §5 screen endpoint returns a schema-conforming payload.
- Contract validators (`validate.py`, `validate_slice.py`) gate every schema
  change, as today.

## 9. Risks

| Risk | Mitigation |
|---|---|
| **Manifest churn during Builder work** — Basic mode grows fields YAML can't express | §4.5 is a hard rule; round-trip test (8.5) lands in Phase 1, before any Builder code |
| **Governance extraction regresses hardened behaviour** — 21 rounds of correctness work moves packages | Move code, don't rewrite it; the full existing suite must stay green at every commit of Phase 2 |
| **Frontend is the largest single line item and has no prior art in-repo** | Phase 3 fixes the API contract first; screens ship one at a time against real endpoints |
| **Spec is a v0.1 concept draft** — it is thinner than the contracts it's overriding (no schemas, no statistics semantics, no threat model) | Adopt its *structure* literally; keep the repo's field-level rigour as the extension layer, same interpretation rule v0.3 used |
| **Catalog shows six suites, three exist** | Resolved: launch set of three (§6 Phase 2); the Suite Catalog renders an explicit unavailable state for the other three — never a card that runs nothing |

## 10. Decisions taken and questions still open

### Settled

- **Remote endpoint — not adopted (§4.6).** Four inputs, not five; no endpoint
  field in `suite/v1`, no `endpoint` member on `TraceSource`. Deviates from
  spec §5 by owner decision.
- **Built-in suites — three at launch (§6 Phase 2):** Blank, AgentDojo, Budget.
  Prompt Injection / Performance / Reliability trail; the catalog shows them as
  unavailable rather than faking them.

### Still open — none of these block Phase 1

1. **Frontend location** — `web/` inside `axor-lab`, or a separate repo? The
   open-core split (§4.8) argues for separate once the Builder is commercial.
   Decidable at Phase 4.
2. **Statistical defaults for single-arm suites** — descriptive + Wilson only,
   or does a suite get to declare its own aggregation from day one? Decidable
   at Phase 2; Phase 1 only needs the manifest to have somewhere to put it.
3. **Governance capability in the UI** — the boards show no governance screens
   at all. Is governance surfaced only inside a suite's own config, or does it
   keep a top-level nav presence? Decidable at Phase 3.
