# Axor Lab — Run Lifecycle (v1)

The state machine branches by backend; a local run does not "provision a sandbox." Terminal states are explicit.

## Plans by backend

```
local:            validating → waiting_for_runner → running_local → uploading_artifacts → analyzing → completed
lab_template:     validating → queued → provisioning → running → analyzing → completed
trace_replay:     validating → replaying → analyzing → completed
instrumented_ep:  validating → connecting → running_remote → analyzing → completed
```

Terminal (all plans): `completed | failed | cancelled`, then optionally `published`.

## Rules
- **Validation is pre-run**: schema check, tool bindings resolve, predicates type-check, `$inputs` resolve, cost/trial estimate shown, privacy stated. A scenario that can't execute never reaches `queued`.
- **Cancel** keeps completed trials; nothing already run is lost.
- **Retry** can target only the failed subset (e.g. rate-limited trials), not the whole run.
- **Failure is specific and staged**: unknown-tool predicate (validating), missing key (provisioning/connecting), model timeout/429 (running — partial kept), agent crash (running), malformed trace (analyzing — excluded+flagged).
- **Partial results are NOT automatically valid**: if failures are non-random, the aggregate is flagged potentially-biased and shows denominator + missing count (statistics.md §5). The UI never silently computes over survivors.
- **Idempotency**: a retried trial with the same (scenario,condition,seed,repeat) replaces, never duplicates.
