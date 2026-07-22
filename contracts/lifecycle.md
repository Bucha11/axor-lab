# Axor Lab — Run Lifecycle (v1)

Four lifecycles by trace source (architecture-boundary.md). Lab assigns and reads; the runtime executes. Terminal states explicit.

```
demo:               validating → queued → running → analyzing → completed
connected_runtime:  validating → waiting_for_runtime → running → receiving_traces → analyzing → completed
trace_import:       validating → importing → replaying → analyzing → completed
offline_runner:     validating → waiting_for_upload → analyzing → completed
```

Terminal (all): `completed | failed | cancelled`, then optionally `published`.

`ready / awaiting_confirmation` sits between `validating` and run start, carrying the cost/trial estimate the user confirms.

## Rules
- **Validation is pre-run**: schema check, predicates type-check, `$inputs` resolve, estimate shown, privacy stated. A scenario that can't execute never starts.
- **Lab never executes the agent.** In `connected_runtime`, `running` means the runtime claimed the assignment and is executing locally; `receiving_traces` means Lab is ingesting its events. Lab's own states are assignment + ingestion + analysis, never tool dispatch.
- **Cancel** keeps completed trials. **Retry** targets only the failed subset.
- **Failure is staged**: unknown-tool predicate (validating), no runtime available (waiting_for_runtime), runtime dropped mid-run (running), malformed/incomplete trace (analyzing — excluded + flagged).
- **Partial results are not automatically valid**: if failures are non-random, the aggregate is flagged potentially-biased with denominator + missing count (statistics.md §5).
- **Idempotency via TrialAttempt**: a retried trial is a new attempt that *supersedes* the failed one (audit history preserved), never a silent duplicate or a destructive replace.
