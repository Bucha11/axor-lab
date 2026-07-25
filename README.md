# axor-lab

Axor Lab — standalone research surface for the Axor governance stack: bring an agent, run attack scenarios ungoverned/governed on simulated tools, investigate single trials (EvidenceCase), replay governance verdicts exactly, and publish reproducible bundles.

- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — the production-ready implementation plan (phases, reuse map, milestones, definition of done). The MVP spine is implemented; see its status block.
- **[docs/POST_MVP_PLAN.md](docs/POST_MVP_PLAN.md)** — the post-MVP plan: BYOK model adapter, Control Plane export, full web app, production hardening, then the Later tier and the commercial track. Written before v0.3, so its Later tier still plans the gateway, sandbox, games and black-box evaluation that v0.3 retired — see "Retired in v0.3" below.
- **[contracts/](contracts/)** — the engineering contract: 9 JSON Schemas, statistics/claims/provenance semantics, lifecycle, threat model, MVP contract, vertical slice, acceptance tests. Where prose and a contract disagree, the contract wins. Validate: `cd contracts && python3 validate.py && python3 validate_slice.py`.
- **[docs/design/](docs/design/)** — product narrative (spec-lab v0.3), packaging/economics, bench format guide, UI mocks.

## Maturity — subsystems are NOT equally production-ready

Axor Lab is a contract-first **executable research prototype** with a
production-oriented contract, not yet a hosted SaaS. Honest per-area status
(see `docs/POST_MVP_PLAN.md` for the roadmap):

| Area | Maturity | Notes |
|---|---|---|
| contracts, local runner, replay, EvidenceCase, regression, analysis | **beta** | the vertical-slice spine; correctness-hardened over multiple review rounds (typed replay values, replay rejects malformed traces, predicate completion fail-closed, evidence-graph verifier now **resolves every trial's scenario/condition and every trace tool's manifest in-bundle**, **sensitive labels propagate through model output**, **a redacted secret keeps a runtime-only value so the real kernel still sees it without serializing it**, **per-driving-arg allowlist supersession**, **the simulator honors its manifest contract**, **a single failed trial no longer sinks the analysis — completed-only outcomes, missingness reported first**, **a regression pin records the whole ordered verdict sequence and each pin replays under its OWN scenario's inputs** — no false regression on a multi-call/multi-scenario bundle, **EvidenceCase resolves the real governor via `resolve_kernel` and correlates the DENY to its intent by call_id**, **local `publish` proves replay only — it never mints a statistical claim over self-reported aggregates — and content-addresses the publication by its whole body**, **regression honors the replay STATUS so a malformed trace is never a false match**, **the value-ledger is unambiguous — unique value_ids, canonical_value_hash consistency, strictly-ordered seq**, **the canonical hash is full RFC 8785 — floats in ECMAScript form, keys sorted by UTF-16 code unit, non-string keys and unsafe integers and lone surrogates rejected, pinned against the official edge vectors**, **a bundle overwrite can't destroy the prior bundle on a crash**, **EvidenceCase separates a self-reported `explicit_flow_tracked` claim from a verified one**, **a fail-closed DENY is representable as valid evidence — a null `driving_value_id` with a typed `driving_unresolved` reason, not a fake ledger id that would fail validation**, **replay is honest about capability — a REDACTED sensitive value a decision turned on yields `redacted_input_unavailable` (never a false match/mismatch over a hash sentinel), the fail-closed reason is part of the replay-comparable core, and an EvidenceCase claims `exactly_replayable` only for a replayable status**, and **the offline `axor-lab verify` is a strict state machine — a `signed` receipt with no verifiable signature exits UNVERIFIED(5), a tampered one exits 1, integrity is never confused with authenticity**, and **(round 16) a pinned real kernel is the kernel that RAN or the trace is `unsupported_kernel` — `axor-core@X` is never silently replaced by the reference kernel; the CP earned bridge and every hosted statistic are RECOMPUTED from the traces, never trusted from an uploaded aggregate; McNemar's power is the discordant n; `executable_config_hash` binds the whole compiled governor config including untrusted-field taint; and an ungoverned trace's arg-independent ALLOW replays even when a bound value is redacted**, and **(round 17) ONE kernel resolver serves every surface — CLI regress / EvidenceCase / incident import resolve each trace's own scenario inputs via `resolve_kernel_for_trace`; the CP bridge requires the COMPLETE trace set (a cherry-picked subset raises) and emits an immutable `cp_bridge_analysis/v1` receipt; and the carry-over key is honestly `parametric_policy_hash` (symbolic `$inputs`), distinct from the concrete per-scenario `runtime_config_hash`**, and **(round 18) `regress --kernel X` tests the CANDIDATE kernel X — `resolve_candidate_kernel_for_trace` takes the policy from the candidate condition and the version from `--kernel` while keeping each trace's own scenario inputs, so a counterfactual regression never silently re-runs the trace's original recorded kernel (that stays `resolve_recorded_kernel_for_trace` for exact replay); replay narrows its kernel-resolution guard to `UnknownKernelError` so an internal bug propagates instead of masquerading as `unsupported_kernel`; and the CP handoff verifies the evidence graph (`verify_bundle`) and proves the per-scenario `runtime_config_hash` it recommends was RECORDED at build time (`config_provenance`), not synthesized at export**, and **(round 19) the earned bridge separates governance from COMPOSITION shift — it compares the SAME experimental units across arms (coordinate intersection + McNemar for a matched design; per-scenario balance for independent samples) and rejects a large ASR delta whose two arms merely tested a different mix of scenarios; `runtime_config_hash` is recorded ON the trial at execution, provenance is mandatory + nested `{scenario:{condition:hash}}` for an evidence export, which emits a hash ONLY for a scenario that actually ran; and the CP export directory is self-contained + independently recomputable via `axor-lab verify-cp-export` (a doctored deploy config no longer recomputes)**, and **(round 20) a matched bridge must clear a PRACTICAL-significance floor (net absolute risk reduction (b−c)/completed ≥ 0.10) as a separate gate AFTER McNemar's p<0.05 — a statistically-real but 2% effect no longer earns a production config — and an independent bridge requires EXACT per-scenario arm balance so an inverse per-scenario mix can't earn on pure reweighting; the completed-trial schema now REQUIRES `runtime_config_hash`+`config_compiler_version`, and `config_provenance` marks `recorded_at_execution` vs `reconstructed_legacy` (the evidence export refuses the reconstructed one) and raises on a divergent per-(scenario, condition) hash; and `verify-cp-export` checks INTEGRITY (a signed full-file manifest over the WHOLE directory + no unlisted files), AUTHENTICITY (a signed export with no key → UNVERIFIED) and DERIVABILITY, with `--overwrite` clearing stale files first**, and **(round 21) every experimental unit is globally unique — the runner stamps an `execution_id` on each trial, `verify_bundle` rejects a duplicate `(execution, scenario, condition, seed, repeat)` coordinate, and the CP bridge + hosted recompute RAISE on a duplicate rather than let trial ARRAY ORDER pick the outcome (no last-write-wins statistics); the comparison design is a run-recorded, content-hashed `environment.experiment_design` block bound to the agent's actual determinism — the bridge reads it from THERE (never an uploader aggregate), never defaults to matched, and requires a matched design to be deterministic; `config_provenance` is DERIVED from the trials (build_bundle + verify_bundle reject a caller-asserted map), each completed trial declares `runtime_provenance` (recorded_at_execution / reconstructed_incident / reconstructed_legacy) so an imported incident is honest, and the CP handoff REQUIRES + carries the `resolved_kernel_fingerprint`, refusing a behaviour-modified backend; and the independent bridge requires equal PLANNED allocation + missingness per scenario (not just completed counts), with both receipts naming the complete-case estimand and carrying a per-scenario missingness matrix**) |
| multi-scenario benchmark bundle | **beta** | trace ids carry the full trial coordinate; a 3-scenario suite survives a build→write→read→verify→replay roundtrip (`tests/test_multiscenario_bundle.py`) — the round-2 P0 that used to corrupt it is fixed |
| AgentDojo adapter | **beta** | curated **banking** subset (3 tasks), not arbitrary-dataset import |
| server / catalog | **beta (local)** | token-gated writes, content-hash filenames, atomic writes, **recomputes every statistical aggregate AND its test from the traces** (rejects a fabricated McNemar/two-proportion p or an unknown metric; the recomputed marginal matches the runner's per-condition count so an honest bundle is not falsely rejected at missingness), **hides `private` publications on every read route** (HTML, JSON, EvidenceCase), **content-addresses each publication by its whole body** so it is immutable (re-publish is idempotent-or-distinct; a disk-edited record is dropped on load), **re-runs the full publish handshake (replay + recompute + re-mint) on restart so a hand-assembled publication never loads unverified**, **counts only cryptographically verified reproductions in the public badge** (unsigned self-reports shown separately; on load each attestation is re-verified, bound to its publication, and schema-checked), **re-earns an `integrity: signed` badge on load only from a persisted author-signature receipt** (a forged signed badge degrades to hash_verified and is dropped), builds each DENY claim from the **recorded decision** correlated by call_id, and **isolates each publication on startup so one corrupt file can't sink the whole catalog**, **refuses to resurrect an admin-taken-down publication via a write-token re-publish** (tombstone wins, 409), **serves a downloadable reproduction package with a PORTABLE verification receipt** (`GET /api/publications/{id}/bundle` returns bundle+traces+receipt; `axor-lab verify` checks content hashes, replay, and the receipt's signed_ref/signature OFFLINE — no server trusted — and the publish response carries an acceptance receipt of what the server verified), **makes an admin takedown final over a STABLE evidence lineage** (an `evidence_lineage_ref` invariant to bundle_id/created/packaging — takedown retires every sibling on that lineage, blocks any re-publish under altered metadata OR repackaged bytes, guards every read, and a two-pass cold load collects all lineage tombstones before loading publications), **issues a deterministic, content-addressed, optionally Ed25519-SIGNED acceptance receipt** (persisted, returned on publish, and served in the download package alongside the publication body so an offline reader can verify the claims, not just the bytes), **reports completed/planned + condition-imbalanced missingness in every statistical claim**, **recomputes the WHOLE test object (the two_proportion interval included) and rejects any test field it does not itself recompute**, and **maps a malformed request body to a clean 4xx, never a 500**, and **(round 16) verifies the ENTIRE downloaded package before `verify` exits 0 — a stripped receipt or an edited publication/acceptance fails; lineage takedown is durable, crash-safe, and array-order-independent; the exact recomputed test shape is required and an inconclusive uploaded test is refused; and the persisted acceptance is RESTORED on load (never re-minted under a rotated key)**, and **(round 17) a downloaded package cannot be silently downgraded — `verify` requires a versioned envelope (`--allow-bare` to opt out) and an UNSIGNED server acceptance reads as UNVERIFIED, not a pass; a historical acceptance's signature is verified against a server keyring (a forgery is quarantined, a rotated-out key kept opaque, never re-issued); a mixed-kernel publication page renders; the acceptance report only claims checks that ran; and the durable tombstone fsyncs the file bytes before the rename**, and **(round 18) a `signed` publication cannot be proof-downgraded — `verify` requires the author receipt's integrity to equal the publication's and treats a signed publication with no verifying key as UNVERIFIED; a damaged/forged persisted acceptance under a known key is QUARANTINED and re-attested with a distinct, timestamped `reacceptance/v1` linking to the invalid original (never silently re-minted as a clean record); and `_write_atomic` loops over short `os.write`s so a large body is never truncated on disk**, and **(round 19) a MISSING or malformed acceptance for a loaded publication is no longer re-minted clean — it is a forensic event re-attested via reacceptance/v1; the append-only `acceptance-history/` preserves EVERY superseded record so repeated corruption keeps a resolvable chain; the reproduction package carries `acceptance_history` and `verify` requires a reacceptance's `previous_ref` to resolve to a record in it; and `_write_atomic` raises on a zero-byte write instead of spinning**, and **(round 20) the server itself re-resolves that chain on COLD LOAD — a persisted reacceptance/v1 whose `previous_ref` no longer resolves to a hash-matching record in the append-only history (deleted or tampered) is a forensic broken-chain event: the record is archived and re-attested with a linked reacceptance that DOES resolve (converging, not re-stamped every reload), so the server never serves what the offline verifier would reject**, and **(round 21) the acceptance ancestry is verified RECURSIVELY to a root — a deep (grandparent) break, a cycle, or an over-deep chain is rejected, not just the immediate hop; a broken chain is repaired by RE-ROOTING at a forensic marker so it converges; an unknown-key reacceptance still has its structure + history-chain validated (the unknown key gates only the signature step); and `acceptance_history` omits hash-invalid entries so a corrupt record never rides along in the reproduction package**; not yet a public SaaS (no OAuth/DB/object-store) |
| BYOK agent | **beta** | wrapped runtime is banking-slice-shaped; run identity carries the agent fingerprint; **live runs are analyzed as independent samples (two-proportion, exploratory) — never a paired McNemar p-value**; **a per-scenario cassette keys on the scenario name (not task text), so scenarios can't silently share a transcript**; **`--max-usd/--max-input-tokens/--max-output-tokens` are a HARD run-wide ceiling checked BEFORE the first trial and BEFORE every provider call inside a trial's loop (not just between trials, so one trial can't overshoot by its whole fan-out of calls); a ≤ 0 limit is rejected, the remaining output budget caps the next call's `max_tokens`, and actual usage+spend is recorded in the bundle**; **a USD-only budget reserves output tokens and counts the tool schema in its pre-spend projection**; **the trial plan is block-balanced (scenario→repeat→condition) so a cost stop keeps matched pairs, missingness is condition-aware, and a cost-stopped run is labelled `[completed_partial]`/`[stopped_cost_ceiling]` — never `[completed]` — with planned/completed/failed/excluded reported separately**; **a USD-only budget is a HARD ceiling (the next call's max_tokens is capped at what the remaining USD can buy, not just estimated), and condition order is counterbalanced across blocks with the execution order recorded on each trial**; generic multi-tool loop is roadmap |
| kernel | **reference + real backend** | ships `reference_taint_floor_kernel` (1 gate, stdlib) AND a real backend that drives the production `axor_core.governor.ToolCallGovernor` when axor-core is installed and the condition pins the installed version (`pip install axor-lab[kernel]`; `axor-lab run --real-kernel` repins EVERY condition — baseline included — so the compare isolates enforcement, not a mixed kernel, and the bundle carries a single kernel_version). Verified: real governor DENYs the exfil, ALLOWs the faithful payment, replays bit-identically |
| Private Lab / workspaces / billing | **design-only** | hosted workspace surface not built; the entitlement subsystem was retired in v0.3 (see below) |

## Packages (MVP spine + post-MVP blocks, stdlib-only core)

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
- **`lab_adapters/`** — benchmark imports (MVP item 2): the curated AgentDojo
  banking data-flow suite materialized as `scenario/v1` objects (mirrors
  axor-eval's property map), each schema-valid and author-time-validated.
- **`lab_server/`** — the hosted surface (Phase 4 + minimal Phase 5): the
  publish handshake (schema + hash + safe replay verification, `origin=local`),
  an append-only attestation log, `integrity=signed` for known author keys,
  takedown that preserves attestations, and escaped HTML catalog / publication
  / EvidenceCase pages with three-axis provenance. Stdlib `http.server`; runs
  no live agents.
- **`lab_agent/`** (B1) — BYOK model-backed agent: `ModelBackend` protocol,
  `CassetteBackend` (offline) + `AnthropicBackend`, a `WrappedModelAgent`
  driving the loop through the ledger; cost estimate.
### Retired in v0.3 — deliberately absent

`lab_endpoint/` (gateway + black-box eval), `lab_sandbox/`, `lab_games/` and
`lab_entitlement/` were removed by *v0.3 Phase 1: retire out-of-scope
subsystems*. They are not deferred work and not missing files.

The gateway and black-box evaluation went for one reason: each would make Lab
dispatch tools, hold tool credentials, or stand as a synchronous enforcement
boundary — runtime territory, not Lab's. Instrumented endpoints are served by the
shared runtime adapter instead ("connect runtime"), and `black_box` is absent
from `trace.producer.mode` by design: with no ledger to build there is no
conformant trace to produce. See `contracts/mvp-contract.md`,
`contracts/ui-backend-contract.md` and `contracts/provenance-semantics.md`.

## CLI quickstart (`axor-lab`, or `python -m lab_runner`)

```
axor-lab import-agentdojo banking --out suite.axl   # curated benchmark -> .axl
axor-lab validate examples/banking-exfil-01.axl
axor-lab run examples/banking-exfil-01.axl --out ./bundle --yes
axor-lab replay ./bundle                       # exact: bit-identical verdicts
axor-lab pin ./bundle <trace_id> DENY --out pins.json
axor-lab regress ./bundle --pins pins.json     # surfaces changes, exit 4 if any
axor-lab evidence ./bundle <trace_id>          # the three-mode EvidenceCase
axor-lab publish ./bundle --question "…" --out publication.json   # local
axor-lab publish ./bundle --question "…" --server http://127.0.0.1:8000   # hosted
```

Lifecycle, exit codes, and the estimate-confirm gate follow
`contracts/runner-protocol.md` and `contracts/lifecycle.md`. The bundle
directory is the `axor-bundle-dir/v1` layout (`bundle.json` + `traces/`).

Run the server (stdlib only, no live agents). One process, and one command brings
up the **whole** product — the catalog on `--port` (8000) and the run API on
`--runtime-port` (8010). The run API used to default to off, which meant this
command served the catalog and left the builder, runs, results and agent ingest
dark against a server that looked healthy; `--no-runtime-api` is the explicit
opt-out.

```
python -m lab_server --root ./lab-store
# :8000  GET / catalog · GET /e/{id} publication · GET /e/{id}/evidence/{trace_id}
# :8010  runtimes · experiments/plan · runs · runs/local
```

## No-CLI path: a first result from the UI

The bundled example is fully offline — `scripted@0.6` is a deterministic stand-in
for the model layer, the kernel is the stdlib reference kernel, and the tools are
simulated. So the server can just run it:

```
GET  /catalog               # the real suites/scenarios a builder may offer
POST /experiments/compose   # {suite, conditions, repeats} → a validated .axl
POST /runs/local            # {} → runs examples/banking-exfil-01.axl
POST /runs/local            # {"compose": {...}} → composes a selection and runs it
POST /runs/local            # {"experiment": {...}} → runs an .axl you pass in
POST /replay                # {bundle, traces} → reproduce recorded verdicts
GET  /runs/{id}/traces      # which trace to look at, and which were denied
GET  /runs/{id}/evidence/{trace_id}   # EvidenceCase — no publishing required
POST /runs/{id}/pin         # pin one of YOUR OWN traces as a regression case
POST /runs/{id}/cp-export   # the Lab → Control Plane handoff config
```

The web is the primary surface; the CLI is what you reach for when the browser
must not do the job. Four capabilities used to be CLI-only for no good reason and
are not any more: an EvidenceCase over an unpublished run (investigation is what
decides whether a run is worth publishing, so it cannot require publishing
first), pinning a trace from your own run (pinning used to need an imported
production incident to exist), the Control Plane handoff (the bridge into the
paid contour sat behind a terminal), and running a hand-written `.axl`.

### Verification moved to the browser, not to the server

`verify` was the one capability that could not become a server endpoint: its
whole value is that no server is trusted, so "the server checked itself" would
have deleted the guarantee rather than moved it. It runs in the **browser**
instead (`#/verify`) — the reader's machine, over bytes the reader already holds.

Checked locally, trusting nothing: the versioned envelope (a server package
stripped of its envelope *and* every proof cannot pass as an honest bare file),
every content hash, each trial's binding to its trace body, the publication's
commitment to this bundle, the receipt's binding to it, and — with an author key
— the Ed25519 signature. An unsigned receipt reads **skipped**, never pass:
integrity is not authenticity.

Replay is the exception and is labelled as one. Recomputing verdicts needs the
kernel, so `POST /replay` does it and the panel says plainly that this is a check
the *server* performed. For a verdict that trusts nobody, run `axor-lab verify`.

The browser canonicalizer is pinned byte-for-byte against
`contracts/canonicalization-vectors.json` — the same vectors the Python one is
pinned against — because a hash the browser computes differently is worse than no
check at all (`tests/test_browser_canonicalization.py`). The verifier itself is
driven over a real published package plus five specific attacks
(`tests/test_browser_verify.py`), including the case people forget: a genuine
package must still pass.

### Live-model runs: the only measurement of a model

The scripted agent's attack rate is a **parameter** — `scripted@0.6` follows the
injection about 60% of the time — so an ungoverned ASR from it is a dial, not a
finding. What it does prove is real and replayable: the kernel denies a tainted
egress sink. But nothing about any model.

`POST /runs/live/plan` → `POST /runs/live` is the run where the ungoverned arm
becomes a measurement, and the only way to ask which of several models actually
gets exfiltrated. `/runs/local` still refuses to spend anything; this is a
separate surface carrying the CLI's safeguards rather than waiving them:

- **Two steps.** Pricing needs no key — see the cost before handing anything over
  — and returns a confirm token derived from the exact experiment, model and
  budget. A token from a 6-trial estimate will not execute 120 trials, raise the
  ceiling, or switch models.
- **A budget is required.** The CLI may run unbounded because a human is watching
  the terminal; nothing in an HTTP request plays that role.
- **The key is yours.** Per request, used, never stored, logged or echoed.
- **Token ceilings are hard; `max_usd` is best-effort** — it comes from an
  illustrative price table, not your provider's billing, so it stops the run
  *near* the figure. The UI says this next to the field, not in a footnote.
- **No fake pairing.** A live model samples each condition independently, so the
  result is a two-proportion comparison and never a paired McNemar p-value —
  stated at plan time, before the money moves.
- **A run where every trial failed is a 502, not a completed run with no
  traces.** A rejected key used to land as an empty success.

### Signing the CP export: vault custody, not a key in a form

The last CLI-only capability, and the one where "move it to the server" was wrong
twice over: a Lab server must not hold your signing key, and a browser is a worse
place for it than a terminal — a private key that vouches for a production config
does not belong in a web form, however convenient.

The Control Plane already solved this class of problem. Its signing vault SIGNS
and never surrenders: `sign(operator, key_id, payload)` returns a signature over
bytes you submit, the private half never leaves, and every request is authorised
against the key's operator list and audited. An export signature says "this named
author vouches for this production config" — an operator action, which CP already
treats this way.

So `POST /runs/{id}/cp-export` with `tree: true` assembles the whole export
directory using the CLI's own code (the two cannot drift) and hands the manifest's
canonical bytes to the vault. Configure it with `--cp-url` / `--cp-signing-token`;
the URL and token come from **server** config, never from the request — a request
that could name the URL would point the server anywhere it liked.

The load-bearing property is a byte one, and it has a test: what Lab hands the
vault is EXACTLY what `lab_contracts.signing.sign_bundle` would sign locally
(manifest minus `signature`, canonicalized, author included). If those differed, a
vault signature would verify nowhere.

No vault configured, unreachable, or refusing → the tree comes back **UNSIGNED and
labelled unsigned**, or the refusal is surfaced. An export that quietly claims an
authority it does not have is the failure this subsystem exists to prevent.

It lands as an ordinary COMPLETED run, so results, bundle assembly and publish all
work over it unchanged — in the UI that is **Run the example → results → publish**,
no terminal at any step. The run is byte-identical to `axor-lab run` over the same
file (same `bundle_id`, trials, traces, aggregates and environment — pinned by
`tests/test_local_run.py`), and it keeps `recorded_at_execution` config provenance
rather than the weaker reconstructed kind, so it is publishable evidence.

Two things it refuses, on purpose: an experiment needing a **live model** (409 — a
browser must not be able to spend money; the cost ceiling and estimate-confirm gate
live in the CLI) and a suite over its **trial ceiling** (413).

`POST /replay` is the other half — reproducing someone else's run without an agent
at all. It returns a named `outcome` rather than a bare boolean, because
`bit_identical: false` conflates two different answers: **`diverged`** (the
recomputed verdicts differ from the recorded ones) and **`not_attempted`** (the
bundle pins a kernel this server does not have, so nothing was replayed). Calling
the second one a divergence would be a false claim about someone's evidence. Each
trace carries its own status, so one malformed trace is visible as itself instead
of dragging the whole upload into an unexplained mismatch.

What replay proves is deliberately narrow: the verdict core — verdict, gate,
driving value id — recomputed under the pinned kernel and compared to what was
recorded. Decision prose may evolve without changing a verdict, so it is outside
the comparison, and behaviour is not reproduced at all: the model's choices were
sampled once and frozen into the traces.

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
blocks: BYOK agent (cassette-driven), Control Plane export, bundle signing, the
in-server local run and its CLI parity, the experiment catalogue/composer, and
uploaded-bundle replay. Optional Ed25519/BYOK paths skip cleanly when
PyNaCl / the Anthropic SDK are absent.
