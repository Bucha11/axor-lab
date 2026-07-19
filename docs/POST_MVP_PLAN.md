# Axor Lab — Post-MVP Implementation Plan (v1)

The MVP spine exists (`docs/IMPLEMENTATION_PLAN.md` status block): contracts,
local runner + CLI, statistics, AgentDojo import, publish handshake + server,
all ten acceptance criteria green on the vertical slice. This document plans
everything **after** that — the Then/Later sequence from `contracts/mvp-contract.md`
plus the production hardening of the original plan's Phase 6, and the
commercial track from `axor-packaging.md`.

**Governing discipline (unchanged):** where this plan and a contract disagree,
the contract wins. Every Vision capability needs its own contract before code;
nothing is added to a tier that the contract's NOT-list excludes. Each block
below states its **contract anchor**, **what carries over from the spine**,
**work items**, and **definition of done** (an executable test or acceptance
criterion, in the same style as `contracts/acceptance-tests.md`).

The blocks are ordered by the contract's sequence — **Then** (built outward
from the spine, low new-risk) before **Later** (needs the expensive
subsystems: sandbox, endpoint governance, multi-agent). Commercial and
hardening tracks run in parallel, gated by external demand, not by code
readiness.

---

## Sequencing overview

```
Spine (done) ──┬─► B1 BYOK model adapter ─────┐
               ├─► B2 Control Plane export ────┤ Then  (outward from spine)
               ├─► B3 Web app (full Phase 5) ──┤
               └─► B4 Production hardening ─────┘
                        │
                        ▼
               ┌─► B5 Instrumented endpoint ──┐
               ├─► B6 Sandbox + cloud code ────┤ Later (new subsystems)
               ├─► B7 Multi-agent games ───────┤
               └─► B8 Population / topology ────┘
                        │
        (parallel, demand-gated) ─► B9 Commercial: Private Lab workspaces
```

`B1`–`B4` depend only on the spine and can proceed concurrently. `B6` (the
sandbox) gates `B5`'s cloud path and every `Later` block that runs untrusted
code on Lab infra. `B9` reuses the same license/entitlement as the Control
Plane and can start as soon as `B3` gives it a hosted surface.

---

## Then tier — built outward from the spine

### B1 — BYOK model-backed AgentAdapter — ✅ implemented
*The scripted agent becomes real: run the researcher's own agent on their key.*

> **Status:** `lab_agent` ships the `ModelBackend` protocol (`CassetteBackend`
> offline, `AnthropicBackend` BYOK behind an optional dependency + key), a
> `WrappedModelAgent` driving read→sink through the ledger as a `DrivingAgent`,
> and a pre-run cost estimate. The kernel gained allowlist enum-supersession
> (paper §6.3) for the over-taint utility recovery. `axor-lab run --agent
> cassette:<file>|anthropic:<model>` routes it. Covered by `test_byok_agent.py`
> (attacker DENY, faithful known-IBAN ALLOW via effect model, external
> over-taint DENY, allowlist recovery, schema-valid trace, bit-identical
> replay) — no network in CI.

- **Contract anchor:** `spec-lab.md` §1 (upload-code / wrapped-code ingest,
  `explicit_flow_tracked`), `provenance-semantics.md` §4 (labels travel with
  values inside the wrapped loop), `mvp-contract.md` Then ("BYOK inference
  in-app"), cost model (`spec-lab.md` §9: BYOK or local only, pre-run estimate).
- **Carries over:** the `AgentAdapter` protocol (`lab_runner/agents.py`) is
  already the seam — a model-backed adapter is a new implementation behind it;
  the ledger, kernel, simulator, trace format, and the whole run→bundle→publish
  pipeline are unchanged.
- **Work items:**
  1. `lab_agent/` package: a wrapped-code runtime that drives a real tool-calling
     loop (LangChain/MCP/plain function-tools) through the value ledger — every
     tool result mints `external_read` values on `untrusted_fields`; every
     model-emitted tool-call argument mints a `model_extraction` value under the
     conservative join (this is where the real §2 join replaces the scripted
     stand-in).
  2. Provider clients behind a small `ModelBackend` protocol (Anthropic first —
     `claude-*` via the Messages API tool-use loop; OpenAI-compatible second),
     keyed from env/`--api-key`, never persisted server-side.
  3. Real pre-run cost estimate: token accounting per provider, printed in the
     `[estimate]` stage before any live call; `--max-cost` abort guard.
  4. Fidelity honesty: adapters that cannot carry labels emit
     `provenance_fidelity=heuristic_attribution` and the EvidenceCase renders
     the existing warning path — never silently claim `explicit_flow_tracked`.
  5. `agent_ref` grammar extends: `anthropic:<model>`, `openai:<model>`,
     `langchain:<module:factory>`; `resolve_agent` gains these behind the
     protocol.
- **Definition of done:** the banking slice runs on a live model with BYOK,
  produces a `trace/v1` whose recipient value carries real
  `sources=[external_read:…]` + `model_extraction` lineage (not scripted), the
  governed condition DENYs it, and the produced bundle round-trips through
  publish→replay identically. A recorded-cassette test (no network in CI)
  replays a captured model transcript so the adapter is covered without a key.
- **Risk:** the conservative join over-taints (measured paper cost); recovery is
  a declared allowlist in `condition.policy.allowlist`, never heuristic
  un-tainting. Covered by a utility-cost regression test.

### B2 — Control Plane export (the earned bridge) — ✅ implemented
*Carry the validated policy + manifests into production; add what Lab can't provide.*

> **Status:** `lab_runner/cp_export.py` + `axor-lab export-cp` emit an
> `axor-cp-deploy/v1` config carrying the validated policy, the `config_hash`
> (byte-identical carry-over key, recomputed and asserted against the recorded
> condition), the tool manifests, and pinned regressions — plus a
> `production-todo.md` listing exactly the four NOT-reused categories
> (bindings, credentials, topology, operations). `earned_bridge()` surfaces
> only when an aggregate shows governance changed the outcome. `axor-lab
> import-incident` builds a trace-replay bundle from a production trace (second
> funnel). Covered by `test_cp_export.py`.

- **Contract anchor:** `control-plane-handoff.md` (what carries / what must be
  added), `axor-packaging.md` §8 (Production Governance is an add-on toggle,
  not a migration), the trigger rule (earned, only after a result where
  governance changed the outcome).
- **Carries over:** `condition.policy` + `config_hash`, `tool-manifest/v1`
  objects, and pinned `RegressionCase`s are the *same artifacts* the Control
  Plane consumes — export is serialization, not re-authoring.
- **Work items:**
  1. `lab_runner export-cp ./bundle --out cp-config/` — emits the exact
     validated policy + config hash, the tool manifests, and any pinned
     regressions in the Control Plane's config format (target
     `axor-control-plane/packages/axor-backend` schema).
  2. A stated **diff manifest** of what is NOT reused (honest half): real tool
     bindings, credentials/vault, deployment topology, notifications/owners —
     emitted as an explicit `production-todo.md` alongside the config, so the
     handoff never reads as "nothing is re-done."
  3. Trigger surfacing: publication/results carry an `earned_bridge` flag set
     only when an aggregate shows governance changed the outcome on the
     researcher's own agent; the web app (B3) renders the footer CTA from it.
  4. Second funnel (`control-plane-handoff.md` §Second funnel): a
     `import-incident` path that ingests a production trace → trace-replay mode
     → policy test → regression → export. (Trace-replay ingest is spine-level;
     this wires it to the CP export.)
- **Definition of done:** an exported config re-validates against the Control
  Plane's schema; a round-trip test asserts `config_hash` is byte-identical
  across the boundary (the carry-over key); the `production-todo.md` lists
  exactly the four not-reused categories.

### B3 — Web app (full Phase 5)
*The mocks become the product surface; the stdlib server becomes an API backend.*

- **Contract anchor:** `spec-lab.md` §2.5 / §7.5 (landing+catalog, published
  experiment page, run lifecycle as an explicit state machine — never a bare
  spinner), the `mocks/*.jsx` (visual contract), `claims.md` (three-mode
  EvidenceCase, split claim blocks), `statistics.md` §Implementation note
  (render stored aggregate fields verbatim; "not computed" over placeholders).
- **Carries over:** `lab_server` already renders catalog / publication /
  EvidenceCase HTML with escaping, three-axis provenance, and the split claim
  blocks — that logic is the reference for the API responses and the SSR
  fallback; the mocks are the eight screens to build.
- **Work items:**
  1. Promote `lab_server` from HTML-string rendering to a JSON API + a real
     frontend (stack mirrors `axor-control-plane/frontend`): landing+catalog,
     scenario author (MVP predicate subset only — the full boolean DSL UI is
     still deferred), run progress (explicit lifecycle states, stage-specific
     failures), results (verbatim aggregate rendering), EvidenceCase (the
     differentiating screen), published page, agent-ingest, builder.
  2. Three descending-barrier entry points (explore → reproduce → bring your
     agent), the reproduction-count + provenance badges, fork + citation.
  3. Local-runner ↔ web handshake beyond publish: run-status streaming for
     hosted template runs (the lifecycle state machine), leaving black-box
     endpoint UI explicitly out (B5).
  4. Terminology lint extended to the frontend string bundle (the existing
     `test_terminology.py` becomes the seed).
- **Definition of done:** all ten acceptance criteria pass **through the UI
  paths** (not only the CLI/API), the results screen shows "not computed" when
  an aggregate field is absent, and the EvidenceCase screen renders the three
  modes with the counterfactual labeled — Playwright drives it headless
  (Chromium is pre-installed).

### B4 — Production hardening (original plan Phase 6) — ◐ code slices implemented
*The "-ready" in production-ready; no new product surface.*

> **Status (code-level slices done; infra deferred):** the `integrity=signed`
> path ships — `lab_contracts/signing.py` (optional Ed25519 over the bundle's
> `content_hashes`, same crypto as CP); the server upgrades `hash_verified →
> signed` only for a KNOWN author key, refuses an unknown key, and never
> changes `origin`. Takedown removes a publication from the catalog while
> preserving its append-only attestation record (survives a store reload).
> CI gained a cross-OS replay-determinism matrix and a schema-compat gate (a
> schema change that breaks the slice examples or the AgentDojo import fails
> CI). Covered by `test_hardening.py` + a takedown HTTP test.
>
> **Still infra-level (deferred):** Postgres + content-addressed object
> storage, GitHub OAuth, observability/alerting, signed PyPI release. These
> need a hosting target, not more contract code.

- **Contract anchor:** original `IMPLEMENTATION_PLAN.md` Phase 6, `threat-model.md`
  §4–5 (untrusted payloads, trust in results), `axor-packaging.md` §3 (hosted
  vs self-hosted metering).
- **Work items:**
  1. **Persistence:** replace the file-backed store with Postgres (publications,
     catalog, users, attestation log) + content-addressed object storage for
     bundles/traces; migrations; backup + restore drill.
  2. **AuthN/Z:** GitHub OAuth (the free tier's identity, as CP); publications
     owned by accounts; unlisted = capability URL; publish rate limits.
  3. **Signing:** the `integrity=signed` path with Ed25519 detached signatures
     over `content_hashes` — the *same* crypto as CP licensing
     (`cp-monetization.md` §4); a known author key upgrades `hash_verified` →
     `signed` without changing `origin`.
  4. **Observability:** structured logs, error tracking, publish/replay/verify
     latency metrics, alerting.
  5. **CI/CD:** the acceptance suite as the release gate; a schema-compat check
     (a schema change that breaks slice examples or the AgentDojo import fails
     CI); a pinned-kernel determinism matrix (replay bit-identical across two
     OS runners); dependency audit; signed PyPI release of `axor-lab`.
  6. **Legal/ops:** takedown workflow for published runs, redaction-manifest
     enforcement end-to-end, privacy note.
- **Definition of done:** a hardened hosted deployment at `lab.useaxor.net`
  serving the public catalog; the determinism matrix and schema-compat gate are
  required checks; a takedown removes a publication from the catalog while
  preserving the append-only attestation record.

---

## Later tier — new subsystems (each needs its contract first)

### B5 — Instrumented-endpoint contract
*Govern an agent behind an endpoint that emits value-carrying events.*

- **Contract anchor:** `endpoint-protocol.md` (the split: instrumented =
  governance-capable via `POST /runs` + `SSE /runs/{id}/events` + tool gateway;
  black-box = evaluation-only, labeled everywhere), `spec-lab.md` §1 (endpoint
  governance requires instrumentation — stated plainly), endpoint safety (SSRF,
  DNS-rebinding, egress runner).
- **Carries over:** the trace/ledger/gate pipeline is producer-agnostic — an
  instrumented endpoint is a third `producer.mode` (already in the trace
  schema); the same EvidenceCase/replay/publish path applies.
- **Work items:** the gateway (`POST /runs`, SSE event stream carrying
  value ids + labels, `POST /runs/{id}/tools/{call_id}/result` where provenance
  is minted); `producer.mode=instrumented_endpoint` with fidelity
  `explicit_flow_tracked` (labels carried) or `heuristic_attribution` (events
  only, flagged); the **black-box** mode as strictly evaluation-only (output
  scoring, no ledger, EvidenceCase unavailable, labeled "not governance"
  everywhere); endpoint safety controls (SSRF/private-network block,
  DNS-rebinding guard, isolated egress runner, auth + secret storage,
  idempotency keys on tool replay).
- **Definition of done:** an instrumented reference agent drives the banking
  slice over the gateway, producing a conformant `trace/v1` that DENYs under
  governance; a black-box run of the same task produces **no** conformant trace
  and the UI never offers gate-on/off for it. **Blocked by:** none for the
  instrumented path; the safety runner shares infra with B6's egress controls.

### B6 — Sandbox + arbitrary cloud code
*The single most expensive subsystem; gates every "run untrusted code on Lab infra" path.*

- **Contract anchor:** `spec-lab.md` §9 ("Sandbox is a real subsystem, not a
  phrase" — the full enumerated list), `threat-model.md` §2 (untrusted code).
- **Carries over:** nothing new at the contract layer — the sandbox wraps the
  existing local runner so cloud runs produce the *same* artifacts as local
  runs; until it exists, code execution stays local-only (the spine's posture).
- **Work items:** gVisor/Firecracker-class isolation; CPU/RAM/disk/wall-time
  caps; ephemeral FS, no host mounts; egress deny-by-default + API allowlist;
  secret injection without persistence; dependency lock; output-size caps;
  kill/cancel; audit trail; retention policy. Then the cloud runner for
  **trusted templates only** first (the Then-tier cloud path), arbitrary
  uploaded code only once the full list holds.
- **Definition of done:** a red-team suite (network egress attempt, fork bomb,
  disk fill, secret exfiltration attempt, wall-time overrun) is contained by
  the sandbox with an audit record for each; a trusted-template cloud run
  produces a bundle byte-identical to the same run executed locally. **This is
  the critical-path gate for the rest of the Later tier.**

### B7 — Multi-agent game runtime
*Players are singles or federations; composition is a variable.*

- **Contract anchor:** `spec-lab.md` §5 (Game experiment type),
  `statistics.md` §1 (the unit-of-analysis error that invalidates iterated
  games — n is runs, never rounds; serially-correlated rounds fabricate
  precision), `domain-model.md` (federation game: unit = one run of the
  federation).
- **Carries over:** `experiment.type=game` is already in the schema;
  `lab_analysis` already rejects `unit_of_analysis=round`; the trace format is
  federation-aware (`node`, `spawn`/`death`, `message_send/recv`,
  `cross_process_in` constructor).
- **Work items:** the game runtime (turn/round orchestration across nodes,
  message causality ordering — not wall clock); per-run metric computation
  (cooperation rate over a run's rounds is the run's single value); federation
  players (a node group as one observation); the continuous-metric statistics
  (paired bootstrap over runs, Wilcoxon, effect sizes — already in
  `lab_analysis`, wired to game aggregates); seed game catalog. **Blocked by:**
  B6 for any cloud-executed game; local games can precede it.
- **Definition of done:** an iterated game reports n = repeats (never rounds),
  its CI narrows with repeats, and a federation game's per-node values are
  structure-within-observation (a property test asserts a round-level n is
  rejected at aggregate time).

### B8 — Population scale + arbitrary topology
*Towns of N agents, arbitrary interaction graphs (the outreach targets).*

- **Contract anchor:** `spec-lab.md` §9 (population-scale is Vision), Prompt
  Infection / topology-attack outreach targets (`outreach-targets.md`).
- **Carries over:** the federation trace model and A2A `cross_process_in`
  constructor (carried-taint containment at boundaries) from `axor-core`.
- **Work items:** population-scale spawn (town of N), arbitrary interaction
  topology, ephemeral-node accounting (concurrent peak, not spawn count — the
  same amplification fix budget caps used); scale-out execution on the sandbox
  (B6). **Blocked by:** B6 and B7.
- **Definition of done:** a Prompt-Infection-shaped experiment reproduces
  carried-taint containment at the first federation boundary, with honest
  per-run statistics at N-agent scale.

---

## Parallel track — Commercial (demand-gated, not code-gated)

### B9 — Private Lab workspaces (the paid rung) — ✅ entitlement implemented
*Turn the spine into revenue the moment there's a buyer; reuse the CP license.*

> **Status:** `lab_entitlement` ships the license (modules as flags, one file
> both modules — mirrors CP `cp-monetization.md` §4) and the two lines as code:
> `SAFETY_FEATURES` are free forever (the `FeatureGate` never consults a license
> for them), `ORG_FEATURES` require a non-expired license flagging `private_lab`,
> tier-bundled (team vs security). Expiry degrades org features to read-only and
> never touches safety. Optional Ed25519 sign/verify (PyNaCl) over a JCS-subset
> payload, same crypto as CP. Covered by `test_entitlement.py` (safety-free,
> org-paid, expiry read-only, module-flag, signed round-trip when PyNaCl is
> present). The hosted workspace UI/billing surface is the remaining
> infra-level work (rides on B3/B4).

- **Contract anchor:** `axor-packaging.md` (single source of truth: tiers,
  prices, the one-ladder/two-modules frame), `lab-economics.md` (bill the
  workflow — incident → EvidenceCase → policy → regression — not the trace),
  `cp-monetization.md` §1–2 (the two lines: safety free forever; org use paid).
- **Carries over:** the same Ed25519-signed, offline-verifiable license file as
  CP (`cp-monetization.md` §4) — modules are flags (`private_lab`,
  `control_plane`), not separate licenses; B4's signing infra is the same
  crypto.
- **Work items:**
  1. Workspace tier (`private_lab: true`): private scenarios/incidents, private
     EvidenceCases (included with a generous limit, **never** the meter),
     shared scenarios, basic CI — gated by the license, not by a safety
     feature.
  2. Security tier: scheduled regression CI + history, approvals, policy/kernel
     comparison, compliance/audit exports.
  3. Metering per `axor-packaging.md` §2–3: hosted trial allowance in the
     billing system (not the license file, so air-gapped self-hosted works);
     storage/retention overage; BYOK inference (Axor never resells tokens).
  4. The Incident-to-Regression pilot (`axor-packaging.md` §6) as the first
     revenue motion: fixed scope, bounded credit — a productized packaging of
     the spine, no new engine work.
- **Definition of done:** the license file gates org features and degrades to
  read-only on expiry **without disabling any safety feature** (Line 1); a
  public Lab run stays free and reproducible; the pilot deliverables map 1:1 to
  existing spine capabilities (one incident → one EvidenceCase → one policy →
  one regression pack → export).
- **Free/paid line (canonical, restated):** free forever — Public Lab, local
  BYOK runs, replay, local regressions, all safety features incl. EvidenceCase
  *capture*. Paid — hosted private org workspace, scheduled CI, approvals,
  retention, SSO, compliance exports. Trigger is organizational use, never a
  safety feature, never hobby-scale privacy.

---

## Definition of done (post-MVP, overall)

Each block ships when its own DoD test passes. The tier is "complete" when:

- **Then:** a researcher runs their *own* agent on their key (B1), gets a result
  where governance changed the outcome, follows the earned bridge to a
  re-validated Control Plane config (B2), through a web UI that passes all ten
  acceptance criteria (B3), on hardened hosted infra with a determinism gate
  (B4).
- **Later:** untrusted code and endpoints run under a red-team-verified sandbox
  (B5/B6), multi-agent games report honest per-run statistics (B7), and
  population-scale federation experiments reproduce carried-taint containment
  (B8) — each built outward from the spine, never in parallel with it.
- **Commercial:** the same license that guards the command channel guards the
  workspace; safety is free forever; the first dollars come through the
  incident-to-regression pilot (B9).

At that point every capability in `spec-lab.md`'s Vision has an executable
referent and a contract behind it — the same standard the MVP was held to.
