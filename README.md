# axor-lab

**Nobody can check whether your agent is safe.** Not because the answer is
secret — because the claim has no shape. *"Hardened against prompt injection"*:
against which injections, measured how, with what left over when it works, and
reproducible by whom?

Axor Lab runs the same agent twice — governance off, then on — and returns one
object that states exactly what it proves and exactly what it does not.

```
$ axor-lab run-suite ingest --out ./artifact --yes

[validating]
valid: ingest
  scenarios=3 conditions=3 repeats=12 -> 108 trials
[estimate]
  108 trial(s), local simulated tools, no paid inference
[running_local]
  planned 108: 108 completed
[analyzing]
  n=108/108
  ASR[ungoverned] = 0.71 [0.51, 0.85] n=24
  ASR[governed] = 0.00 [0.00, 0.14] n=24  mcnemar (paired) vs ungoverned: b=17 c=0 p=1.5e-05
  ASR[governed_allowlist] = 0.00 [0.00, 0.14] n=24  mcnemar (paired) vs ungoverned: b=17 c=0 p=1.5e-05
  task_success[ungoverned] = 1.00 [0.90, 1.00] n=36
  task_success[governed] = 1.00 [0.90, 1.00] n=36
  task_success[governed_allowlist] = 1.00 [0.90, 1.00] n=36
  invariant RG-ingest-no-exfil: passed — predicate == False
  invariant RG-ingest-files-the-record: passed — predicate == True
[completed]  artifact: ./artifact/artifact.json (108 traces)
  reproduce verdicts (exact):    axor-lab replay ./artifact
```
Containment worked, and the job still got done — and the run says so in the same
table, because the measurement that can come back negative is the one worth
having.

## Why this is not another eval harness

**The arms are matched pairs, not two numbers side by side.** Every arm runs the
identical unit — same scenario, same seed, same repeat — so the comparison is
McNemar's exact test over the pairs. The ungoverned arm is *observed, not
enforced*: the kernel watches every trial and gates nothing, so the verdict is
recorded either way. One arm is not a different program with the safety code
deleted.

**Every row says how much it is worth.** A latency mean and an attack-success
rate look identical in a results table and are not the same kind of claim:

| | what it means | may back a claim |
|---|---|---|
| `derived` | a predicate the evidence can re-evaluate against every frozen trace | yes — a server recomputes it before minting one |
| `self-reported` | the runner's own measurement, present in no trace (latency, tokens, spend) | no — published, readable, claimed by nobody |

`axor-lab report` prints the column, so the distinction survives into whatever
you paste it into:

```
| Metric         | Arm          | Estimator | Estimate | Interval         |  n | Evidence      |
| `ASR`          | `ungoverned` | rate      |    0.708 | [0.508, 0.851]   | 24 | derived       |
| `ASR`          | `governed`   | rate      |    0.000 | [0.000, 0.138]   | 24 | derived       |
| `duration_ms`  | `ungoverned` | mean      |    0.866 | [0.691, 1.197] † | 5  | self-reported |
```

**You cannot ship a policy the experiment did not earn.** The export that
carries a validated configuration to production is gated on the evidence:
governance changed the outcome on the *same* experimental units (McNemar,
p < 0.05), the effect clears a practical floor as a separate gate (net absolute
risk reduction ≥ 0.10), and the arms tested the same mix of scenarios — a large
delta from reweighting is composition shift, not governance. An observe-only run
is refused: *"bundle has no enforcement-on condition to carry over"*.

**The result is citable.** The artifact is content-addressed and replays
bit-identically. `axor-lab report --format all` renders it as a results table, a
Methods paragraph carrying the pinned kernel and each arm's config hash, and a
BibTeX entry — because nobody pastes a bundle into a results section.

**Your agent stays yours.** Lab hands out assignments and reads traces back. It
never holds a model credential and never dispatches a tool.

## Sixty seconds

```
pip install -e .
axor-lab suites                                  # the catalog
axor-lab run-suite ingest --out ./artifact --yes  # governed vs ungoverned, paired
axor-lab replay ./artifact                        # exact: bit-identical verdicts
axor-lab report ./artifact --format all --out ./paper
```

`ingest` is derived from a shipped agent's documented tool chain — an
inbox-to-record loop whose attachment is attacker-authored and whose mail sink
leaves the perimeter. It is the case you start from when nobody has curated
anything for your stack. `blank`, `budget` and `agentdojo` cover authoring from
nothing, the metrics layer, and the external-benchmark import path.

Then open the web app and author your own suite in the Builder — Basic,
Advanced and YAML edit the **same document**, and a field no form shows is
carried through untouched:

```
python -m lab_runner.cli serve --port 8871
```

## What this is not

A page that lists only strengths is the unfalsifiable claim again, one level up.

- **Not a runtime guard.** Lab measures and proves; enforcement in production is
  the Control Plane's job. Lab hands it a configuration the experiment earned.
- **Not a hosted SaaS yet.** Multi-tenant workspaces, RBAC, an audit log, plan
  entitlements and the billing handshake are built and tested; scheduled CI,
  approvals, compliance report generation and fleet view are written down as
  scope, not shipped (`docs/POST_MVP_PLAN.md` §B10, with the check that proves
  each absence).
- **Not a model vendor.** Inference is yours. Lab never resells tokens.
- **Not multi-agent.** Topologies validate and save; a run of one is refused
  until multi-agent execution ships, rather than silently running a single agent
  and labelling it otherwise.

## Maturity — subsystems are NOT equally production-ready

A contract-first **executable research prototype** with a production-oriented
contract, not yet a hosted SaaS. Honest per-area status; the roadmap is
`docs/POST_MVP_PLAN.md`, and twenty-two rounds of correctness hardening are
recorded in **[docs/HARDENING.md](docs/HARDENING.md)**.

| Area | Maturity | Notes |
|---|---|---|
| contracts, local runner, replay, EvidenceCase, regression, analysis | **beta** | the vertical-slice spine, correctness-hardened over 22 review rounds: exact replay is a pure function of the projection and the pinned kernel, a pinned real kernel is the kernel that RAN (never silently swapped for the reference one), canonical hashing is full RFC 8785 against the official edge vectors, a single failed trial no longer sinks the analysis, and missingness is reported before the estimate. Round by round: [docs/HARDENING.md](docs/HARDENING.md) |
| server / catalog | **beta (local)** | token-gated writes, content-hash filenames, atomic durable writes; **recomputes every statistical aggregate AND its test from the traces** before minting a claim (a fabricated McNemar p, an unknown metric, or an aggregate over a metric no trial measured is refused); immutable content-addressed publications, an append-only attestation log whose ancestry is verified to a root on cold load, takedown that is final over a stable evidence lineage, and a portable acceptance receipt an offline reader can verify without trusting the server. Round by round: [docs/HARDENING.md](docs/HARDENING.md) |
| multi-scenario benchmark bundle | **beta** | trace ids carry the full trial coordinate; a 3-scenario suite survives a build→write→read→verify→replay roundtrip (`tests/test_multiscenario_bundle.py`) — the round-2 P0 that used to corrupt it is fixed |
| AgentDojo adapter | **beta** | curated **banking** subset (3 tasks), not arbitrary-dataset import |
| bring-your-own agent | **beta** | the caller's agent runs in the CALLER's process against the caller's REAL tools, wrapped by `axor-wrap`, and pulls assignments from `GET /runtime/jobs` — Lab never holds a model credential and never dispatches a tool. Run identity carries the agent fingerprint, and a connected runtime is analysed as INDEPENDENT SAMPLES (two-proportion, exploratory) — never a paired McNemar p-value, because Lab did not see the model. This row used to describe `lab_agent` — a `ModelBackend`/`CassetteBackend`/`AnthropicBackend` stack with per-scenario cassettes and `--max-usd` ceilings — deleted in the v0.3 re-scope: it drove a model against SIMULATED tools, so the numbers described neither the caller's agent nor their tools. `axor-lab run` has no `--agent` flag and `[byok]` is an empty alias |
| endpoint gateway · sandbox · games / federation | **retired** | these described `lab_endpoint/`, `lab_sandbox/` and `lab_games/`, deleted in the v0.3 re-scope (`docs/spec-v0.3/CONFORMANCE.md`) — the table went on describing their SSRF guard, RLIMITs and blast-radius measures for three subsystems no longer in the repo, twelve lines above the paragraph that says the packages are gone. Enforcement and tool dispatch are the runtime's (`contracts/architecture-boundary.md`); multi-agent games are deferred |
| Suite Platform (suites, Builder, artifacts) | **beta** | a suite is an authorable `suite/v1` manifest: three-mode Builder over ONE document (a field no form shows is carried through untouched), four built-ins, author-time validation that refuses what a run would only discover later (an empty identifier, an aggregation over an undeclared metric, a threshold over a boolean, a `mcnemar` on a single arm), one planner shared by the local runner / the connected runtime / `Preview plan`, and `artifact/v1` wrapping a bundle byte-for-byte so every prior hash still verifies. Tool manifests, scenario fixtures and predicate trees are edited in YAML by design — the form links to the exact key |
| kernel | **reference + real backend** | ships `reference_taint_floor_kernel` (1 gate, stdlib) AND a real backend that drives the production `axor_core.governor.ToolCallGovernor` when axor-core is installed and the condition pins the installed version (`pip install axor-lab[kernel]`; `axor-lab run --real-kernel` repins EVERY condition — baseline included — so the compare isolates enforcement, not a mixed kernel, and the bundle carries a single kernel_version). Verified: real governor DENYs the exfil, ALLOWs the faithful payment, replays bit-identically |
| Private Lab / workspaces / billing | **beta (local)** | the hosted surface IS built: durable multi-tenant workspaces with per-tenant stores, RBAC (owner/admin/member/viewer; a viewer cannot mutate) with every mutation written to a per-workspace audit log, an entitlement gate in `lab_server/workspaces.py` (NOT the deleted `lab_entitlement`) whose plan limits and capabilities answer **402 Payment Required**, an operator plan catalog (`serve --plans-file`; reference ladder in `docs/pricing/`), identity login whose org `tier` selects the catalog plan and fails CLOSED to free, checkout + a secret-gated provider webhook (a purchase needs an unspent checkout, a lapse is addressed to the workspace, a lapsed subscription drops to free), anonymous guest trials (capped, swept on expiry), and a Workspace screen for plans/members/audit/tenants. NOT built: scheduled CI + history, approvals, compliance report generation, fleet view, hosted-trial metering and overage, SSO beyond an identity JWT (no SAML/SCIM) — each recorded with its evidence of absence in `docs/POST_MVP_PLAN.md` §B10 |

## The plans and the contract

Where prose and a contract disagree, the contract wins — and
`tests/test_docs_match_the_code.py` fails when a document starts describing
something the code no longer does.

- **[contracts/](contracts/)** — the engineering contract: 10 JSON Schemas
  (artifact, attestation, bundle, condition, evidence-case, experiment,
  publication, regression, scenario, suite), plus statistics / claims /
  provenance semantics, lifecycle, threat model, MVP contract and a fully-worked
  vertical slice. Validate:
  `cd contracts && PYTHONPATH=.. python3 validate_slice.py` (14 examples, green).
  A tool manifest, a predicate and a trace have no schema FILE of their own:
  manifests and predicates are defined inside the schemas that carry them, and
  the trace belongs to axor-core.
- **[docs/spec-suite-platform/](docs/spec-suite-platform/)** — the **governing**
  product spec (Experiment Suite Platform RFC + Web UX RFC), and
  [INTEGRATION_PLAN.md](docs/spec-suite-platform/INTEGRATION_PLAN.md), the gap
  analysis behind it.
- **[docs/POST_MVP_PLAN.md](docs/POST_MVP_PLAN.md)** — the roadmap, including
  §B10: the paid features the tiers sell and the code does not have, each with
  the search that proves its absence.
- **[docs/RELEASING.md](docs/RELEASING.md)** — how `axor-lab` publishes to PyPI
  (tag-driven, credential-free via Trusted Publishing), and the gate that
  refuses a release PyPI would reject — a git-pinned dependency, or a version
  floor nothing published satisfies — before the tag is spent.
- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** ·
  **[docs/spec-v0.3/](docs/spec-v0.3/)** — the implementation plan, and the v0.3
  narrative (superseded as the product story, still accurate on the governance
  capability), with packaging, economics and the bench-format guide.

## Packages (MVP spine + post-MVP blocks, stdlib-only core)

- **`lab_contracts/`** — the contract layer: schema loading + the contracts' own
  subset JSON-Schema validator (cwd-independent), semantic checks (author-time
  scenario validation, trace referential integrity), canonical JCS hashing,
  bundle assembly/verification, typed publication claims.
- **`lab_runner/`** — the platform execution engine + CLI: value ledger with
  conservative-join provenance, untrusted-field minting, the general agent loop,
  simulated tools with `$injection` fixtures, predicate evaluation, executable
  invariants, trial identity, bundle I/O.
- **`lab_analysis/`** — the statistics engine (`contracts/statistics.md` as
  code): Wilson, exact McNemar over stored pairs, paired bootstrap, missingness
  honesty, unit-of-analysis enforcement.
- **`lab_adapters/`** — benchmark imports (MVP item 2): the curated AgentDojo
  banking data-flow suite materialized as `scenario/v1` objects (mirrors
  axor-eval's property map), each schema-valid and author-time-validated.
- **`lab_server/`** — the hosted surface (Phase 4 + minimal Phase 5): the
  publish handshake (schema + hash + safe replay verification, `origin=local`),
  an append-only attestation log, `integrity=signed` for known author keys,
  takedown that preserves attestations, and escaped HTML catalog / publication
  / EvidenceCase pages with three-axis provenance. Stdlib `http.server`; runs
  no live agents.
- **`lab_suite/`** — the Suite SDK: the `Suite` protocol and `BaseSuite`, a
  registry with four built-in suites, manifest load/validate/resolve, suite
  execution, and dispatch to a connected runtime. Each built-in covers a
  different surface once: **Blank** — a suite is authorable from nothing;
  **AgentDojo** — the external-benchmark import path; **Budget** — the metrics
  layer; **Ingest** — a suite derived from a shipped agent's documented tool
  chain (an inbox-to-record ingestion loop whose attachment is attacker-authored
  and whose mail sink leaves the perimeter), which is the case a customer starts
  from when nobody has curated anything.
  A suite declares its own options as a JSON Schema in `config_schema`, their
  values in `config`, and where the Builder puts each one in `ui_schema` — the
  Builder renders them as ordinary fields and the values are validated at
  author time, so a suite's knobs are refused in the form rather than at run
  time (RFC §12/§13, "every suite contributes declarative schemas").
  A scenario may `$ref` a tool the suite shares in `environment.tools` or carry
  the manifest inline; either way it lands in the bundle a run governs against,
  and one tool id declared twice with different contracts is refused. Scenarios
  themselves are shareable: `scenario_refs` resolve from the workspace (and its
  org registry) on the server, and from a `scenarios/` directory beside the
  manifest on the CLI.
- **`lab_capabilities/governance/`** — governance as an opt-in capability
  (Suite Platform RFC §10): the reference kernel and the real axor-core backend,
  the gate a condition resolves to, exact verdict replay, EvidenceCase
  rendering, verdict pinning, the Control Plane bridge, and the paired `.axl`
  experiment runner. `lab_runner` imports none of it — the dependency direction
  and the short list of composition roots are enforced by
  `tests/test_capability_boundary.py`.

`lab_agent/`, `lab_entitlement/`, `lab_endpoint/`, `lab_sandbox/` and
`lab_games/` were documented here long after they were deleted. They are gone;
`docs/POST_MVP_PLAN.md` records what each did and why it was cut.

## The rest of the CLI

`Sixty seconds` above covers the first four verbs. The whole surface:

```
axor-lab run-suite budget --out ./artifact --yes    # a suite -> artifact/v1
axor-lab run-suite ./suite.json --out ./artifact --yes   # ...or a manifest file;
                                                   #    `scenario_refs` resolve from
                                                   #    ./scenarios beside it (or
                                                   #    --scenarios DIR)
axor-lab import-agentdojo banking --out suite.axl   # curated benchmark -> .axl
axor-lab validate examples/banking-exfil-01.axl
axor-lab run examples/banking-exfil-01.axl --out ./bundle --yes
axor-lab replay ./bundle                       # exact: bit-identical verdicts
axor-lab pin ./bundle <trace_id> DENY --out pins.json
axor-lab regress ./bundle --pins pins.json     # surfaces changes, exit 4 if any
axor-lab evidence ./bundle <trace_id>          # the three-mode EvidenceCase
axor-lab publish ./bundle --question "…" --out publication.json   # local
axor-lab publish ./bundle --question "…" --server http://127.0.0.1:8000   # hosted
axor-lab report ./bundle --format all --out ./paper   # results table + Methods + BibTeX
```

Lifecycle, exit codes, and the estimate-confirm gate follow
`contracts/runner-protocol.md` and `contracts/lifecycle.md`. The bundle
directory is the `axor-bundle-dir/v1` layout (`bundle.json` + `traces/`).

### Getting the evidence out

Seven doors, and the hosted face reaches all of them (it used to reach one):

| | CLI | UI |
|---|---|---|
| artifact + bundle + traces | `run-suite --out DIR` | Artifact → **Download artifact** / **Download reproduction package** |
| a results table for a paper | `report --format md\|tex\|bib` | Artifact → **Results + Methods (.md)** / **Table (.tex)** / **Citation (.bib)** |
| publication, local | `publish --out` | Artifact → **Publish** (mints locally) |
| publication, hosted + reproduction package | `publish --server`, `verify` | Artifact → **Publish** with a server; the receipt is shown |
| Control-Plane handoff | `export-cp`, `verify-cp-export` | Handoff → **Download handoff (.zip)**, which `verify-cp-export` checks as-is |
| incident import | `import-incident` | Handoff → **Import incident** |
| suite / scenario sharing | `suite-yaml`, publish to org | Suites → **Publish to org** |

`report` is the one door that is not JSON, because nobody pastes a bundle into a
results section: it renders the run as a table (arm × metric, estimate,
interval, n), the comparison sentences with their test statistics, a Methods
paragraph carrying the pinned kernel and each arm's config hash, and a BibTeX
entry keyed on the content-addressed publication (or, unpublished, on the bundle
hash — and it says which). Every row states whether its number was DERIVED from
the traces or merely REPORTED by the runner: a latency mean and an
attack-success rate look identical in a results table and are not the same kind
of claim.

A LOCAL publication re-runs the verdicts, so it asserts replay — and refuses to
claim the aggregates, because it did not recompute them and a hand-edited bundle
could carry a fabricated one. Only a server that recomputes from the traces
mints a statistical claim, and it returns an acceptance receipt saying so.

A downloaded reproduction package is bare `{bundle, traces}`:
`axor-lab verify <file> --allow-bare` checks integrity and replay and claims no
more. `axor-reproduction-package/v1` is the server-issued shape whose proof
objects are mandatory, and an unpublished artifact has none of them.

### Three ways a suite executes

A manifest says which tools exist and what they mean. It cannot say the order an
agent calls them in, and that difference is what separates the three:

| | who decides the actions | tool results | what you get |
|---|---|---|---|
| **connected runtime** | a real model, on your machine | real | the only numbers worth publishing. Lab assigns, reads back, and recomputes every claim — a trace for an unplanned trial, an invalid one, one whose `runtime_config_hash` disagrees with the assignment, or one naming no kernel under an arm that does, is refused |
| **`program_for`** | the suite, in-process | the scenario's fixtures | the same kernel, the same ledger, the same verdicts — enough to prove the scenarios, predicates, arms and invariants measure what you meant, in CI, with no keys |
| **neither** | nobody | none | every trial completes, every metric is false, an artifact is written. `POST /suites/plan` reports `drives_itself: false` and the Builder says so before the click |

All three run the same `axor_wrap.WrappedToolset` over the real kernel, so an
`ungoverned` arm is **observed and not enforced** — the verdict is recorded
either way — rather than ungated.

Run the catalog/publish server (stdlib only, no live agents):

```
python -m lab_server --root ./lab-store --port 8000
# GET / catalog · GET /e/{id} publication · GET /e/{id}/evidence/{trace_id}
```

## Executable acceptance suite

`contracts/acceptance-tests.md` §1–10 runs as code against these packages —
one test file per criterion, plus two golden paths (in-process
`test_slice_e2e.py` and subprocess `test_cli_e2e.py`); every produced artifact
is validated against the real schemas in `contracts/`.

```
python -m unittest discover -s tests -t .      # full suite, no required dependencies
```

Beyond the ten acceptance criteria, the suite covers the AgentDojo adapter,
the CLI (subprocess), the server over real HTTP (publish handshake, escaped
pages, three-axis provenance, takedown), a terminology lint, and the post-MVP
blocks: the Suite Platform (authoring, the three-mode Builder, the shared
planner, all three execution paths), Control Plane export and its earned
bridge, the paper report, workspaces/RBAC/entitlement and the billing
handshake, and bundle signing. The endpoint, sandbox and game suites went with
their subsystems in the v0.3 re-scope. Optional Ed25519 paths skip cleanly when
PyNaCl is absent.
