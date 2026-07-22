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

## Pending

- **Phase 3 — collapse the acceptance machinery.** Reduce `lab_server` acceptance
  to *publication + bundle hash + optional signature + reproduction records*; remove
  the `reacceptance/v1`, `acceptance-history/`, tombstone-chain and quarantine/re-root
  logic (v0.3 defers it).
- **Runtime-jobs execution API.** Build `GET /runtime/jobs`, `/claim`,
  `/trials/{id}/events`, `/complete` (Lab assigns, runtime executes). Not yet
  implemented; the local runner remains the offline/CI path.
- **Lifecycle/domain code re-model.** `RuntimeRef` / `AgentRef` / `TraceSource` /
  `AgentSnapshot`; the four trace-source lifecycles; `TrialAttempt` supersede-
  idempotency and the `ready/awaiting_confirmation` state.
