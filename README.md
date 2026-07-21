# axor-lab

Axor Lab — standalone research surface for the Axor governance stack: bring an agent, run attack scenarios ungoverned/governed on simulated tools, investigate single trials (EvidenceCase), replay governance verdicts exactly, and publish reproducible bundles.

- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — the production-ready implementation plan (phases, reuse map, milestones, definition of done). The MVP spine is implemented; see its status block.
- **[docs/POST_MVP_PLAN.md](docs/POST_MVP_PLAN.md)** — the post-MVP plan: BYOK model adapter, Control Plane export, full web app, production hardening, then the Later tier (instrumented endpoints, sandbox + cloud code, multi-agent games, population scale) and the commercial track.
- **[contracts/](contracts/)** — the engineering contract: 9 JSON Schemas, statistics/claims/provenance semantics, lifecycle, threat model, MVP contract, vertical slice, acceptance tests. Where prose and a contract disagree, the contract wins. Validate: `cd contracts && python3 validate.py && python3 validate_slice.py`.
- **[docs/design/](docs/design/)** — product narrative (spec-lab v0.3), packaging/economics, bench format guide, UI mocks.

## Maturity — subsystems are NOT equally production-ready

Axor Lab is a contract-first **executable research prototype** with a
production-oriented contract, not yet a hosted SaaS. Honest per-area status
(see `docs/POST_MVP_PLAN.md` for the roadmap):

| Area | Maturity | Notes |
|---|---|---|
| contracts, local runner, replay, EvidenceCase, regression, analysis | **beta** | the vertical-slice spine; correctness-hardened over multiple review rounds (typed replay values, replay rejects malformed traces, predicate completion fail-closed, evidence-graph verifier now **resolves every trial's scenario/condition and every trace tool's manifest in-bundle**, **sensitive labels propagate through model output**, **a redacted secret keeps a runtime-only value so the real kernel still sees it without serializing it**, **per-driving-arg allowlist supersession**, **the simulator honors its manifest contract**, **a single failed trial no longer sinks the analysis — completed-only outcomes, missingness reported first**, **a regression pin records the whole ordered verdict sequence and each pin replays under its OWN scenario's inputs** — no false regression on a multi-call/multi-scenario bundle, **EvidenceCase resolves the real governor via `resolve_kernel` and correlates the DENY to its intent by call_id**, **local `publish` proves replay only — it never mints a statistical claim over self-reported aggregates — and content-addresses the publication by its whole body**, **regression honors the replay STATUS so a malformed trace is never a false match**, **the value-ledger is unambiguous — unique value_ids, canonical_value_hash consistency, strictly-ordered seq**, **the canonical hash is full RFC 8785 — floats in ECMAScript form, keys sorted by UTF-16 code unit, non-string keys and unsafe integers and lone surrogates rejected, pinned against the official edge vectors**, **a bundle overwrite can't destroy the prior bundle on a crash**, **EvidenceCase separates a self-reported `explicit_flow_tracked` claim from a verified one**, **a fail-closed DENY is representable as valid evidence — a null `driving_value_id` with a typed `driving_unresolved` reason, not a fake ledger id that would fail validation**, **replay is honest about capability — a REDACTED sensitive value a decision turned on yields `redacted_input_unavailable` (never a false match/mismatch over a hash sentinel), the fail-closed reason is part of the replay-comparable core, and an EvidenceCase claims `exactly_replayable` only for a replayable status**, and **the offline `axor-lab verify` is a strict state machine — a `signed` receipt with no verifiable signature exits UNVERIFIED(5), a tampered one exits 1, integrity is never confused with authenticity**, and **(round 16) a pinned real kernel is the kernel that RAN or the trace is `unsupported_kernel` — `axor-core@X` is never silently replaced by the reference kernel; the CP earned bridge and every hosted statistic are RECOMPUTED from the traces, never trusted from an uploaded aggregate; McNemar's power is the discordant n; `executable_config_hash` binds the whole compiled governor config including untrusted-field taint; and an ungoverned trace's arg-independent ALLOW replays even when a bound value is redacted**, and **(round 17) ONE kernel resolver serves every surface — CLI regress / EvidenceCase / incident import resolve each trace's own scenario inputs via `resolve_kernel_for_trace`; the CP bridge requires the COMPLETE trace set (a cherry-picked subset raises) and emits an immutable `cp_bridge_analysis/v1` receipt; and the carry-over key is honestly `parametric_policy_hash` (symbolic `$inputs`), distinct from the concrete per-scenario `runtime_config_hash`**, and **(round 18) `regress --kernel X` tests the CANDIDATE kernel X — `resolve_candidate_kernel_for_trace` takes the policy from the candidate condition and the version from `--kernel` while keeping each trace's own scenario inputs, so a counterfactual regression never silently re-runs the trace's original recorded kernel (that stays `resolve_recorded_kernel_for_trace` for exact replay); replay narrows its kernel-resolution guard to `UnknownKernelError` so an internal bug propagates instead of masquerading as `unsupported_kernel`; and the CP handoff verifies the evidence graph (`verify_bundle`) and proves the per-scenario `runtime_config_hash` it recommends was RECORDED at build time (`config_provenance`), not synthesized at export**) |
| multi-scenario benchmark bundle | **beta** | trace ids carry the full trial coordinate; a 3-scenario suite survives a build→write→read→verify→replay roundtrip (`tests/test_multiscenario_bundle.py`) — the round-2 P0 that used to corrupt it is fixed |
| AgentDojo adapter | **beta** | curated **banking** subset (3 tasks), not arbitrary-dataset import |
| server / catalog | **beta (local)** | token-gated writes, content-hash filenames, atomic writes, **recomputes every statistical aggregate AND its test from the traces** (rejects a fabricated McNemar/two-proportion p or an unknown metric; the recomputed marginal matches the runner's per-condition count so an honest bundle is not falsely rejected at missingness), **hides `private` publications on every read route** (HTML, JSON, EvidenceCase), **content-addresses each publication by its whole body** so it is immutable (re-publish is idempotent-or-distinct; a disk-edited record is dropped on load), **re-runs the full publish handshake (replay + recompute + re-mint) on restart so a hand-assembled publication never loads unverified**, **counts only cryptographically verified reproductions in the public badge** (unsigned self-reports shown separately; on load each attestation is re-verified, bound to its publication, and schema-checked), **re-earns an `integrity: signed` badge on load only from a persisted author-signature receipt** (a forged signed badge degrades to hash_verified and is dropped), builds each DENY claim from the **recorded decision** correlated by call_id, and **isolates each publication on startup so one corrupt file can't sink the whole catalog**, **refuses to resurrect an admin-taken-down publication via a write-token re-publish** (tombstone wins, 409), **serves a downloadable reproduction package with a PORTABLE verification receipt** (`GET /api/publications/{id}/bundle` returns bundle+traces+receipt; `axor-lab verify` checks content hashes, replay, and the receipt's signed_ref/signature OFFLINE — no server trusted — and the publish response carries an acceptance receipt of what the server verified), **makes an admin takedown final over a STABLE evidence lineage** (an `evidence_lineage_ref` invariant to bundle_id/created/packaging — takedown retires every sibling on that lineage, blocks any re-publish under altered metadata OR repackaged bytes, guards every read, and a two-pass cold load collects all lineage tombstones before loading publications), **issues a deterministic, content-addressed, optionally Ed25519-SIGNED acceptance receipt** (persisted, returned on publish, and served in the download package alongside the publication body so an offline reader can verify the claims, not just the bytes), **reports completed/planned + condition-imbalanced missingness in every statistical claim**, **recomputes the WHOLE test object (the two_proportion interval included) and rejects any test field it does not itself recompute**, and **maps a malformed request body to a clean 4xx, never a 500**, and **(round 16) verifies the ENTIRE downloaded package before `verify` exits 0 — a stripped receipt or an edited publication/acceptance fails; lineage takedown is durable, crash-safe, and array-order-independent; the exact recomputed test shape is required and an inconclusive uploaded test is refused; and the persisted acceptance is RESTORED on load (never re-minted under a rotated key)**, and **(round 17) a downloaded package cannot be silently downgraded — `verify` requires a versioned envelope (`--allow-bare` to opt out) and an UNSIGNED server acceptance reads as UNVERIFIED, not a pass; a historical acceptance's signature is verified against a server keyring (a forgery is quarantined, a rotated-out key kept opaque, never re-issued); a mixed-kernel publication page renders; the acceptance report only claims checks that ran; and the durable tombstone fsyncs the file bytes before the rename**, and **(round 18) a `signed` publication cannot be proof-downgraded — `verify` requires the author receipt's integrity to equal the publication's and treats a signed publication with no verifying key as UNVERIFIED; a damaged/forged persisted acceptance under a known key is QUARANTINED and re-attested with a distinct, timestamped `reacceptance/v1` linking to the invalid original (never silently re-minted as a clean record); and `_write_atomic` loops over short `os.write`s so a large body is never truncated on disk**; not yet a public SaaS (no OAuth/DB/object-store) |
| BYOK agent | **beta** | wrapped runtime is banking-slice-shaped; run identity carries the agent fingerprint; **live runs are analyzed as independent samples (two-proportion, exploratory) — never a paired McNemar p-value**; **a per-scenario cassette keys on the scenario name (not task text), so scenarios can't silently share a transcript**; **`--max-usd/--max-input-tokens/--max-output-tokens` are a HARD run-wide ceiling checked BEFORE the first trial and BEFORE every provider call inside a trial's loop (not just between trials, so one trial can't overshoot by its whole fan-out of calls); a ≤ 0 limit is rejected, the remaining output budget caps the next call's `max_tokens`, and actual usage+spend is recorded in the bundle**; **a USD-only budget reserves output tokens and counts the tool schema in its pre-spend projection**; **the trial plan is block-balanced (scenario→repeat→condition) so a cost stop keeps matched pairs, missingness is condition-aware, and a cost-stopped run is labelled `[completed_partial]`/`[stopped_cost_ceiling]` — never `[completed]` — with planned/completed/failed/excluded reported separately**; **a USD-only budget is a HARD ceiling (the next call's max_tokens is capped at what the remaining USD can buy, not just estimated), and condition order is counterbalanced across blocks with the execution order recorded on each trial**; generic multi-tool loop is roadmap |
| endpoint gateway | **experimental** | fail-closed governance + **SSRF guard: `safe_open()` self-resolves DNS, validates every address, connects pinned to a validated IP (no library re-resolution), keeps Host/SNI, and re-checks each redirect** (`ssrf_check` alone is only an address validator) + bearer token + per-run secret + quotas + per-run locking, atomic seq, finalize-before-read, 400/413 body limits; **BOTH the HTTP gateway AND the in-process SDK path share one gating rule — the gate decides on the BOUND ledger value, never a client-forged concrete arg (a clean binding + malicious arg is refused, not laundered), and returns the authoritative args a cooperating proxy must run**; **it is an advisory DECISION point, not a tool executor — enforcement needs a cooperating/attested runtime, and an untrusted client's self-reported labels are `heuristic_attribution`, never `explicit_flow_tracked`** (`contracts/endpoint-protocol.md` documents exactly this, no phantom dispatch route); **`authoritative_args` is the COMPLETE bound call — every required arg must have a value id**; **it is a real conformity boundary — every event shape is validated, an unknown tool is a clean 400 (not a KeyError→500), a redacted sensitive value must pin its bytes with a canonical_value_hash, the assembled trace is validated as a conformant trace/v1 at finalize before it can be served, and a finalized run is evicted for quota ONLY after its trace has actually been DELIVERED (a finalized-but-unread trace is never dropped)**, and **(round 16) delivery is CLIENT-ACKNOWLEDGED, not inferred from a GET — the trace is frozen at finalize and served repeatably, and only an explicit `POST /trace/ack` marks it evictable, so a failed socket write or a client crash leaves the trace retrievable**, and **(round 17) the gateway/in-process endpoint resolve the REAL governor through the shared resolver (a real-kernel pin that isn't installed fails at construction, never a disguised reference kernel), the ack is BOUND to the bytes (finalize returns a `trace_ref`, the ack must echo `content_hash(frozen_trace)` after a real GET), and `max_runs` bounds only ACTIVE runs so finalized-unacked runs can't exhaust the quota**, and **(round 18) retention NEVER sheds unread evidence — the count AND byte caps evict only an acknowledged (DELIVERED) trace, else fail closed (429); a real-kernel gate on BOTH surfaces DENYs `provenance_unavailable` when a redacted untrusted value is bound to a gated arg (the governor can't register taint it can't see, so it fails closed, not open); the in-process endpoint rejects an unknown event type instead of silently dropping it; a per-run byte quota bounds memory beyond the event count; and the ack response is honestly `client-declared`, not server-verified delivery**; manifest-derived labels + a per-event attestation envelope + a full isolation runtime are roadmap |
| sandbox | **experimental** | real RLIMIT limits (CPU/mem/**per-file size — `max_file_mb`, not a total-disk quota**/nproc) + streaming output cap (boundary-exact) + **whole-process-group sweep on exit** (a forked descendant can't outlive the run) + isolated cwd; NOT namespace/seccomp/cgroup isolation — a per-file cap is not a disk quota, absolute-path writes and a child's own `setsid()` are not contained; do not run hostile code from untrusted users |
| games / federation | **experimental** | a deterministic toy model; containment is demonstrated, not proven; measure names are honest — `governed` (was `carried_taint`), `contained()`, and `blast_radius()` (spread BEYOND the recorded origin compromise) |
| kernel | **reference + real backend** | ships `reference_taint_floor_kernel` (1 gate, stdlib) AND a real backend that drives the production `axor_core.governor.ToolCallGovernor` when axor-core is installed and the condition pins the installed version (`pip install axor-lab[kernel]`; `axor-lab run --real-kernel` repins EVERY condition — baseline included — so the compare isolates enforcement, not a mixed kernel, and the bundle carries a single kernel_version). Verified: real governor DENYs the exfil, ALLOWs the faithful payment, replays bit-identically |
| Private Lab / workspaces / billing | **design-only** | `lab_entitlement` gates features; hosted workspace surface not built |

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
- **`lab_entitlement/`** (B9) — the Private Lab license (modules as flags) and
  the two lines as code: safety free forever, org use paid; optional Ed25519.
- **`lab_endpoint/`** (B5) — instrumented-endpoint trace assembly + black-box
  eval-only labeling + SSRF guard.
- **`lab_sandbox/`** (B6) — the sandbox policy decision layer (egress
  allowlist, resource caps, no host mounts, non-persistent secrets, audit).
- **`lab_games/`** (B7) — iterated-game runtime with honest per-run statistics.

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
blocks: BYOK agent (cassette-driven), Control Plane export, entitlement,
bundle signing, instrumented/black-box endpoints, the sandbox red-team suite,
and per-run game statistics. Optional Ed25519/BYOK paths skip cleanly when
PyNaCl / the Anthropic SDK are absent.
