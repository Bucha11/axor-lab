# Spec v0.3 conformance — status

The v0.3 spec is a deliberate **re-scoping** of Axor Lab down to the experiment /
evidence layer over Axor runtime traces (`contracts/architecture-boundary.md`, READ
FIRST). This file tracks how the repo is brought into line, phase by phase, so the
migration stays reviewable instead of one destructive sweep.

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

**Runtime registry seam extracted** (review v0.3-1, `lab_server/runtime_jobs.py`):
`RuntimeRef` + credentials (ingest keys) + status now live in a distinct, injectable
`RuntimeRegistry` — the *shared platform component* — not inside the Lab job store.
`RuntimeJobStore(registry=…)` takes the registry by injection, so one registry
instance backs a Lab store and (once wired) the Control Plane; Lab only *selects* an
already-registered `runtime_ref` and never mints or holds the credential. A runtime
connected once against a shared registry is selectable by any store sharing it
(proved by test). `/runtimes/connect` is now a thin passthrough to the registry, not
a Lab-owned concept. What remains is the CROSS-REPO wiring: a CP-backed
`RuntimeRegistry` implementation (and shared trace ingest) in `axor-control-plane` —
the seam is ready for it.

Still deferred to the platform layer (need shared Axor infrastructure / the other
repo): the **CP-backed registry + shared trace ingest** implementation, runtime
**leases / heartbeat / reclaim**, and **promote-via-refs** (a `POST /promotions` over
shared artifact refs rather than the offline `cp_export` package). Tracked below.

## Pending

- **CP-backed runtime registry + shared trace ingest** (review v0.3-1, cross-repo):
  the in-repo `RuntimeRegistry` seam is in place; implement a Control-Plane-backed
  registry (and shared telemetry ingest) in `axor-control-plane` and inject it, so a
  CP user's already-connected runtime is the one Lab assigns to. Lab keeps only
  `/experiments`, `/runs`, `/runs/{id}/confirm`, `/runs/{id}/results` and selects.
- **Runtime leases + cancellation** (review v0.3-lease): `lease_expires_at` /
  heartbeat / reclaim / `runtime disconnected`, so a dropped runtime mid-run is a
  first-class failure case (lifecycle.md already names it).
- **Promote, not export** (review v0.3-promote): a hosted `POST /promotions`
  referencing shared `policy_ref` / `regression_refs`; `lab_runner/cp_export.py`
  stays as the offline/self-hosted portability tool, not the SaaS path.
- **Full lifecycle/domain re-model**: the four lifecycles (demo / connected_runtime
  / trace_import / offline_runner) are documented in `lifecycle.md`; the store drives
  the `connected_runtime` path end-to-end, and the other three plus durable,
  per-tenant persistence are the remaining extensions.
