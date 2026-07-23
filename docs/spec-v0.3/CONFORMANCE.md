# Spec conformance — status

The spec re-scopes Axor Lab to the experiment / evidence layer over Axor runtime
traces (`contracts/architecture-boundary.md`, READ FIRST). This file tracks how the
repo is brought into line, phase by phase, so the migration stays reviewable instead
of one destructive sweep.

## v0.4 — framework adapters (contracts/adapters.md §4)

The two pre-built framework adapters now exist, each a thin `AgentAdapter` = the
generic `RunnerAgentAdapter` + a framework `ModelBackend` (the shared wrapped runtime
does the provenance mint + gate-before-dispatch, so downstream behaviour is
identical):

- **`lab_client.frameworks.axor_claude`** — the Anthropic Messages tool-use loop; its
  model decision is the existing `AnthropicBackend`. `axor_claude.adapter(…)` builds a
  ready `AgentAdapter`; a deterministic `CassetteBackend` can be injected for
  offline/CI.
- **`lab_client.frameworks.axor_langchain`** — a LangChain chat model
  (`bind_tools(…).invoke(…)`); its model decision is the new
  `lab_agent.LangChainBackend` (duck-typed, so `langchain` is an optional dependency).

Proved offline (no live SDK): a fake LangChain model and a cassette drive **real
governed trials** — the injected transfer is DENIED under `enforcement=on` and
ALLOWED under `off`, via the actual kernel, and each adapter reports its
`adapter_kind` (`claude` / `langchain`). The generic/custom `RunnerAgentAdapter`
stays first-class, not a fallback. (These live in `axor-lab` for now; they move to
their own `axor-claude` / `axor-langchain` repos once those are added to the session.)

## v0.4 — adapter layer (contracts/adapters.md)

The latest bundle adds `contracts/adapters.md` (the adapter is the only place a
user's agent meets Axor) and cross-references it from architecture-boundary /
agent-connection / runner-protocol. `lab_client` is brought into line with it:

- **`ExecutionContext`** now carries what §6 specifies — `condition` (thin ref),
  `trace_sink` (append-only, the ONLY event egress), `tools`, `fixtures`, `limits`,
  `cancel` — plus the assigned `trial` coordinate. New `TraceSink` / `Limits` /
  `CancelToken` types.
- **`LabRuntimeClient(url, token, adapter=…)`** (§10): the adapter is attached to the
  client from outside; `client.run_job_loop()` builds the `ExecutionContext`, calls
  `adapter.run()`, ships the events the adapter emitted **through the sink**, then
  completes the trial. An adapter opens no network egress of its own (§11).
- **`RunnerAgentAdapter`** now emits its kernel events through `ctx.trace_sink` and
  declares `provenance_fidelity: explicit_flow_tracked` — it does the two provenance
  writes and gates BEFORE dispatch via `run_trial` (§2/§7/§8). The e2e test asserts
  Lab received the streamed events (sink egress) and built Results itself.

Docs adopted: `adapters.md` (new), and the updated `agent-connection.md` /
`architecture-boundary.md` / `runner-protocol.md` / `spec-lab.md` / mocks /
`validate.py` / `validate_slice.py`. Schema interpretation unchanged (Lab-owned
correctness supersets kept; shared schemas byte-equivalent).

## v0.4 — two separate products

The latest spec sharpens the boundary: **Control Plane and Axor Lab are two separate
products** — separate repos, backends, URLs, APIs, credentials, and **stores**. What
is shared is only the **local** axor-core + agent adapter and the **trace/event +
tool-manifest schemas** — never a backend. This *retires* the earlier "shared trace
fabric / integrated deployment injects CP-backed providers of the same ports"
framing. Adopted (commit "v0.4 two-products"):

- **Docs**: `contracts/architecture-boundary.md` (two-products rule), the new
  `contracts/agent-connection.md` (canonical connection topology: `PlaneClient`
  `axcp_`→control.useaxor.net vs `LabRuntimeClient` `axlab_`→lab.useaxor.net;
  identity/token-exchange; integrated = server-side ports only), plus the updated
  `control-plane-handoff` / `domain-model` / `ui-backend-contract` / `validate.py`
  and the business docs + mocks.
- **`lab_server/providers.py` reframed**: the data ports (`RuntimeRegistry`,
  `TraceStore`, `TraceIngest`, `ArtifactStore`) are **Lab's own**, swappable only for
  another Lab-owned backend (in-memory ↔ durable) — never CP-backed, no shared trace
  fabric. The ONLY CP-facing seams are three new *optional, server-side* integration
  ports: `ControlPlaneRuntimeProvider` (import a CP runtime reference),
  `ControlPlanePromotionBackend` (promote Lab→CP), `ControlPlaneIdentityProvider`
  (optional org SSO). Standalone Lab wires none of them. `RuntimeJobStore` docstrings
  updated: it issues its own `axlab_` token; not shared with CP.
- **New `lab_client/` package** (the concrete connection layer the spec introduces):
  the `AgentAdapter` protocol (`describe`/`run`/`reset` + `AgentDescription` /
  `AgentInput` / `AgentRunResult` / `ExecutionContext`), the Lab-owned
  `LabRuntimeClient` + `run_job_loop` (poll → claim → run each trial locally → upload
  trace, stdlib-only, `axlab_` token), and **`RunnerAgentAdapter` — a real adapter
  that actually executes an assigned trial locally through the axor-core kernel**
  (`lab_runner.run_trial`): the agent decides, the kernel governs, provenance is
  built, and the GENUINE `trace/v1` it produced is uploaded — nothing canned. A
  framework/BYOK agent swaps in as the `agent`. Proved end-to-end by test: a runtime
  connects, claims a job, runs 6 real trials (1 scenario × 2 conditions × 3 repeats)
  through the kernel, uploads real governed traces (with real ALLOW/DENY gate
  decisions — the governed condition blocks the attack), and Lab binds each to its
  unit and computes ASR itself. No Control Plane involved.

**Schema interpretation (unchanged):** the new spec's Lab-owned schemas are an
older/silent snapshot (shorter descriptions; `experiment.comparison_design` absent)
— adopting them verbatim would regress the r18–r21 statistics-soundness fields the
removal list never names, so the repo keeps its correctness supersets as documented
Lab extensions; only formatting differs. The shared `trace`/`tool-manifest` schemas
are byte-equivalent (whitespace only).

## Interpretation (how "hold to the new spec" is applied)

The spec changes two different kinds of thing, handled differently:

1. **Explicit structural direction** — the subsystems `architecture-boundary.md`
   names as *removed / deferred*, the schema-ownership split, the thin-wrapper
   `condition`, the contract-doc rewrites. These are adopted literally.

2. **Field-level schema deltas where the spec is merely an older/silent snapshot**
   — the spec's Lab-owned schemas predate the r18–r21 correctness fields
   (`execution_id`, `config_provenance`, `experiment_design`, `comparison_design`,
   `runtime_provenance`, `resolved_kernel_fingerprint`, `statistics_integrity`, …).
   The `architecture-boundary.md` removal list does **not** name any of them, and
   dropping them would silently regress the earned-bridge soundness the repo just
   built. Because the spec's ownership table makes `bundle/condition/experiment/
   publication` **Lab-owned**, Lab legitimately keeps these as an **extension layer**
   on top of the spec baseline. `trace`/`tool-manifest` are axor-core-owned; the
   repo keeps its richer, replay-load-bearing versions as the de-facto axor-core
   baseline (the spec's `_shared_from_axor_core/` copies are stubs and dropping the
   `call_id` / `decision_value` / event discriminators they omit would break replay,
   gating, and EvidenceCase — all explicitly *in* scope).

If the intent is instead to strip those fields to the spec's exact schemas, that is
a clean, separate follow-up — say so and it happens.

## Done

**Phase 1 — subsystems retired** (commit "v0.3 Phase 1"):
`lab_endpoint` (gateway/MCP proxy), `lab_sandbox`, `lab_games`, `lab_entitlement`
and their 12 test suites deleted; dropped from pyproject + the CI ruff/real-kernel/
crypto jobs; `contracts/endpoint-protocol.md` retired.

**Additive conformance** (commit "spec v0.3: additive conformance"):
`condition` gains thin-wrapper `kernel_ref` / `policy_ref` / `runtime_config_hash`;
`contracts/architecture-boundary.md` + `ui-backend-contract.md` +
`_shared_from_axor_core/` reference schemas + the `docs/spec-v0.3/` narrative,
authoring, business and mock docs.

**Phase 4 (docs) — contract docs adopted** (this commit):
`control-plane-handoff.md`, `domain-model.md`, `lifecycle.md`, `mvp-contract.md`,
`runner-protocol.md` replaced with the v0.3 versions (all 11 shared contract docs
now match the spec byte-for-byte). These describe the target — the runtime-jobs
pull API and the `RuntimeRef`/`TraceSource`/`connected_runtime` lifecycle — which
the code does not yet implement (see Pending).

**Schema conformance decision (Phase 2):** resolved per the Interpretation above —
Lab-owned schemas kept as supersets with the thin-wrapper fields added; the r18–r21
correctness fields retained as Lab extensions; trace/tool-manifest kept rich. No
`kernel_version`-required change (it conflicts with the repo's mixed-kernel bundles,
which legitimately omit the single global kernel_version).

**Runtime-jobs execution API — simple implementation** (`lab_server/runtime_jobs.py`):
the connected-runtime contract "Lab assigns, the runtime executes" exists now, kept
deliberately simple (in-memory, single process, stdlib):

  POST /runtimes/connect  -> { runtime_ref, ingest_key }   (scoped per-runtime key)
  GET  /runtimes                                            (control)
  POST /runs              -> { run_id, state }              (assign an experiment)
  GET  /runs/{id}         -> { state }                       (a lifecycle state)
  GET  /runs/{id}/results -> collected trials + pushed traces
  GET  /runtime/jobs                                   (runtime polls; ingest_key)
  POST /runtime/jobs/{id}/claim                         (claim -> the assignment)
  POST /runtime/jobs/{id}/trials/{trial_id}/events      (stream kernel events)
  POST /runtime/jobs/{id}/trials/{trial_id}/complete    (finalize a trial + trace)

The `connected_runtime` lifecycle
(`waiting_for_runtime → running → receiving_traces → … → completed`) is driven by
the store; a runtime can only claim/drive its own jobs. Durability, per-tenant
scoping, SSE streaming, and bundle assembly from the collected traces are the
extension points — the connection possibility is open, the implementation is small.

**Phase 3 — acceptance machinery collapsed** (`lab_server/store.py`,
`lab_runner/cli.py`, `lab_contracts/signing.py`): `lab_server` acceptance is now
just *publication + bundle hash + optional signature + reproduction records*. The
`reacceptance/v1` schema, `acceptance-history/` chain, tombstone-chain resolution
and quarantine/re-root logic are removed (v0.3 defers them). `_load_acceptance`
restores a persisted `axor-lab-acceptance/v1` verbatim (never re-minted under a
rotated key; an opaque record whose key we no longer hold is preserved untouched);
a *damaged* persisted acceptance is discarded and a fresh `acceptance/v1` is minted
from the current bundle on load. `verify_reacceptance` and the CLI's reacceptance
branch are gone; the download package no longer carries `acceptance_history`. Basic
single-hop lineage takedown (`_write_lineage_tombstone`) is retained as a Lab
feature — it is not part of the removed acceptance-chain machinery.

**Runtime-connection surface extended** (`lab_server/runtime_jobs.py`): the
UI-facing control endpoints from `ui-backend-contract.md` §2 now exist over the
same store —

  POST /scenarios/validate   -> { ok, errors[] }   (lab_contracts.validate_scenario)
  POST /experiments/plan     -> { trials, estimate }  (deterministic unit expansion)
  POST /runs/{id}/confirm    awaiting_confirmation -> waiting_for_runtime
  POST /runs/{id}/aggregates attach bundle.aggregates + finalize the run
  GET  /runs/{id}/events     text/event-stream (state + trial-progress frames)
  GET  /runs/{id}/results    now carries `aggregates` (RENDERED, never recomputed)
  GET  /runs/{id}/trials/{trial_id}/trace   the completed trial's trace

`create_run` supports the `awaiting_confirmation`/`ready` gate (carrying the
estimate the operator confirms before a runtime ever sees the job), and a
`TrialAttempt` is now supersede-idempotent: re-completing a trial with the same
trace is a no-op, a different trace (or streaming events into a finished trial)
supersedes the prior attempt and bumps `attempt`/`superseded`. The SSE endpoint is
a snapshot stream today; a long-lived push stream and per-tenant scoping remain the
extension points.

**Trust-boundary restoration** (review v0.3-2 — reverses a regression the runtime
surface introduced; `lab_server/runtime_jobs.py`, `store.py`):

- **No trust in uploaded aggregates.** The `POST /runs/{id}/aggregates` endpoint is
  gone; `/runs/{id}/results` no longer carries uploaded numbers. Lab computes every
  aggregate from the collected traces at bundle/publish time (`store.publish`
  already recomputes) — a runtime cannot hand Lab a result to render.
- **Strict trace ingestion.** A trial only reaches `completed` with a schema- **and**
  semantics-conformant `trace/v1` (`validate_artifact` + `trace_semantics`), bound to
  the assigned `TrialUnit` when the plan named one; the attempt is frozen with its
  content-addressed `trace_ref`. Only a *planned* trial id may be driven (fail-closed).
  A `failed` trial needs typed failure details.
- **Immutable attempts + audit history.** A finished attempt cannot be re-completed
  or streamed into; a re-run is an explicit `POST /runs/{id}/trials/{id}/retry` that
  opens a superseding `TrialAttempt` and **keeps the prior attempt** in the history
  (no destructive replace). Terminal run states (`completed`/`failed`/`cancelled`)
  reject further ingest.
- **Idempotent event batches.** An `Idempotency-Key`/`batch_id` makes a re-delivered
  event batch a no-op, so a network retry cannot duplicate a ledger.
- **Fail-closed planner.** `plan_experiment` rejects an empty scenario/condition
  matrix or `repeats < 1` instead of inventing plausible identifiers.
- **Damaged acceptance is flagged, not re-minted.** A persisted acceptance that fails
  verification under a known key is marked `acceptance_status=invalid` and hidden
  from the catalog; the server no longer silently mints a fresh clean receipt over
  the tampering. An operator re-verifies / re-publishes to clear it.

**Provider ports — Lab owns the seam, standalone-first** (review v0.3-3,
`lab_server/providers.py`): the earlier framing ("move the registry/ingest to CP")
was wrong — Axor Lab is a self-contained product that must run fully **without**
Control Plane. So Lab *owns the ports* and ships standalone implementations; an
integrated deployment injects CP-backed implementations of the SAME ports. The
domain (experiment assignment, run lifecycle, trace validation, immutable attempts,
Results, EvidenceCase) never knows which is wired.

  Port (Lab-owned)     Standalone impl (this repo)     Status
  RuntimeRegistry      InMemoryRuntimeRegistry         WIRED (injected, swap-proved)
  TraceStore           LabTraceStore                   WIRED (injected, swap-proved)
  TraceIngest          RuntimeJobStore (strict)        declared — adapter pending
  ArtifactStore        PublicationStore                declared — adapter pending
  PromotionBackend     cp_export (portable package)    declared — adapter pending

Honest status (review v0.3-ports): only `RuntimeRegistry` and `TraceStore` are real
seams today — injected into `RuntimeJobStore`, swap-proved by tests. The other three
are declared contracts naming the target shape; the standalone code that plays each
role does not yet expose exactly that interface, so a thin conforming adapter is
still pending. Documented so the boundary is explicit, not because the swap works.

`RuntimeJobStore(registry=…, trace_store=…)` takes both providers by injection and
defaults to Lab's standalone ones — a fully working, CP-free deployment. `RuntimeRef`
+ credentials live behind the `RuntimeRegistry` port; accepted trace bodies live in
the `TraceStore` (the attempt keeps only a content-addressed `trace_ref`). Both are
proved swappable by test (a shared registry serving two stores; an injected custom
`TraceStore` receiving every accepted trace). `architecture-boundary.md` is corrected
to the provider-interface framing: *Lab consumes an Axor trace fabric through a
provider interface; standalone Lab supplies it, integrated Lab may share the CP
fabric; a user connects an agent once per deployment, not necessarily through CP.*

Both promotion directions are first-class, not competing: standalone Lab →
portable verified package (`cp_export`) → CP import; integrated Lab → shared
artifact refs → promote. `cp_export` stays as the standalone `PromotionBackend`.

**Standalone experiment→Results vertical slice closed** (review v0.3 blockers 1–4,
`lab_server/runtime_jobs.py`): the connected-runtime path is now product-complete on
its own, not just a strict-ingest prototype.

- **Server-owned plan + mandatory binding (P0-1).** `plan_experiment` emits immutable
  `TrialUnit`s with the full coordinate; `POST /experiments/plan` stores them under a
  `plan_ref`. `POST /runs` takes a `plan_ref` (or the experiment) — never a
  client-supplied trial-id list — stamps the run's own `run_id` into each unit, and
  rejects a duplicate trial_id. `claim` hands the runtime the stamped coordinates;
  `complete` requires the trace's `trial` block to equal the assigned unit EXACTLY.
  Binding now holds on the ordinary path, not only an optional special case.
- **Lab builds Results (P0-2).** When every planned trial is terminal the run passes
  through `analyzing`: Lab evaluates each scenario's `violation` predicate against the
  traces it collected and computes ASR aggregates ITSELF, then completes.
  `GET /runs/{id}/results` serves those Lab-computed aggregates — the runtime supplies
  traces, never numbers. (The upload-aggregate endpoint stays gone.)
- **Idempotent completion (P1-3).** Re-delivering the SAME terminal result (a lost
  HTTP response) returns the prior success with `idempotent: true`, even on a terminal
  run; only a DIFFERENT status/trace is a 409. A genuine re-run is an explicit retry.
- **Control-owned retry (P1-4).** `retry_trial` is a control action (the `/runs/…/retry`
  route requires the control token); a runtime can only `request_retry`
  (`/runtime/jobs/…/retry-request`, advisory, no state change). A runtime can no longer
  restart its own experiments, invalidate shown Results, or run up model cost.
- **Provider + acceptance hardening.** `LabTraceStore` owns addressing (`put(trace)→ref`,
  immutable byte copies, integrity error on a colliding ref); `/results` never serves a
  null trace; a missing/malformed/invalid persisted acceptance is flagged with a
  distinct `acceptance-status/v1` envelope and blocked on direct routes, not just the
  catalog.

## Pending

- **CP-backed provider implementations** (review v0.3-3, cross-repo, OPTIONAL): a
  `ControlPlaneRuntimeRegistry` + shared trace-fabric `TraceStore` + shared-ref
  `PromotionBackend` in `axor-control-plane`, injected into Lab for integrated
  deployments. The ports are ready; this is an integration capability, not a
  precondition for Lab.
- **Conforming adapters for the three declared ports** (review v0.3-ports): thin
  `TraceIngest` / `ArtifactStore` / `PromotionBackend` adapters over `RuntimeJobStore`
  / `PublicationStore` / `cp_export`, so those seams are swappable in fact, not only
  declared.
- **Persistent standalone providers**: `LabTraceStore` / registry are in-memory; a
  durable on-disk implementation (still Lab-owned, still CP-free) for a long-running
  standalone Lab.
- **Move provider I/O out of the run lock** (review v0.3-lock, P2): `registry` /
  `trace_store` calls run under the store's lock — fine for in-memory standalone, but
  a CP-backed HTTP/DB provider would block all runs during network I/O; lift external
  I/O out of the critical section (or move to an async application service) before CP
  integration.
- **Runtime leases + cancellation** (review v0.3-lease): `lease_expires_at` /
  heartbeat / reclaim / `runtime disconnected` on the `RuntimeRegistry` port, so a
  dropped runtime mid-run is a first-class failure case (lifecycle.md names it).
- **Shared-ref promotion** (review v0.3-promote): an integrated `PromotionBackend`
  that promotes by shared `policy_ref` / `regression_refs`; the standalone
  `cp_export` portable package stays as the CP-free path (both are first-class).
- **Full lifecycle/domain re-model**: the four lifecycles (demo / connected_runtime
  / trace_import / offline_runner) are documented in `lifecycle.md`; the store drives
  the `connected_runtime` path end-to-end, and the other three plus durable,
  per-tenant persistence are the remaining extensions.
