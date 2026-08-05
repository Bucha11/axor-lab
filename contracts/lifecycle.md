# Axor Lab — Run Lifecycle (v2, Suite Platform)

A **Run** is one execution of a **Suite**. Its lifecycle is determined by where the trials' traces come from — the *input mode* — not by whether governance is involved. A single-arm run with no kernel and a two-arm governed comparison walk the same states; only the trial count and what a trial carries differ.

Lab assigns and reads. **Lab never executes the agent** (architecture-boundary.md); its own states are assignment, ingestion and analysis, never tool dispatch.

## Input modes

Four, one per `TraceSource` in domain-model.md. There is deliberately **no remote-endpoint mode** — Lab never calls an agent out (INTEGRATION_PLAN §4.6), so a fifth lifecycle for it must not appear here later by accident.

| Mode | `TraceSource` | Who runs the agent | Lifecycle |
|---|---|---|---|
| Simulated environment | `demo` | Lab's own simulator, no agent | `validating → queued → running → analyzing → completed` |
| Live agent runtime | `runtime` | a connected Axor runtime, beside the agent | `validating → waiting_for_runtime → running → receiving_traces → analyzing → completed` |
| Uploaded traces | `import` | already ran, elsewhere | `validating → importing → replaying → analyzing → completed` |
| Recorded bundle / offline runner | `offline_runner` | a local CLI run, uploaded afterwards | `validating → waiting_for_upload → analyzing → completed` |

Terminal for all four: `completed | failed | cancelled`, then optionally `published`.

`awaiting_confirmation` sits between `validating` and run start, carrying the trial-count/cost estimate the user confirms. It is skipped when the caller did not ask for confirmation.

### Implemented today

`lab_server/runtime_jobs.py` implements `awaiting_confirmation`, `waiting_for_runtime`, `running`, `receiving_traces`, `analyzing`, `completed`, `failed` — the live-runtime lifecycle. `validating`, `queued`, `importing`, `replaying`, `waiting_for_upload`, `cancelled` and `published` are contract states with no server implementation yet; they arrive with Phase 3. This table is the target, and it says which half is real so nobody reads it as a description of the server.

## Two lifecycles that are not Runs

**Playground — one trial (RFC §13).** A single trial executed for inspection, not for statistics: no repeats, no aggregation, no artifact. `validating → running → completed | failed`. It produces a Trace and a Trial record and nothing else; a Playground trial must never be counted into a Run's aggregates, or a debugging session silently becomes evidence.

**Regression check.** Re-running a pinned invariant against a target is not a Run either — it consumes existing trials or replays a frozen trace. `checking → passed | failed | error`. `error` is a distinct terminal state and never collapses into `failed`: an invariant that could not be evaluated (an absent metric, an unresolvable trace) has not been satisfied and has not been violated, and reporting either one is a lie.

## Rules

- **Validation is pre-run.** Schema check, predicates type-check, `$inputs` resolve, tools resolve against manifests, estimate shown, privacy stated. A Suite that cannot execute never starts.
- **Conditions are optional.** A Suite with no `execution.conditions` runs single-arm: one trial per (scenario × seed × repeat), no kernel resolved, no verdicts. This is the common case and the default; the governed comparison is the opt-in.
- **Lab never executes the agent.** In `runtime` mode, `running` means the runtime claimed the assignment and is executing locally; `receiving_traces` means Lab is ingesting its events.
- **Every trial records metrics.** `duration_ms`, `steps`, `tokens_in`, `tokens_out`, `cost_usd`, plus any suite-defined key. An **unmeasured** metric is absent from the record, never `0` — a run that never priced anything has no `cost_usd`, and a regression bounding one must report `error`, not `pass`.
- **Cancel** keeps completed trials. **Retry** targets only the failed subset.
- **Failure is staged**: unknown-tool predicate (`validating`), no runtime available (`waiting_for_runtime`), runtime dropped mid-run (`running`), malformed/incomplete trace (`analyzing` — excluded and flagged).
- **Partial results are not automatically valid.** If failures are non-random the aggregate is flagged potentially-biased, with denominator and missing count (statistics.md §5).
- **Idempotency via TrialAttempt.** A retried trial is a new attempt that *supersedes* the failed one (audit history preserved), never a silent duplicate and never a destructive replace.
- **`completed` produces an Artifact.** `artifact/v1` wrapping the run's `bundle/v1` body verbatim, plus suite identity, agent identity, evidence cases, regressions and reproduce instructions. A Run that finished but emitted no artifact has nothing portable to show, which is the whole output of the platform.

## Governance capability

A Suite that declared the `governance` capability adds no states. What it adds inside `analyzing`: verdict replay over each frozen trace, the paired ungoverned/governed comparison, and the Control Plane handoff (control-plane-handoff.md). If any of that fails the Run still reaches `completed` with the failure recorded per trial — a replay mismatch is a finding, not a run failure.
