# Spec v0.3 conformance — status

This directory carries the **new v0.3 spec bundle** (`spec-lab.md`, `formats/`,
`mocks/`, and the business docs) plus the new contract docs that landed in
`contracts/` (`architecture-boundary.md`, `ui-backend-contract.md`) and the
`contracts/schemas/_shared_from_axor_core/` reference schemas.

The v0.3 spec is a deliberate **re-scoping** of Axor Lab, not an additive tuning.
It is captured here so the repo documents the target while the migration proceeds
in controlled, reviewable steps rather than one destructive sweep.

## Done (non-destructive, additive) — this pass

- New contract docs copied in: `contracts/architecture-boundary.md` (READ FIRST),
  `contracts/ui-backend-contract.md`.
- Spec narrative + authoring + business docs under `docs/spec-v0.3/`:
  `spec-lab.md`, `formats/bench-format.md`, `axor-packaging.md`,
  `cp-monetization.md`, `lab-economics.md`, `outreach-targets.md`, and the UI
  reference `mocks/*.jsx`.
- `contracts/schemas/_shared_from_axor_core/{trace,tool-manifest}.schema.json` —
  the axor-core-owned baselines, as **reference** (not yet the loaded schemas).
- `condition.schema.json` gains the thin-wrapper fields `kernel_ref`, `policy_ref`,
  `runtime_config_hash` (optional; the inline `kernel`/`policy` still validate), and
  the `$comment` records the thin-wrapper direction. Both schema copies stay
  byte-identical (packaging parity), the slice examples validate, and the full test
  suite stays green.

## Pending (architectural / destructive) — needs a deliberate go-ahead

Per `architecture-boundary.md`, v0.3 removes scope the repo currently implements and
hardened over rounds 16–21. These are **not** in this pass because each deletes
green, hardened code and reverts real correctness guarantees:

1. **Retire the Lab gateway.** `endpoint-protocol.md` is retired; `lab_endpoint/`
   (the synchronous `/runs` gateway, `run_secret`, `authoritative_args`,
   `/trace/ack`, retention/eviction) is out. Replace with the runtime-pull
   assignment API: `GET /runtime/jobs`, `/claim`, `/trials/{id}/events`,
   `/complete` — Lab assigns, the runtime executes.
2. **Drop the Lab-owned sandbox** (`lab_sandbox/`) and **multi-agent games**
   (`lab_games/`) — enterprise/later and deferred, respectively.
3. **Collapse the entitlement subsystem** (`lab_entitlement/`) into platform-level
   tiers (`workspace_tier`/`private_lab`/`control_plane`/`self_hosted_runner`, per
   `axor-packaging.md`).
4. **Defer the attestation / reacceptance / acceptance-history / tombstone chains**
   in `lab_server` down to "publication + bundle hash + optional signature +
   reproduction records" (v0.3 §"Removed / Deferred").
5. **Shared-schema ownership.** trace + tool-manifest are owned by axor-core; make
   `_shared_from_axor_core/` the loaded baseline (the second Lab trace schema is
   deleted). This collides with the repo's trace hardening (`call_id`,
   `decision_value`, event `allOf` discriminators, `driving_value_id: null` for a
   fail-closed DENY) — those must be re-expressed as an explicit Lab extension or
   dropped.
6. **Reconcile the r18–r21 bundle/experiment/publication fields** the v0.3
   `additionalProperties:false` schemas would invalidate (`experiment_design`,
   `config_provenance`, `execution_id`, `runtime_provenance`,
   `resolved_kernel_fingerprint`, the completed-trial `allOf`, `comparison_design`,
   `statistics_integrity`, …): keep as a Lab extension layer, or drop to match the
   spec verbatim. This is a product decision, not a mechanical merge — the v0.3
   `config_hash` = "CP carry-over key" wording also reverts the r19
   `parametric_config_hash` distinction.
7. **Lifecycle / domain re-model.** `AgentArtifact` → `RuntimeRef` / `AgentRef` /
   `TraceSource` / `AgentSnapshot`; backend-branched plans → the four trace-source
   lifecycles (`demo` / `connected_runtime` / `trace_import` / `offline_runner`)
   with `TrialAttempt` supersede-idempotency and a `ready/awaiting_confirmation`
   state.

Each pending item is a self-contained, reviewable change; several delete or rewrite
test suites. They should land as their own commits after their scope is confirmed.
