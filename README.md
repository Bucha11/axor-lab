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
| contracts, local runner, replay, EvidenceCase, regression, analysis | **beta** | the vertical-slice spine; correctness-hardened over multiple review rounds (typed replay values, replay rejects malformed traces, predicate completion fail-closed, evidence-graph verifier, **sensitive labels propagate through model output**, **a redacted secret keeps a runtime-only value so the real kernel still sees it without serializing it**, **per-driving-arg allowlist supersession**, **the simulator honors its manifest contract**, **a single failed trial no longer sinks the analysis — completed-only outcomes, missingness reported first**) |
| multi-scenario benchmark bundle | **beta** | trace ids carry the full trial coordinate; a 3-scenario suite survives a build→write→read→verify→replay roundtrip (`tests/test_multiscenario_bundle.py`) — the round-2 P0 that used to corrupt it is fixed |
| AgentDojo adapter | **beta** | curated **banking** subset (3 tasks), not arbitrary-dataset import |
| server / catalog | **beta (local)** | token-gated writes, content-hash filenames, atomic writes, **recomputes every statistical aggregate AND its test from the traces** (rejects a fabricated McNemar/two-proportion p or an unknown metric), **hides `private` publications on every read route** (HTML, JSON, EvidenceCase), **content-addresses each publication by its whole body** so it is immutable (re-publish is idempotent-or-distinct; a disk-edited record is dropped on load), **re-runs the full publish handshake (replay + recompute + re-mint) on restart so a hand-assembled publication never loads unverified**, **counts only cryptographically verified reproductions in the public badge** (unsigned self-reports shown separately; on load each attestation is re-verified, bound to its publication, and schema-checked), **re-earns an `integrity: signed` badge on load only from a persisted author-signature receipt** (a forged signed badge degrades to hash_verified and is dropped), builds each DENY claim from the **recorded decision** correlated by call_id, and **isolates each publication on startup so one corrupt file can't sink the whole catalog**; not yet a public SaaS (no OAuth/DB/object-store) |
| BYOK agent | **beta** | wrapped runtime is banking-slice-shaped; run identity carries the agent fingerprint; **live runs are analyzed as independent samples (two-proportion, exploratory) — never a paired McNemar p-value**; **a per-scenario cassette keys on the scenario name (not task text), so scenarios can't silently share a transcript**; **`--max-usd/--max-input-tokens/--max-output-tokens` are a HARD run-wide ceiling checked against actual usage between trials (stops before the next call), and actual usage+spend is recorded in the bundle**; generic multi-tool loop is roadmap |
| endpoint gateway | **experimental** | fail-closed governance + **SSRF guard: `safe_open()` self-resolves DNS, validates every address, connects pinned to a validated IP (no library re-resolution), keeps Host/SNI, and re-checks each redirect** (`ssrf_check` alone is only an address validator) + bearer token + per-run secret + quotas + per-run locking, atomic seq, finalize-before-read, 400/413 body limits; **BOTH the HTTP gateway AND the in-process SDK path share one gating rule — the gate decides on the BOUND ledger value, never a client-forged concrete arg (a clean binding + malicious arg is refused, not laundered), and returns the authoritative args a cooperating proxy must run**; **it is an advisory DECISION point, not a tool executor — enforcement needs a cooperating/attested runtime, and an untrusted client's self-reported labels are `heuristic_attribution`, never `explicit_flow_tracked`** (`contracts/endpoint-protocol.md` documents exactly this, no phantom dispatch route); **`authoritative_args` is the COMPLETE bound call — every required arg must have a value id**; manifest-derived labels + a per-event attestation envelope + a full isolation runtime are roadmap |
| sandbox | **experimental** | real RLIMIT limits (CPU/mem/**per-file size — `max_file_mb`, not a total-disk quota**/nproc) + streaming output cap (boundary-exact) + **whole-process-group sweep on exit** (a forked descendant can't outlive the run) + isolated cwd; NOT namespace/seccomp/cgroup isolation — a per-file cap is not a disk quota, absolute-path writes and a child's own `setsid()` are not contained; do not run hostile code from untrusted users |
| games / federation | **experimental** | a deterministic toy model; containment is demonstrated, not proven |
| kernel | **reference + real backend** | ships `reference_taint_floor_kernel` (1 gate, stdlib) AND a real backend that drives the production `axor_core.governor.ToolCallGovernor` when axor-core is installed and the condition pins the installed version (`pip install axor-lab[kernel]`; `axor-lab run --real-kernel`). Verified: real governor DENYs the exfil, ALLOWs the faithful payment, replays bit-identically |
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
