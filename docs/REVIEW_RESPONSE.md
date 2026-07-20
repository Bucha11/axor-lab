# Response to the static code review

This tracks the static review section-by-section: what was fixed (with the
test that proves it) and what remains genuinely large. The whole suite is
stdlib-only and green (`python -m unittest discover -s tests -t .`); optional
paths (axor-core, PyNaCl, Anthropic SDK) skip cleanly when their dependency is
absent.

## Fixed

| Review item | Fix | Proof |
|---|---|---|
| **P0.1** replay from truncated preview | ledger stores typed `decision_value` + `canonical_value_hash` apart from the UI `preview`; replay + predicates read `decision_value` | `test_replay_value_fidelity.py` (long strings, structured/list/None args) |
| **P0.2** Lab reimplements the kernel | `lab_runner/axor_backend.py` drives the real `axor_core.governor.ToolCallGovernor`; selected when a condition pins the installed version exactly | `test_real_kernel.py` (real DENY/ALLOW, bit-identical replay) |
| **P0.3** signature covers only content_hashes | `content_hashes` spans every field; signature covers the whole canonical bundle minus `signature` | `test_hardening.py::TestFullBundleIntegrity` |
| **P0.4** path traversal via `trace_id` | trace files named by server content hash; atomic writes; verify-on-load | `test_server_security.py::TestPathTraversal` |
| **P0.5** unauthenticated writes / unlisted leak | bearer-token write/admin auth; `catalog()` public-only; private never served | `test_server_security.py` |
| **P0.6** endpoint fail-open on bad provenance | kernel fails **closed** on an unresolvable egress driving arg; gateway rejects unknown/duplicate value ids | `test_later_tier.py::TestGatewayFailClosed` |
| **§3.1** validator misses constraints | `minimum/maximum/minItems/maxItems/minLength` enforced | `test_contract_parity.py::TestValidatorConstraints` |
| **§3.2** schema-valid but runtime-invalid predicates | author-time rejects non-tool_call events, result/output addresses, `count` | `test_contract_parity.py::TestAuthorTimeMatchesRuntime` |
| **§3.3** no predicate type-checking | matchers type-checked against the tool `args_schema` | `test_contract_parity.py::TestPredicateTypeChecking` |
| **§3.4** undiscriminated trace events | `allOf`/`if`/`then` require each event variant's fields | `test_contract_parity.py::TestDiscriminatedTraceEvents` |
| **§4.2** provenance never scoped | `mint_model_extraction(context_value_ids=…)` joins only the call's context | `test_runner_correctness.py::TestProvenanceScoping` |
| **§4.3** retry discards history | replaced attempts kept in `superseded` | `test_runner_correctness.py` |
| **§4.4** one bad trial aborts the run | per-trial capture → `status=failed`, feeds missingness | `test_runner_correctness.py::TestFailureCaptureAndHistory` |
| **§4.5** inline manifests ignored | inline manifests validated + registered with `$ref`s | `test_runner_correctness.py::TestInlineManifests` |
| **§4.6** wrong config_hash passes | verified on every resolve | `test_contract_parity.py::TestConfigHashVerifiedOnResolve` |
| **§5.1** replay single pending call | per-node FIFO intent queues | `test_replay_regression_robustness.py::TestReplayMultiCall` |
| **§5.2** "bit-identical" overclaim | documented as verdict-core + cross-machine byte-identity | `ReplayReport` docstring |
| **§5.3** regression no hash/miss guard | verifies pinned trace hash, reports missing/tampered, ordered sequence | `test_replay_regression_robustness.py::TestRegressionRobustness` |
| **§6.1** bundle records "scripted" always | records the actual agent + experiment id | CLI `_environment` |
| **§6.2** JCS approximate | documented as `axor-jcs` (RFC8785 on float-free JSON), byte-identical to `axor_core.kernel.canonicalize`, pinned by golden vectors | `test_canonicalization_vectors.py` |
| **§6.3** 32-bit publication id | 128-bit id + collision check + idempotent re-publish | `test_hardening.py::TestPublicationIdAndAttestations` |
| **§6.4** unverified reproduction counter | dedup by (attester, kind, pub); verify signature vs known key | `test_hardening.py` |
| **§7.1–7.3** thread-safety / atomic / verify-on-load | RLock, temp+rename, verify_bundle on load | `test_server_security.py` |
| **§7.4** raw PII in trace/preview | `sensitive_fields` → redacted preview, omitted `decision_value` | `test_runner_correctness.py::TestSensitiveFieldRedaction` |
| **§8** endpoint auth/ids/quotas | bearer token, unpredictable ids + per-run secret, run/event quotas | `test_later_tier.py::TestInstrumentedGateway` |
| **§9** sandbox/games over-claim | maturity table + explicit experimental docstrings ("not a security boundary", "toy model not proof") | README, package docstrings |
| **§11** no lint / packaging test / status drift | ruff CI job, wheel-install CI job, test counts de-hardcoded | `.github/workflows/ci.yml` |
| **§12** wheel not self-contained | schemas ship as package data (`importlib.resources`), drift-guarded | `test_packaging.py` |
| **§17** subsystems presented as equal | per-area maturity table | README |

## Remaining (genuinely large — not claimed done)

- **§4.1 generic runner.** The runner is still banking-slice-shaped (first
  read → first sink, `recipient`/`amount`). A general multi-tool, model-driven
  agent loop is a substantial rewrite. The BYOK `DrivingAgent` seam and scoped
  provenance are the groundwork; the loop itself is future work.
- **§13 typed models.** The codebase uses `dict[str, object]` at contract
  boundaries by design (stdlib-only, schema-checked). Migrating to
  dataclasses/discriminated unions everywhere is a large, separate effort.
- **§6.1 full self-containment.** The bundle embeds the actual agent + kernel;
  embedding the whole `experiment`/`AgentArtifact` needs a bundle schema
  addition, deferred.
- **Hosted SaaS** — Postgres, object storage, OAuth, workspaces/RBAC/billing,
  the web frontend — and the **real gVisor/Firecracker isolation runtime**
  remain infrastructure, not contract code. Marked design-only/experimental in
  the maturity table.

The most load-bearing review points — replay correctness, the real kernel,
full-bundle signing, path traversal, fail-closed governance, packaging, and
honest positioning — are resolved.

## Second review round

The second pass found concrete bugs (not just architecture) that broke the
multi-scenario benchmark path. Fixed in six ordered patches, each with a
proving test; the whole suite stays stdlib-only and green.

| Round-2 finding | Fix | Proof |
|---|---|---|
| **P0** multi-scenario traces collide (trace_id omits scenario_id) → bundle manifest + on-disk files overwrite, roundtrip loses 24/36 trials | trace_id carries the full trial coordinate; trace files named by content hash; atomic write; clean-on-overwrite; schema-validate on read | `test_multiscenario_bundle.py` (36 distinct traces survive build→write→read→verify→replay) |
| **P0** replay accepts an incomplete trace as bit-identical (no leftover-pending check) | `replay_trace_status` → MATCH / MISMATCH / MALFORMED_TRACE / UNSUPPORTED_KERNEL; call_id correlation end to end; leftover/duplicate/orphan decisions ⇒ malformed, never reproduced | `test_replay_regression_robustness.py::TestMalformedTraceIsNotReproduced` |
| **P0** predicate scores a decision-less intent as executed (fail-open ALLOW default) | an intent counts as executed only with an explicit ALLOW decision (call_id-paired); also `count` cardinality honored, invalid regex → predicate error, bool≠number | `test_predicate_evaluator.py` |
| **P0-hosted** server mints "statistically reproducible" from uploaded aggregates without recomputing | server recomputes every aggregate from trials+traces+predicates and rejects mismatches; `statistics_integrity` axis (self_reported / recomputed_from_traces); honest "trials (scripted agent)" wording | `test_hosted_stats_integrity.py` (fabricated 10^6-trial bundle rejected) |
| **P0/P1** import-incident reconstructs the condition (loses enforcement/policy/config_hash) | `--condition` required + used verbatim; full schema/semantic/cross-ref/config-hash validation; replay before write | `test_import_incident.py` |
| **P1** resolve() KeyErrors on a schema-invalid scenario; duplicate ids silently overwrite | two-stage (semantic validation only on schema-valid scenarios); duplicate manifest/scenario ids are errors | `test_validation_pipeline.py` |
| **P1/P2** sandbox output cap buffers gigabytes in the parent; stderr uncapped; descendants orphaned | streaming reads with a process-group kill at the cap, combined stdout+stderr, isolated temp cwd + cleanup, `start_new_session` | `test_later_tier.py` (memory-bounded flood, stderr cap) |
| **P1** CLI leaks tracebacks from BYOK/analysis/contract errors | main() maps AgentError / AnalysisError / ContractsError to stable exit codes | CLI error handling |
| **P2** entitlement `allows_nodes` ignores expiry/module | requires a non-expired license with the module, not just the ceiling | `test_entitlement.py::TestNodeCeilingRespectsExpiryAndModule` |
| **CI** signing tests silently skip (PyNaCl never installed) | dedicated `crypto` CI job installs PyNaCl and runs them | `.github/workflows/ci.yml` |

Still deferred (unchanged from round 1): the generic multi-tool runner loop,
end-to-end typed models, the hosted SaaS surface, and the real
gVisor/Firecracker isolation runtime.

## Third review round — evidence-graph integrity

The third pass audited the ARROWS between artifacts (Trial → Trace →
Aggregate → Claim → Export), not individual JSON validity. Five ordered
patches, each with a proving test; suite green throughout.

| Round-3 finding | Fix | Proof |
|---|---|---|
| **P0** a Trial is not bound to its Trace — one trace could back many fabricated trials, or a trial could cite a trace from another scenario/condition | `verify_bundle` verifies the evidence graph: completed-trial↔trace binding on every coordinate, one-to-one (no reuse, no orphans), and uniqueness of all display ids | `test_bundle_graph_integrity.py` |
| **P0** CP export ships the first enforcing condition while the earned bridge measured a different one | explicit `--condition`; ambiguity error when several enforce; earned bridge computed for the SELECTED condition; source records baseline + supporting aggregate refs | `test_cp_export.py::TestMultiConditionExport` |
| **P0/P1** earned_bridge hardcoded the literal baseline id `ungoverned` | baseline resolved by role (`enforcement == off`) | `test_cp_export.py` |
| **P0** endpoint gateway races (shared run state, no locks) → duplicate ids, duplicate seq, intent-before-value fail-open, mid-write trace read | global + per-run locks; atomic seq; `expected_seq` → 409; finalize-before-read; negative/oversized Content-Length → 400/413 | `test_gateway_concurrency.py` |
| **P1** server invents the DENY reason ("untrusted_derived") regardless of the real gate | the claim is built from the recorded decision (gate, driving value, labels, reason); no causal invention | `test_evidence_rendering.py` |
| **P1** EvidenceCase replays under an arbitrary first enforcing condition | replay under the trace's own / `?policy=` condition; a replay link per enforcing condition; the condition + config_hash shown | `test_evidence_rendering.py` |
| **P1** methodology always says "conditions differ only in enforcement"; HTML always prints "No exact claims" | real per-condition diff table; `extend()/append()` branch fixed | `test_evidence_rendering.py` |
| **P1** run/trial/trace identity ignores the agent — different `--agent` looked like retries | run_id folds in the agent content fingerprint; trial_id is scoped to the run | `test_run_identity.py` |
| **P1** validator accepts any value for a `null`-typed field; unknown type matches everything | `null` → `is None`; unknown type → no match | `test_contract_validator_types.py` |

Acknowledged, deferred by design: publication identity is currently the
128-bit bundle-hash id with idempotent re-publish (round 1); the round-3
suggestion to split artifact identity (full bundle hash) from publication
identity (UUID) so one bundle can carry several distinct publications is a
data-model change, not a correctness fix, and is left for the hosted surface.

## Fourth review round — methodological validity

The fourth pass asked whether a published artifact actually PROVES its claimed
conclusion — especially for live BYOK runs. Four ordered patches; suite green.

| Round-4 finding | Fix | Proof |
|---|---|---|
| **P0** McNemar (a paired test) was applied to live-model runs whose "pairs" are nominal (each condition sampled independently, no shared seed) — a spurious paired p-value | comparison_design is first-class: matched_pairs (McNemar) is used ONLY for a deterministic agent; a live model uses a two-proportion independent-samples test, marked exploratory; a declared matched_pairs with a non-deterministic agent is rejected | `test_comparison_design.py` |
| **P1** stats engine trusted its inputs | wilson (0≤successes≤n), mcnemar (non-negative), bootstrap (positive resamples, finite), binary_aggregate rejects a test whose paired_n exceeds n | `test_comparison_design.py::TestStatsInputValidation` |
| **P0** a kernel behavior flag (taint_floor) changed verdicts without changing the version/config identity | Kernel.behavior_version encodes behavior-changing flags; regression reports the fingerprint — same version + different gate is a different identity | `test_replay_regression_robustness.py::TestKernelBehaviorIsPartOfIdentity` |
| **P0** policy fields entered the config hash but were never executed (profile/trust_model/criticality_overrides); run_mode and type were ignored | resolve() rejects a policy field the reference kernel doesn't execute; run_mode selects which conditions run; type=game is rejected by the benchmark runner | `test_runtime_parity.py` |
| **P1** visibility default mismatch (local unlisted vs hosted public); one publish could silently world-list | server + app default to unlisted; CLI `--visibility` (public must be explicit) with a NOTE | `test_visibility_and_crypto.py` |
| **P1** a signed publish without PyNaCl 500'd (SignatureUnavailable uncaught) | caught → clean PublishRejected | `test_visibility_and_crypto.py` |

Still deferred (documented, not claimed done): aggregate content-hash identity
with scope, fully typed claim assertions (text rendered from data), a scenario
execution plan binding the injection source tool to the executed path,
persisted signature/author on the publication with re-verification on load,
strict verified-vs-submitted attestation counting, a downloadable reproduction
bundle endpoint, an agent-factory protocol for per-trial isolation, a
schema-set digest in the bundle environment, and the full entitlement packaging
(SSO/RBAC tiers). The load-bearing methodological blocker — never publishing a
paired significance claim for an independently-sampled live run — is closed.

## Sixth review round — execution-semantics integrity

This pass targeted the gap between what the contracts describe and what the
runtime executes (simulated ≠ modelled, ALLOW ≠ executed, redacted source ≠
redacted derived, schema-defined ≠ runtime-validated). Five ordered patches; the
most urgent was a secret re-appearing through model output. Suite green.

| Round-6 finding | Fix | Proof |
|---|---|---|
| **P0 (most urgent)** a `sensitive` source was redacted, but the model copying the secret into a sink arg produced a derived value carrying only `untrusted_derived` + the raw preview/decision_value — the secret re-appeared in the clear | the conservative join covers the whole security-label lattice: a derived value inherits `sensitive` from any context value and is redacted like a sensitive source | `test_sensitive_propagation.py` |
| **P0** an allowlisted first driving arg returned ALLOW without checking the other driving args (a tainted `body` unexamined); empty `driving_args` on an egress sink fell through to ALLOW | the gate checks EVERY driving arg (per-arg supersession); an egress sink with no driving_args fails closed | `test_multi_driving_gate.py` |
| **P0** the simulator declared any side-effecting tool "simulated" (noop_stub), ignoring `simulation.supported`, the adapter, and args_schema | execute() validates args_schema and requires a simulatable manifest (supported + known adapter); fixtures validate against result_schema at resolve; the BYOK agent rejects malformed model calls instead of coercing | `test_simulator_contract.py` |
| **P1** `count` was evaluated at runtime but the validator still forbade it; duplicate condition ids and conflicting inline manifests were accepted; regress used the reference kernel even for a real-kernel pin; local vs server DENY claims diverged | validator accepts count (rejects malformed); duplicate condition id + conflicting inline manifest rejected; regress routes through resolve_kernel; one shared `deny_claim_text` for CLI and server | `test_contract_parity*.py` |
| **P1** the real-kernel CI job swallowed a failed install and went green on skipped tests; a token-protected server was unreachable with the CLI | CI installs without swallowing + asserts `import axor_core`; CLI `publish --token-env/--author/--signature-file` (token from the env, not argv) | `test_secure_publish_cli.py`, ci.yml |

Still deferred (documented): explicit tool completion/started/failed events so
ALLOW is not equated with execution; an execution digest including the injection
text + manifests + agent; binding Trial↔Trace↔Environment↔Kernel with a run_id
on the trial; float cross-language canonicalization vectors (real JCS /
fixed-point statistics); persisted signature/author with re-verification on
load; strict verified-vs-submitted attestation counting; a downloadable
reproduction-bundle endpoint; an agent-factory protocol for per-trial isolation;
condition/model/order-aware missingness; and license-schema validation without
coercion — tracked for a later "Execution Semantics Integrity" milestone.
