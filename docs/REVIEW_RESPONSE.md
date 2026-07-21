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

## Seventh review round — the evidence trust boundary

Each subsystem had become more correct on its own, but the guarantees leaked at
the *seams* between them: a hosted server certified statistics it never checked,
private publications escaped through non-HTML routes, the real kernel crashed on
a redacted secret, one failed trial sank a whole analysis, and a "published"
record was not actually immutable. Six patches; the most urgent were hosted
statistical verification, the private/auth bypasses, and separating a sensitive
value's runtime form from its serialized form. Suite green (367 tests).

| Round-7 finding | Fix | Proof |
|---|---|---|
| **P0 (most urgent)** the publish handshake re-ran replay and re-hashed, but never recomputed the statistical *test* — a bundle could carry a fabricated McNemar p / an unknown "metric" and be certified `statistics_integrity: recomputed_from_traces` | a closed metric registry; the server recomputes every aggregate AND its test (McNemar discordants/p, two-proportion difference/p), rejects an unknown metric, and rejects `matched_pairs` on a live environment | `test_hosted_stats_test_verification.py` |
| **P0** a `private` publication was hidden only on the HTML page — the JSON API and EvidenceCase routes still served it; appending a reproduction attestation (a write) required no token | one `_readable` guard raises NotFound for `private` on every read route; the reproductions POST requires the write token; raw traces are schema-validated on publish | `test_hosted_trust_boundary.py` |
| **P0** redacting a `sensitive` value dropped its `decision_value`, so the real axor-core path — which read `decision_value` off the serialized dict — hit a KeyError and failed the whole trial | the ledger keeps the raw value in an in-memory `runtime_value` map (never serialized); the real kernel registers from that, so the secret stays out of the trace/bundle while the kernel still sees it | `test_sensitive_propagation.py` |
| **P0** one trial raising crashed the command after execution (`outcomes[trial_id]` KeyError on the failed trial) — no bundle, no missingness | analysis uses completed-only outcomes, `pairs()` skips failed trials, missingness is reported first (before aggregates), and a zero-data condition is handled | `test_failure_complete_analysis.py` |
| **P0** a "published" record was not immutable: re-publishing could silently overwrite metadata or wipe the attestation log, and a hand-edited `publication.json` was trusted on load | the id content-addresses the WHOLE publication body — re-publish is idempotent (identical) or a distinct publication (any change); load recomputes the id, so a disk-edited visibility/integrity/question/claims no longer matches and is dropped | `test_publication_immutable.py` |
| **P1** a predicate `count: {}` was a no-op tautology; the DENY claim named the first intent (wrong tool on a multi-call trace); a malformed `.axl` envelope raised a raw AttributeError; the auto run_id was a 32-bit slice | `count` requires min/max (non-negative) in schema + validator; the DENY claim correlates the tool by call_id; the envelope is type-checked before iteration; run_id widened to 128-bit | `test_patch26_hardening.py` |

Still deferred (documented, unchanged in spirit): an execution digest binding the
injection text + manifests + agent into the trial identity; a downloadable
reproduction-bundle endpoint; an agent-factory protocol for per-trial isolation;
condition/model/order-aware missingness; cross-language float canonicalization
vectors; and a signed, verified-vs-submitted attestation count — the seams are
now guarded, but the larger "reproduce-from-scratch" loop remains future work.

## Eighth review round — verify one value, allow another

The most dangerous finding this round was a live-enforcement bypass: the
instrumented gateway could check one value's provenance and let the tool run a
different concrete value. Around it sat a cluster of "trust the persisted/
self-reported thing" gaps — self-certified provenance fidelity, an unsigned
reproduction counter, a restart that skipped the publish handshake, a retry
model that orphaned traces, and load-bearing trace metadata that wasn't actually
bound to its bundle. Seven patches. Suite green (392 tests).

| Round-8 finding | Fix | Proof |
|---|---|---|
| **P0** the gateway took `arg_bindings` and a concrete `args` map independently — labels from the bound values, but the args the gate decided on straight from the client, and only bindings recorded. A clean binding + a malicious concrete arg → ALLOW on clean labels, tool runs the attacker value, replay reproduces the laundered ALLOW | `resolve_args(bindings, values)` is the single source of gated args (bound value's `decision_value`), shared by replay AND the gateway; a client `args` is an assertion checked by canonical hash; every decision-relevant arg must be bound; values must carry `decision_value` | `test_gateway_args_binding.py` |
| **P1** the gateway defaulted `labels_carried=True` and stamped `explicit_flow_tracked` — a governance claim built from labels an untrusted agent merely asserted (self-reported, not tracked) | `explicit_flow_tracked` requires the operator to construct the gateway as an attested `trusted_runtime` (default off); an untrusted client is always `heuristic_attribution` and can only downgrade | `test_gateway_provenance_honesty.py` |
| **P1** unsigned self-reports counted the same as verified reproductions, one "reproduced ×N" badge; on restart reproductions.json loaded raw (no re-check), so a hand-edit could forge `verified`/dupes/bad kinds | `verified` is EARNED only from a valid signature (never an input flag), the signature is retained; `rebuild_reproduction_log` re-verifies + re-dedups on every load; axes split verified/unverified; the badge counts only verified | `test_reproduction_verification.py` |
| **P1** `_load` at restart ran only hash + schema + content-address checks, never replay or aggregate recomputation — a from-scratch, hash-coherent publication (fabricated aggregates/claims/non-replaying decisions) placed in the store dir loaded as if it had passed the handshake | one shared `_semantic_errors` (replay must be bit-identical + aggregates must recompute) runs on publish AND load; load also re-MINTs the publication and refuses anything whose body isn't what the server would generate | `test_publication_immutable.py::test_forged_claims_publication_is_rejected_on_restart` |
| **P1** a stochastic retry left the prior trace in `traces` while the trial referenced the new one → verify_bundle rejected the orphan; a failed retry left a stale outcome + orphan too | both paths route through `_supersede`, which retires the prior attempt AND its trace into the audit log (outside the publishable bundle) and clears the stale outcome, so both attempts survive without orphaning evidence | `test_runner_correctness.py` (build+verify a real bundle after a retry) |
| **P1/P2** the graph verifier bound only trial coordinates; `producer.kernel_version` / `inputs_digest` / a global `environment.kernel_version` could disagree with the bundle and replay would still pass | verify_bundle now binds producer kernel to the condition's kernel, `inputs_digest` to the scenario inputs+fixtures, and requires `environment.kernel_version` to be one of the conditions' kernels | `test_trace_metadata_binding.py` |
| **P2** the publication server's `_read_json` crashed on a non-numeric Content-Length (500) and let a negative one bypass the cap (`read(-1)` hang) | ported the gateway's guards: non-numeric / negative Content-Length is a clean 400 | `test_content_length_hardening.py` |

Still deferred (documented): a cryptographic per-event envelope so an untrusted
multi-tenant gateway caller can earn `explicit_flow_tracked`; a server-signed
acceptance receipt so a store directory without one never loads (stronger than
re-mint-and-compare for a forged integrity badge); a first-class `attempts`
graph in the bundle contract; and hash-chained append-only attestation logs.

## Ninth review round — the second endpoint path + cold-load trust

Round 8 fixed the "verify one value, allow another" bypass in the HTTP gateway
but left the in-process `assemble_and_gate` path with the identical bug, and the
new metadata binding shipped a regression. This round finishes the endpoint
unification and closes the remaining cold-load trust holes. Suite green (406
tests).

| Round-9 finding | Fix | Proof |
|---|---|---|
| **P0** `assemble_and_gate` (the in-process SDK path) still took the concrete `item.args` independently of the bindings, passed them to the kernel, and recorded only bindings — the same laundering bypass r8 fixed in only ONE of the two endpoint paths; it also self-set `explicit_flow_tracked` from a bare boolean | extracted the rules into one shared `lab_endpoint/gating.py` (`gated_args` + `provenance_fidelity`) that BOTH the HTTP gateway and assemble_and_gate call, so they can't drift again; a clean binding + malicious concrete arg fails closed, fidelity needs an attested runtime | `test_instrumented_args_binding.py` |
| **P1 (regression)** the r8 `inputs_digest` verifier expected `hash(inputs+fixtures)` but both endpoint paths hashed inputs only, so a conformant instrumented trace for a fixture-bearing scenario FAILED verify_bundle; and a producer could drop the field to dodge the check | one `world_digest(inputs, fixtures)` in lab_contracts used by runner, gateway and instrumented SDK; verify_bundle REQUIRES inputs_digest for wrapped_code / instrumented_endpoint | `test_world_digest.py` |
| **P1** cold load re-minted with `integrity` taken from publication.json, so a from-scratch publication could claim `integrity: signed` (valid bundle, recomputed id) and load with an author-signed badge it never earned — replay/recompute pass and there was no signature to re-check | persist an author+signature `receipt.json` (signed pubs only); on load `_integrity_on_load` re-earns `signed` only if the receipt verifies against a known key, else hash_verified; re-mint folds integrity in, so a forged badge with no receipt is dropped | `test_publication_immutable.py` |
| **P1** a valid SIGNED attestation for publication A could be copied into B's reproductions.json — reload re-checked signature/kind/dedup but never bound the entry to THIS publication or re-ran schema validation | `rebuild_reproduction_log` takes `expected_publication_id` (drops a transplanted attestation) and schema-validates each entry (junk fields / bad kind rejected), stripping the server-computed `verified` first | `test_reproduction_verification.py` |
| **P0/P1 (honesty)** the gateway docstring claimed the gate "can stop a sink before it fires" — too strong: it's a decision point, not a tool executor, so enforcement depends on a cooperating caller, and an untrusted client can mislabel a value | the intent response returns `authoritative_args` (so a cooperating proxy runs the bound value), and the docstring states the trust model plainly — advisory for an untrusted client (hence heuristic_attribution), real enforcement needs a trusted runtime / signed envelope (roadmap); what IS unconditional is that the gate decides on the bound value | `test_gateway_args_binding.py` |
| **P2** superseded retry attempts lived only in the in-memory result — the CLI never persisted them, so the audit history vanished on exit | `axor-lab run` writes a `superseded_attempts.json` sidecar (each attempt with its trace), kept OUT of the publishable bundle so it can't orphan the graph | `test_superseded_persistence.py` |

Still deferred (documented): a trusted-runtime that mints labels from the tool
manifest / observed execution graph (so an untrusted gateway client can't get a
governance-grade verdict from self-reported labels), plus a cryptographic
per-event envelope; a server-signed acceptance receipt covering the WHOLE record
(stronger than the author-signature receipt for integrity); a first-class
`attempts` graph in the bundle contract; and hash-chained attestation logs.

## Tenth review round — contract-doc drift + complete tool calls + cold-load resilience

No new P0 bypass this round. The fixes: bring the endpoint CONTRACT into line
with the advisory-model code, make the authoritative call complete, harden
startup against a single corrupt file, and name the audit artifact honestly.
Suite green (410 tests).

| Round-10 finding | Fix | Proof |
|---|---|---|
| **P1** `contracts/endpoint-protocol.md` still described a system that doesn't exist — "governance-capable", a nonexistent `POST /runs/{id}/tools/{call_id}/result` dispatch route, "gateway dispatches the tool", SSE events, and `explicit_flow_tracked` "if the SDK carries labels" — while the README was honest | rewrote the protocol as the real advisory decision API: actual POST /events (intent → `{decision, authoritative_args}`) / finalize / trace routes; decision point, not executor; fidelity is heuristic by default, `explicit_flow_tracked` only under an attested `trusted_runtime`; roadmap options listed | `contracts/endpoint-protocol.md` |
| **P1** `authoritative_args` was only the decision-relevant args — for send_money (required recipient+amount, driving recipient) an ALLOW returned `{recipient}` and dropped the required `amount`, so a proxy had to top it up with an unbound, unrecorded value | `gated_args` now requires a binding for the union of decision-relevant args, `args_schema.required`, and every arg the caller will pass; `authoritative_args` is the complete executable call, each arg a bound ledger value | `test_gateway_args_binding.py::test_required_arg_must_be_bound_for_a_complete_call` |
| **P1/P2** `PublicationStore.__post_init__` called `_load` with no error isolation, and `_load` / `rebuild_reproduction_log` did unguarded `json.loads` + `.get()` on each entry — one `{broken` file or a non-object array element could crash the WHOLE catalog on startup | each directory load is isolated (quarantine-not-fatal); `rebuild_reproduction_log` skips non-dict entries; a corrupt reproductions/receipt file degrades gracefully instead of dropping the publication | `test_cold_load_resilience.py` |
| **P2** the superseded audit sidecar was a non-atomic `superseded_attempts.json` with no hash/schema — its name implied bundle-grade integrity | renamed to `superseded_attempts.unverified.json`, written atomically (temp + rename); a content-hashed, schema-validated attempts graph inside the bundle contract is the stronger follow-up | `test_superseded_persistence.py` |

Still deferred (documented): a trusted runtime that mints labels from the tool
manifest / observed execution graph (and/or a signed per-event envelope, and/or a
genuine server-side dispatch route) so an untrusted gateway client can earn a
governance-grade verdict; a server-signed acceptance receipt over the whole
record; and a first-class, content-hashed `attempts` graph in the bundle contract.

## Eleventh review round — sandbox / SSRF / cassette / license / cost

A pass over the newer subsystems. No new P0. Seven findings, all where a
component promised more than it delivered; each is now either enforced or
honestly named. Suite green (442 tests).

| Round-11 finding | Fix | Proof |
|---|---|---|
| **P1** the sandbox left forked descendants running: the finally block only killed the group when the MAIN process was still alive, so `subprocess.Popen(["sleep","3600"])` + exit 0 orphaned the sleep, unbounded by wall_seconds | capture pgid at spawn and ALWAYS sweep the whole process group on return (even after a clean exit) | `test_later_tier.py::...forked_descendant_does_not_outlive_the_run` |
| **P1** the output cap could be crossed while reporting completed/truncated=False — a boundary-crossing chunk dropped its overflow silently | mark capped when a chunk exceeds the remaining space; exactly cap+1 bytes is now OUTPUT_CAPPED | `test_later_tier.py::...output_one_byte_over_cap...` |
| **P1** `disk_mb` was `RLIMIT_FSIZE` (a per-FILE cap), not a disk quota, and only the workdir was cleaned — the name over-promised | renamed to `max_file_mb` with a docstring saying it is per-file only; README/test say a real disk cap needs fs/project quotas | `test_later_tier.py::test_single_file_size_is_capped` |
| **P1** `ssrf_check` only validated caller-supplied IP strings and was never wired into a fetcher — the validated IP and the actual connect were unrelated (rebinding), redirects unhandled | new `safe_open()` resolves DNS itself, validates every address, connects PINNED to a validated IP (no library re-resolution), keeps Host/SNI, and re-checks each redirect; `ssrf_check` documented as an address validator | `test_ssrf_safe_open.py` |
| **P1** the per-scenario cassette looked up the TASK TEXT, never a scenario_name key, so every scenario silently shared the first transcript | thread `scenario_id` through `decide_sink_call`; a dict cassette keys on it (then an explicit "default"), else raises — no silent collapse | `test_cassette_per_scenario.py` |
| **P1/P2** license validation coerced everything (`bool("false")` → True; unknown tier / negative ceiling / bogus "never" expiry accepted; errors leaked) | strict schema validation — JSON booleans only, tier enum, ceiling int ≥ 0, YYYY-MM-DD expiry, features list-of-strings — raising LicenseError; PyNaCl hint fixed to `[crypto]` | `test_license_validation.py` |
| **P1** the cost layer promised a hard ceiling but only printed an estimate | `CostBudget` (max_usd / max_input_tokens / max_output_tokens) enforced against ACTUAL usage between trials so the run stops before the next provider call; actual usage + spend recorded in the bundle environment; `--max-usd/--max-input-tokens/--max-output-tokens` CLI flags | `test_cost_ceiling.py` |

Still deferred (documented): full process-tree containment against a child that
calls `setsid()` itself, and a real total-disk quota — both need a cgroup PID/IO
scope or a namespace/container runtime; and a token-accurate cost model (the
ceiling uses the same rough price table as the estimate).

## Twelfth review round — false regressions, unproven claims, and mislabeled measures

This pass targeted the paths where an OFFICIAL, honest operator could still emit
a false result — a regression that isn't one, a "statistically reproducible"
claim the evidence doesn't prove, an EvidenceCase reasoning with the wrong
kernel, or a measure whose name overstated it. No new P0 bypass; twelve findings
(eleven P1, one P2), each enforced or honestly named. Suite green (464 tests).

| Round-12 finding | Fix | Proof |
|---|---|---|
| **P1** the CLI regression pin stored only the final verdict, so a multi-call trace's real sequence (ALLOW, ALLOW, DENY) was compared to a singleton (DENY) and cried regression on an unchanged trace/kernel | `pin()` records the whole ordered `expected_sequence`; the CLI persists it and `check_pins` compares sequences | `test_regression_pin_fidelity.py` |
| **P1** every pin replayed under the FIRST scenario's inputs, so pin B in a multi-scenario bundle ran against scenario A's allowlist/effect inputs → false regression or false pass | `check_pins(inputs_for=…)` supplies each trace's OWN scenario inputs; the CLI resolves per-trace | `test_regression_pin_fidelity.py` |
| **P1** local `publish` minted a `statistically_reproducible` claim from the bundle's self-reported aggregates without recomputing them — the exact fabrication the server rejects | local publish proves REPLAY only (`exactly_replayable`); it no longer asserts any statistical claim over self-reported numbers (that is the server's recompute) | `test_cli_e2e.py::test_publish_mints_a_schema_valid_typed_publication` |
| **P1** two local publications of the SAME bundle with different question/visibility got the same id (id addressed only part of the body) | id content-addresses the WHOLE publication body via shared `lab_contracts.derive_publication_id`, used by both the CLI and the server store | `test_cli_e2e.py::test_local_publication_id_content_addresses_the_whole_body` |
| **P1** an EvidenceCase for a real-axor-core trace silently reasoned with the reference kernel (`default_registry(...).get`) | CLI and server EvidenceCase build the kernel via `resolve_kernel`, exactly as replay/regress — the real governor when the condition pins the installed build | `test_evidence_rendering.py`, `test_server_e2e.py` |
| **P1** `_chain` paired the first intent with the first decision independently, so a DENY on call B rendered call A's tool/lineage in a multi-call trace | the chain targets the DENY decision and correlates ITS intent by call_id (fallback to the sole intent for legacy traces) | `test_evidence_rendering.py::TestChainCallIdCorrelation` |
| **P1** the server recomputed the matched-pairs marginal over the all-conditions intersection, so one failed baseline trial shrank every condition's n and the server REJECTED an honest runner bundle at missingness | the server marginal is the completed trials OF THAT CONDITION for both designs (mirroring the runner); pairing lives only in the test object | `test_hosted_stats_integrity.py::TestMatchedPairsParityAtMissingness` |
| **P1** `verify_bundle` never checked that a trial's scenario/condition exist or that a trace's tools have manifests — a failed trial could cite a phantom scenario/condition and a trace could invoke an unmanifested sink | `_verify_cross_references` resolves every trial coordinate and every event tool in-bundle | `test_bundle_graph_integrity.py::TestCrossReferenceIntegrity` |
| **P1** CP export carried regression pins verbatim — a pin for a trace not in the bundle, or asserting a sequence the trace never produced, shipped into a production config | `_validate_pins` binds each pin to a real bundle trace (id, content hash, completed-trial citation, verdict, recorded sequence) and carries the full validated shape | `test_cp_export.py` |
| **P1** CP export synthesized a `config_hash` (`get(..., recomputed)`) and presented it as "the config the researcher measured" | export requires a PRESENT recorded config_hash, verifies it, and emits the recorded value | `test_cp_export.py::test_export_requires_a_recorded_config_hash` |
| **P1** the cost ceiling was checked only between trials, so one trial's up-to-8 provider calls could overshoot before the check ran | the budget is consulted BEFORE the first trial and BEFORE every provider call inside the loop (a `CostCeilingReached` halts the whole run); limits ≤ 0 are rejected; the remaining output budget caps the next call's `max_tokens`; reached-vs-overshot distinguished | `test_cost_ceiling.py::TestWithinTrialGuard` |
| **P2** the federation model's `carried_taint` flag, `contained_at` field, and `compromised_spread` all had names that overstated or inverted what they measured | renamed to `governed`; replaced with `contained()` and `blast_radius()` (spread BEYOND the recorded origin compromise) | `test_later_tier.py::TestFederationAndPopulation` |

Still deferred (unchanged in spirit): the larger reproduce-from-scratch loop, a
cryptographic per-event envelope / trusted-runtime for governance-grade labels
from an untrusted gateway client, a server-signed acceptance receipt over the
whole record, a first-class content-hashed `attempts` graph in the bundle
contract, and a token-accurate cost model. The load-bearing round-12 goal — no
official path can emit a regression, statistical claim, or evidence view the
frozen evidence does not support — is closed.

## Thirteenth review round — real-kernel purity, takedown auth, and reproducibility

The most serious new finding was an auth bypass: a write-token holder could
resurrect a publication after an admin took it down. Around it: `--real-kernel`
broke the compare (and the bundle save), regression could bless a malformed
trace, the value-ledger admitted ambiguous/self-contradictory traces, floats
weren't RFC 8785, a cost stop wrecked the missingness denominator, and the
"reproduce" commands weren't reproducible. 17 findings (16 P1, 1 P2), each with a
proving test. Suite green: **496 tests**.

| Round-13 finding | Fix | Proof |
|---|---|---|
| **P1 sec** a taken-down publication could be re-published (id content-addresses the body) and re-enter the catalog until restart | publish() refuses a tombstoned id (409); catalog() filters tombstones | `test_hardening.py::test_write_token_cannot_resurrect_a_taken_down_publication` |
| **P1** `--real-kernel` repinned only enforcement-on conditions → the compare mixed a kernel change with the enforcement change, and the two-kernel bundle failed verify AFTER every paid trial ran | repin EVERY condition (baseline included; the real backend runs enforcement=off as observe-only); a mixed-kernel bundle omits the global kernel_version | `test_real_kernel.py::TestRealKernelRepin` |
| **P1** regression compared only the recomputed verdict SEQUENCE, so a MALFORMED trace whose sequence coincided with the pin reported a match | check_pins uses replay_trace_status; malformed/unsupported are distinct non-match statuses; the CLI exits 4 on any non-match | `test_regression_pin_fidelity.py::TestRegressionHonorsReplayStatus` |
| **P1** the CLI EvidenceCase picked the FIRST enforcing condition (wrong counterfactual for an allowlist trace) and `--twin` accepted any unrelated trace | one shared evidence_condition resolver (CLI+HTML) + `evidence --policy`; validate_twin requires same scenario/seed/repeat and an enforcing twin | `test_evidence_rendering.py` |
| **P1** a pin could assert a verdict the trace never produced (expected_verdict ≠ sequence[-1]) | pin() and CP `_validate_pins` require expected_verdict == the final recorded verdict | `test_regression_pin_fidelity.py::TestPinVerdictConsistency` |
| **P1** CP export carried pin hashes but not the frozen trace bytes — not portable | export-cp writes each pinned trace body under regression-traces/ | `test_cp_export.py::test_export_writes_frozen_pinned_trace_bodies` |
| **P1** trace_semantics deduped value_ids and never checked the hash / event order — an ambiguous or self-contradictory trace passed | value_id unique, canonical_value_hash present & consistent (only a sensitive value may omit decision_value), per-node seq strictly increasing, call_id unique per event type | `test_trace_ledger_integrity.py` |
| **P1** canonical_value_hash didn't have to match the decision_value | folded into the ledger checks above; endpoints derive an authoritative hash via normalize_value_hash | `test_trace_ledger_integrity.py` |
| **P1** seq was declared load-bearing but never enforced (replay trusted array order) | per-node strictly-increasing seq check makes seq-order == array-order | `test_trace_ledger_integrity.py` |
| **P1** floats were hashed with Python repr, not RFC 8785 — a cross-language verifier would compute a different bundle hash | canonical_json implements RFC 8785 §3.2.2.3 number serialization (ES Number::toString); float-free artifacts stay byte-identical to axor-core, which rejects floats | `test_canonicalization_vectors.py::test_floats_use_rfc8785_ecmascript_form_not_python_repr` |
| **P1** a cost stop dropped the never-run trials, so missingness reported e.g. n=1/1 for a 100-trial plan | the full plan is materialized; every not-run trial is recorded status=excluded (failure_reason=cost_ceiling) | `test_cost_ceiling.py::test_missingness_denominator_reflects_the_full_plan` |
| **P1** USD/input-token ceilings were post-call only — a 200k-token prompt against a 100-token budget still went out | CostBudget.pre_spend_exceeded reserves projected input + allowed output and refuses the call before the request | `test_cost_ceiling.py` |
| **P1** bundle overwrite did rmtree-then-replace — a crash between them destroyed the prior bundle | move the old dir to a backup (rename), swap in staging, fsync parent, then drop the backup; roll back on a failed swap | `test_multiscenario_bundle.py::test_a_failed_overwrite_preserves_the_old_bundle` |
| **P1** the page's reproduce commands weren't reproducible — no bundle download, no experiment.axl | GET /api/publications/{id}/bundle returns the package; `axor-lab replay` accepts a downloaded .json package; the page text is honest about the fresh-run gap | `test_server_e2e.py::test_bundle_download_route_is_replayable` |
| **P1** two independent live runs got identical ids (looked like retries) | _derive_run_id folds a random execution nonce for a nondeterministic agent; deterministic agents keep the content id | `test_run_identity.py::TestRunIdExecutionNonce` |
| **P1** a `[]`/mis-shaped POST body dropped the request thread with a 500 | _read_json requires a JSON object; both handlers map (KeyError/TypeError/ValueError/AttributeError) → 400 and everything else → an opaque 500 | `test_server_security.py` |
| **P1** a self-reported `explicit_flow_tracked` rendered as a sound chain with no warning | EvidenceCase emits fidelity={claimed, verified}; an unverifiable explicit claim downgrades to self_reported and still warns | `test_acceptance_04_evidence_case.py::test_self_reported_explicit_fidelity_is_not_presented_as_verified` |

Still deferred (documented): a full self-contained bundle that embeds the whole
experiment document (so a fresh live `run` is reproducible from the package), a
cryptographic trusted-runtime attestation that would let `explicit_flow_tracked`
be VERIFIED rather than self_reported, and a token-accurate cost model. The
load-bearing round-13 goals — admin takedown is final, `--real-kernel` measures a
clean single-kernel compare, no structurally broken trace passes as a regression,
and a published result is actually downloadable and replayable — are closed.

## Fourteenth review round — attestation honesty, gateway boundary, and portable proof

The most urgent item was the red acceptance CI on the already-merged PR #11.
The concrete cause: the only real failure was a flaky sandbox
forked-descendant test (`RLIMIT_NPROC` is per-real-UID, so a busy runner
could deny the fork); the second "failure" was `fail-fast` cancelling the
other Python leg, not an independent break. Both are fixed — the fork test
raises its own process ceiling, and the acceptance matrix reports each leg
independently — so "all green" is trustworthy again.

Around that, eight priority patches plus three P2s: the sampling design was
inferred rather than declared, the JCS implementation wasn't fully RFC 8785,
a fail-closed DENY couldn't be represented as valid evidence, the endpoint
gateway trusted request shape, a cost-stopped run lied about being complete,
and a downloaded publication couldn't be verified without trusting the
server. Each finding has a proving test. Suite green: **538 tests**.

| Round-14 finding | Fix | Proof |
|---|---|---|
| **CI** the acceptance matrix used `fail-fast`, so one leg's failure cancelled the other and masked the real cause; the sandbox fork test flaked on busy runners | `fail-fast: false` on the acceptance strategy; the fork test raises `max_processes` so the fork reliably succeeds | `.github/workflows/ci.yml`; `test_later_tier.py::test_forked_descendant_does_not_outlive_the_run` |
| **P0 sec** takedown removed only the exact publication id — the SAME bundle re-published under a different question/visibility minted a new id and re-entered the catalog | takedown records the evidence lineage (`bundle_ref`); publish() refuses any metadata over a taken-down bundle; survives reload | `test_hardening.py::test_takedown_follows_the_evidence_not_just_the_exact_id`, `::test_evidence_takedown_survives_a_reload` |
| **P0** the server inferred a deterministic (matched-pairs) design from an empty/unknown provider, and presented a paired p-value as if the pairing were attested | deterministic providers are an explicit allowlist (scripted/cassette); the matched-pairs claim says the pairing is UPLOADER-DECLARED, not attested | `test_hosted_stats_test_verification.py::test_empty_or_imported_provider_does_not_imply_deterministic`, `::test_matched_pairs_claim_is_marked_uploader_declared` |
| **P0/P1** the JCS canonicalizer sorted keys by code point (not UTF-16), coerced non-string keys, and admitted unsafe integers / lone surrogates — a cross-language verifier would disagree on the hash | `canonical_json` sorts by UTF-16 code units, rejects non-string keys, rejects ints beyond 2^53−1 and lone surrogates; official edge vectors pinned | `test_canonicalization_vectors.py` |
| **P0** a fail-closed DENY (no driving value) invented a fake `v_none`/`v_unresolved` ledger id → the trace failed validation, so the most interesting incidents were unpublishable | `driving_value_id` is null with a typed `driving_unresolved` reason; semantics accept null only with a reason; roundtrips through a signed bundle | `test_trace_ledger_integrity.py::TestFailClosedEvidenceRoundtrips`; `test_multi_driving_gate.py` |
| **P1** the endpoint gateway trusted request shape — an unknown tool hit a KeyError→500, malformed events crashed, a redacted sensitive value needn't pin its bytes, and the assembled trace was never validated before serving | every event shape validated; unknown tool → 400; redacted sensitive value must carry a canonical_value_hash; finalize runs validate_artifact + trace_semantics; clean 400/opaque-500 boundary; finalized runs are LRU-evicted so they can't exhaust the quota | `test_gateway_conformity.py` |
| **P1** a cost-stopped run was labelled `[completed]`, reported "N trials completed" over a list that also held failures/exclusions, and ran condition-major so a stop left zero matched pairs | block-balanced trial order (scenario→repeat→condition); CLI reports planned/completed/failed/excluded and labels `[completed_partial]`/`[stopped_cost_ceiling]`; missingness is condition-aware; a USD budget reserves output tokens and counts the tool schema | `test_budget_aware_design.py` |
| **P1** a downloaded reproduction package could not be verified without trusting the serving server | the download carries a portable receipt (author/key_id/signature/signed_ref); the publish response carries an acceptance receipt; `axor-lab verify` checks hashes + replay + receipt offline | `test_portable_receipt.py` |
| **P2** a malformed downloaded package crashed with a traceback; the page's download `curl` used a relative `./api/...` that broke from `/e/{id}`; the stats header claimed "live runs" for a deterministic agent | read_bundle_package raises a clean RunnerError; the curl uses a root-relative `/api/...` and shows `axor-lab verify`; the header is split by determinism and comparison design | `test_portable_receipt.py`; `lab_server/html.py` |

Still deferred (documented): a cryptographic trusted-runtime attestation that
would make a matched-pairs pairing (and `explicit_flow_tracked`) VERIFIED
rather than uploader-declared, a token-accurate cost model, and a
self-contained bundle that embeds the whole experiment document. The
load-bearing round-14 goals — CI is authoritative, takedown is final over the
evidence, the canonicalizer matches RFC 8785 byte-for-byte, a fail-closed
incident is publishable, the gateway is a real conformity boundary, a partial
run is labelled honestly, and a published result is verifiable offline — are
closed.

## Fifteenth review round — verification closure

Round 14's fixes were real, but several guarantees were closed only at the first
level: a bundle tombstone was not evidence-lineage removal, a receipt's presence
was not a verified signature, a replay returning a verdict was not a replay that
was actually possible, a lower stored estimate was not an earned bridge, and a
finalized trace was not a safely delivered one. This round closes those to
verification: a label is not granted unless the thing it names is proven. Eight
patches, each with proving tests. Suite green: **586 tests**.

| Round-15 finding | Fix | Proof |
|---|---|---|
| **P0** evidence takedown removed only the exact publication id and keyed on a packaging-sensitive bundle_ref — a sibling published earlier stayed public, and repackaging the same evidence escaped the tombstone | `evidence_lineage_ref` hashes only the load-bearing evidence (scenarios/conditions/manifests/completed-trial coords+refs/aggregate defs); takedown retires EVERY sibling on that lineage and blocks re-publish under altered metadata or repackaged bytes; reads guard on it; cold load is two-pass | `test_evidence_lineage_takedown.py` |
| **P0** `axor-lab verify` exited 0 for an unverified signature, and verify_receipt accepted integrity=signed with the signature stripped | verify_receipt is a strict state machine (hash_verified ⇒ no signature; signed ⇒ ed25519 + non-empty signature/author/key_id that MUST verify; trust-anchor binding); the CLI returns 5 (unverified), 1 (invalid), 0 (verified) distinctly | `test_verification_outcome.py` |
| **P0/P1** a redacted sensitive value a decision turned on was replayed against a hash sentinel yet reported match/mismatch; a fail-closed reason was not in the replay core | new `redacted_input_unavailable` status (exact replay refused, publish rejects it); `_verdict_core` includes `driving_unresolved` when the driving value is null; EvidenceCase claims exact only for a replayable status | `test_replay_capability.py` |
| **P1** the server acceptance receipt was an unsigned, unpersisted blob the CLI ignored; the download carried the bundle but not the publication body | `PublicationStore.acceptance()` builds a deterministic, content-addressed, (optionally) Ed25519-signed axor-lab-acceptance/v1 receipt; it is persisted and returned; the download carries the publication + acceptance; `axor-lab publish` saves it | `test_server_acceptance.py` |
| **P1** a test's power was read from a marginal aggregate n (a 1-pair McNemar rode an n=100); the server did not recompute the two_proportion interval or reject unknown test fields; hosted claims hid the planned/completed denominator | tests carry effective_n + status and are dropped when underpowered; check_aggregates recomputes the interval and rejects unrecognized fields; claims report completed/planned and flag condition-imbalanced missingness | `test_statistical_completeness.py` |
| **P1** the CP earned bridge was true on a lower stored estimate alone — a 1-vs-1 run "earned" it; config_hash omitted the tool manifests | the bridge requires a minimum effect delta, minimum effective n, balanced arms, and an aggregate n not exceeding recorded completed trials; `executable_config_hash` binds the manifests; a pin with no recorded decision is rejected | `test_cp_bridge_policy.py` |
| **P1** the gateway could evict a finalized trace before the client's first read | a run is FINALIZED_UNDELIVERED until its trace is fetched; eviction only takes delivered runs, else refuses (429) | `test_gateway_conformity.py` |
| **P1** a mixed-kernel run wrote a bundle that failed its own schema; USD-only budgets weren't hard; condition order was a temporal confound; the signature schema described the wrong bytes; the page curl wasn't runnable | kernel_version optional + kernel_versions; write_bundle_dir schema-validates pre-swap; `output_cap` caps output at affordable USD; counterbalanced+recorded condition order; signature description fixed; curl uses an absolute origin | `test_contract_parity_r15.py` |

Still deferred (documented): a cryptographic trusted-runtime attestation that
would make a matched-pairs pairing (and `explicit_flow_tracked`) VERIFIED rather
than uploader-declared, a token-accurate cost model, and a self-contained bundle
that embeds the whole experiment document. The load-bearing round-15 goal —
every `signed` requires a verified signature, every exact claim requires
reconstructible inputs, takedown follows a stable lineage and removes all
siblings, server acceptance is portable and checkable, statistical claims show
their denominator, the production bridge rests on powered evidence, a finalized
trace cannot be lost before delivery, and config_hash identifies the whole
executable config — is closed.

## Sixteenth review round — the artifact-vs-execution gap

Round 15 closed *labels* to verification. Round 16 closes the gap between a
thing EXISTING in an artifact and it actually being EXECUTED / TRUE: a proof
object existing is not that it was checked; a pinned kernel version being written
is not that that kernel ran; a stored aggregate is not evidence; a stored
acceptance is not the one the server signed. Most dangerous first — a real
`axor-core@X` artifact silently replayed under the reference kernel and written
bit-identical. Seven patches + two lower-severity fixes, each with proving tests.
Suite green: **613 tests**.

| Round-16 finding | Fix | Proof |
|---|---|---|
| **P0** a bundle pinning `axor-core@X` on a machine without that exact build fell through to `registry.get()`, which minted a reference kernel for any string — so it replayed under the one-gate reference kernel yet claimed the pinned build and wrote a bit-identical verdict | `resolve_kernel` satisfies a real-kernel pin ONLY with the exact installed build; missing/mismatched raises `UnknownKernelError` → `REPLAY_UNSUPPORTED_KERNEL`, never a silent substitution; the standard slice pins `reference_taint_floor_kernel` and says so | `test_kernel_identity.py` |
| **P0** a downloaded server package could have its receipt stripped (or its publication/acceptance edited) and `verify` still exited 0 | `verify` detects a server package and REQUIRES a receipt + a publication-binding check + an acceptance state-machine check (`verify_publication_binding`, `verify_acceptance`); a stripped receipt or edited claim fails | `test_package_verification.py` |
| **P0** a repeated takedown erased the stable lineage tombstone after a restart; a crash between tombstone and body-sweep left an orphan servable; array-order permutation dodged the lineage guard | durable `_lineage_tombstones` registry loaded first on cold start; takedown is idempotent and writes the lineage tombstone (fsync) before deleting bodies; `evidence_lineage_ref` maps scenarios/conditions/manifests by id→content-hash (order-independent) | `test_lineage_durability.py` |
| **P1** the Control Plane earned bridge read the uploaded aggregates — a hand-built bundle could earn it with fabricated but hash-consistent numbers | the bridge RECOMPUTES ASR from the traces via the scenario's own `violation` predicate; earns only on a powered, balanced, statistically-separated delta (2-proportion 95% interval excludes zero); without traces it is unverifiable → not earned | `test_cp_bridge_policy.py` |
| **P1** McNemar's `effective_n` was the total matched-pair count (a 200-pair, 1-discordant run read as powered); the server didn't reject inconclusive uploaded tests or demand the exact test shape | `effective_n` is the DISCORDANT n (b+c); the server recompute demands the exact test shape and refuses a test it recomputes as inconclusive (runner/server parity) | `test_statistical_completeness.py` |
| **P1** `executable_config_hash` bound `effect`+`args_schema` but NOT `untrusted_fields`, so manifests that taint different fields hashed identically; the real kernel never expanded `$inputs` allowlists | one canonical `compiled_governor_config` (untrusted-field taint patterns included) is the sole source of the hash AND the runner's governor kwargs; real-kernel allowlists expand `$inputs` against scenario inputs; export-cp surfaces the executable hash as the carry-over key | `test_real_kernel.py` |
| **P1** the gateway marked a run `delivered` (evictable) inside the GET handler, before the socket write — a failed write or a client crash lost a trace the client never received | the trace is frozen at finalize; GET serves it and is repeatable without marking delivered; a new `POST /trace/ack` is the only thing that marks delivered → evictable; an unacked trace stays retrievable | `test_gateway_conformity.py` |
| **P1** the persisted `acceptance.json` was never read on load; every `acceptance()` re-minted, re-signing under whatever key the server held NOW — a rotation silently replaced the historical attestation | the receipt is restored from disk (binding-verified) and served verbatim; signature verification is the reader's job with the publishing key; a tampered file is dropped and re-minted clean | `test_server_acceptance.py` |
| **P2** replay flagged the WHOLE trace `redacted_input_unavailable` even under enforcement off, where the verdict is an arg-independent ALLOW; `--max-usd` read as a hard cap | the off-path replays exactly without the redacted value → MATCH; docs/CLI name the USD ceiling BEST-EFFORT (illustrative prices) and the token ceilings HARD | `test_replay_capability.py` |

The round-16 goal — a pinned kernel is the kernel that ran or the trace is
`unsupported_kernel`; a downloaded package's receipt/publication/acceptance are
all verified before `verify` exits 0; takedown is durable, crash-safe, and
order-independent; the CP bridge and every hosted statistic are recomputed from
evidence, never trusted from an uploaded number; the executable-config identity
binds the whole compiled governor config; a finalized trace is delivered only
when the client acknowledges it; and the acceptance served is the one the server
signed — is closed.

## Seventeenth review round — Boundary Unification

Round 16 fixed the most dangerous defects in the CORE runner/replay/server
pipeline. Round 17 closes the gap between a primitive EXISTING and it being
applied to EVERY surface: a correct resolver ≠ every execution surface using it;
a versioned package ≠ a package that can't be stripped back to bare; a stored
acceptance ≠ its historical signature verified; a bridge recomputed from traces ≠
one that used the FULL trace set; a delivery ack ≠ an ack bound to specific
bytes. Seven patches, each with proving tests. Suite green: **644 tests**.

| Round-17 finding | Fix | Proof |
|---|---|---|
| **P0** the HTTP gateway and in-process endpoint built a reference Kernel for any version string, so a real `axor-core@X` condition produced a reference-kernel decision under a production-kernel trace label | both surfaces resolve through the shared `resolve_kernel` (real build or UnknownKernelError at construction) and dispatch to the real governor for an AxorKernel; a new `resolve_kernel_for_trace` threads each trace's scenario inputs through CLI regress / CLI+HTML EvidenceCase / incident import | `test_endpoint_kernel_identity.py` |
| **P0** a server package could be downgraded to a bare `{bundle,traces}` by stripping the envelope + every proof, then verified with exit 0; an unsigned acceptance passed as a server verification | `verify` requires a versioned envelope by default (`--allow-bare` to opt into bare integrity+replay only); an unsigned acceptance is UNVERIFIED (exit 5) unless `--allow-unsigned-server`; `verify_acceptance` binds acceptance.integrity to publication.integrity | `test_package_verification.py` |
| **P1** the persisted acceptance was restored on load without verifying its signature — a forged signed acceptance (recomputed report hash + bogus signature) was served as trusted | a historical server keyring verifies a known key's signature (forgery → quarantined); an unknown (rotated-out) key is kept as an opaque UNVERIFIED historical record, never re-issued under the current key | `test_server_acceptance.py` |
| **P1** the CP bridge silently skipped completed trials whose trace was absent, so a cherry-picked favourable subset could earn it; supporting refs named stored aggregates it never consulted | `_recompute_asr` raises on any missing completed-trial trace (full evidence required); the bridge emits an immutable `cp_bridge_analysis/v1` receipt and supporting refs point at it | `test_cp_bridge_policy.py` |
| **P1** `executable_config_hash` always compiled with symbolic `$inputs`, yet was named the full runtime carry-over key — two scenarios with different input-backed allowlists shared it while governing differently | split `parametric_policy_hash` (symbolic, the honest carry-over key) from `runtime_config_hash` (concrete, per-scenario `$inputs` expanded); export-cp emits both | `test_real_kernel.py` |
| **P1** the gateway ack accepted any POST after finalize (not bound to the delivered bytes, fireable before any GET); finalized-unacked runs permanently exhausted the run quota | finalize returns `trace_ref`, GET exposes it (ETag), ack requires a prior fetch + `trace_ref == content_hash(frozen_trace)`; `max_runs` bounds only ACTIVE runs so finalized ones can't block new opens | `test_gateway_conformity.py` |
| **P1/P2** a valid mixed-kernel publication 400'd on the page (`kernel_version` KeyError); the acceptance always claimed `statistics_recomputed`; the "durable" tombstone fsync'd only the directory, not the file bytes | the page renders `kernel_versions`; the report lists `statistics_not_applicable` when there are no aggregates; `_write_atomic(durable=True)` fsyncs the contents before the rename | `test_surface_parity.py` |

The round-17 goal — one kernel resolver on every execution surface; a package
that cannot be silently downgraded and an unsigned acceptance that never reads as
authenticated; a historical acceptance whose signature is verified, not assumed;
a CP bridge that requires the complete evidence graph and names it; a carry-over
key that is honestly parametric, distinct from the concrete runtime config; a
delivery ack bound to the exact bytes with a quota that can't be exhausted by
unacked runs; and every valid artifact surviving the page, the report, and a
power loss — is closed.

## Eighteenth review round — Candidate and Proof Closure

Round 17 unified the surfaces. Round 18 closes the gap between a primitive
EXISTING and it *applying to every surface, being internally consistent, and
having been recorded during runtime*: a kernel resolver ≠ the one that resolves
the candidate a `regress --kernel` asks about; a signed publication ≠ a receipt
whose integrity matches it; a lossless-delivery lifecycle ≠ a retention cap that
never sheds unread evidence; a bridge that requires all refs ≠ one that verifies
the evidence graph and proves the runtime config it recommends actually ran; a
verifier that rejects a forged acceptance ≠ a history honest that the original
failed; an atomic write ≠ one that survives a short `os.write`; a taint model ≠
one that fails closed when the taint was redacted. Seven patches, each with
proving tests. Suite green: **675 tests**.

| Round-18 finding | Fix | Proof |
|---|---|---|
| **P0** `regress --kernel X` rebuilt each trace's ORIGINAL recorded kernel, not the candidate X the user asked to test — so a counterfactual regression silently re-ran the old kernel | split `resolve_recorded_kernel_for_trace` (exact replay: trace's own condition) from `resolve_candidate_kernel_for_trace` (policy from the candidate condition, version from `--kernel`, inputs from the trace's scenario); regress uses the candidate resolver | `test_regress_candidate_kernel.py` |
| **P0** the retained-trace quota evicted a finalized-but-unread trace to make room, dropping evidence the client had never received | retention evicts ONLY an acknowledged (DELIVERED) trace; if the cap is full and nothing is acked, the gateway FAILS CLOSED (429) rather than shed unread evidence — for both the count cap and a new byte cap | `test_gateway_conformity.py::TestLosslessRetention` |
| **P0/P1** a `signed` publication verified green while carrying a hash-only author receipt (the author signature stripped, the server acceptance left signed) — a proof-level downgrade | `verify` requires `receipt.integrity == publication.integrity`, and a signed publication with no `--pubkey` to check its receipt is UNVERIFIED, not a pass; the receipt/publication/acceptance integrity claims must all agree | `test_package_verification.py`, `test_verification_outcome.py` |
| **P1** the CP bridge required all trace refs but never verified the evidence GRAPH they came from, and per-scenario runtime hashes were recomputed at export time, not proven to match what ran | `export_cp` runs `validate_artifact` + `verify_bundle` on the traces (tamper/re-point → `CPExportError`); `config_provenance` records each completed trial's `runtime_config_hash` at build time, cross-checked on export ("does not match what ran"); frozen bridge trace bodies travel with the deploy config | `test_cp_bridge_policy.py` |
| **P1** a damaged/forged persisted acceptance under a known key was silently dropped, then lazily re-minted as a clean `acceptance/v1` indistinguishable from the publish-time record | the invalid bytes are QUARANTINED (`acceptance.invalid.json`) and a distinct, timestamped `reacceptance/v1` — linking to the invalid original by content hash — is minted, persisted, and served; `verify` reports the re-attestation instead of choking on the schema | `test_server_acceptance.py` |
| **P1** an existing primitive did not hold on every branch: `_write_atomic` ignored short `os.write`s; replay caught bare `Exception` and mislabelled internal bugs `unsupported_kernel`; a real-kernel gate skipped redacted untrusted taint (fail-open); the in-process endpoint silently dropped unknown event types; a run had no byte quota | full-write loop; replay narrowed to `UnknownKernelError` (internal errors propagate); both endpoint surfaces DENY `provenance_unavailable` on a redacted untrusted driving value; unknown event type raises; per-run + retained byte quotas | `test_r18_hardening.py` |
| **P1/P2** green CI skipped every crypto/real-kernel-gated suite except two; the production-todo named the concrete `config_hash` the carry-over key; the delivery ack claimed `delivered` though the server only knows the client fetched+echoed the ref | the real-kernel CI job runs `test_endpoint_kernel_identity` + `test_replay_capability` (asserting axor imports), the crypto job runs the package/receipt/acceptance/verification suites (asserting PyNaCl); the todo names `parametric_config_hash`; the ack response is honestly `client-declared` | `.github/workflows/ci.yml`, `test_cp_export.py`, `test_gateway_conformity.py` |

The round-18 goal — a regression that tests the kernel the user named, not the
one the trace recorded; a retention cap that never drops unread evidence; a
signed publication whose author receipt cannot be downgraded; a CP handoff that
verifies the evidence graph and proves the runtime config it recommends is the
one that ran; an acceptance history honest that an original attestation failed;
primitives that hold on the short-write, internal-error, redacted-taint and
unknown-event branches; and a CI that runs the crypto and real-kernel proofs
rather than skipping past them — is closed.

## Nineteenth review round — Experimental and Historical Closure

Rounds 16–18 hardened the integrity spine: a properly-verified artifact can no
longer be forged. Round 19 asks the next question — does a properly-verified
artifact prove the CAUSAL claim written on it, and is its lifecycle honest end to
end? The most dangerous defect this round was not in crypto or the server but in
the earned-bridge statistics: a full, graph-valid evidence set with equal arm
sizes and a large, statistically-separated ASR delta could still be a COMPOSITION
contrast (heavy scenarios in one arm, light in the other) rather than a
governance effect — so perfect structural evidence could earn a wrong production
recommendation. Seven patches, each with proving tests. Suite green: **694 tests**.

| Round-19 finding | Fix | Proof |
|---|---|---|
| **P0** the earned bridge pooled every completed trial per condition and always ran an independent two-proportion test, so equal-size arms testing DIFFERENT scenarios (composition shift) could earn it despite not one shared experimental unit | the bridge is computed over experimental-unit COORDINATES (scenario, seed, repeat): a composition guard requires both arms to cover the SAME scenarios, then matched_pairs uses the coordinate intersection + McNemar over the discordant pairs (b>c, conclusive, p<0.05), and independent_samples requires per-scenario balance before the interval is consulted; the receipt records comparison_design + scenario_balance + paired/discordant counts | `test_cp_bridge_policy.py::TestDesignAwareBridge` |
| **P0/P1** retained count + byte quotas were checked only at POST /runs, but the ACTIVE→RETAINED transition happens at /finalize, so pre-opening max_runs then finalizing them all overran the retained cap by orders of magnitude; bytes were counted from input events, not the frozen trace | retained capacity is RESERVED at /finalize (evicting only acknowledged traces, else 429 with the run kept active), measured from the exact frozen-trace byte size | `test_gateway_conformity.py::TestLosslessRetention` |
| **P1** a MISSING, malformed, or non-object acceptance.json for a LOADED publication returned None, which acceptance() re-mints as a clean acceptance/v1 impersonating the publish-time record; the single fixed-name quarantine kept only the FIRST damaged copy; the package carried no history | missing/malformed is a forensic event re-attested as a reacceptance/v1; an APPEND-ONLY acceptance-history/ (content-hash keyed) preserves every superseded record; the reproduction package carries acceptance_history and `verify` requires a reacceptance's previous_ref to RESOLVE to a record in it | `test_server_acceptance.py`, `test_package_verification.py` |
| **P1** config_provenance was optional (empty {} passed), the export emitted a runtime hash for EVERY scenario (including one whose governed trial never completed), the key was an ambiguous "<sid>\|<cid>" string, and the hash was recompiled in build_bundle, not recorded at execution | the runner records runtime_config_hash + config_compiler_version ON the trial at execution; config_provenance reads it, keyed NESTED {scenario:{condition:hash}}; the evidence export emits hashes ONLY for executed (scenario, condition) pairs and REQUIRES complete, compiler-versioned provenance (missing key → hard error) | `test_cp_bridge_policy.py::TestMandatoryRuntimeProvenance` |
| **P1** export_cp ran its schema + graph verification only `if traces is not None`, so a no-traces call produced a deploy config with no verification at all; the export directory was not self-contained (bridge-traces/ alone can't re-derive the bridge) | export_cp REQUIRES the complete traces (`verified:true`); export_cp_template is the honestly-named unverified path; the CLI writes a self-contained source-bundle/ + bridge-analysis.json, and `axor-lab verify-cp-export` recomputes the whole handoff from the directory and confirms it is byte-identical to cp-deploy.json | `test_cp_bridge_policy.py::TestBridgeExportPortability` |
| **P1** two r18 real-kernel proof suites (candidate-regression, redacted fail-closed) were @skipUnless(axor_available) but not in the real-kernel CI job, so they only ever skipped | the real-kernel job runs test_regress_candidate_kernel + test_r18_hardening too, captures the run's own exit code, and asserts skipped=0 so a silent skip fails CI | `.github/workflows/ci.yml` |
| **P2** _write_atomic spins forever on a zero-byte os.write; a retried finalize dropped the trace_ref; a fail-closed provenance_unavailable with no driving args used a "v_none" sentinel that failed trace_semantics; the condition schema called config_hash the carry-over key | zero-write raises; idempotent finalize returns the same trace_ref; the fail-closed decision emits a null driving_value_id + driving_unresolved; the schema names parametric_config_hash the carry-over key | `test_r18_hardening.py`, `test_gateway_conformity.py` |

The round-19 goal — an earned bridge that separates governance from composition
shift; retained quotas enforced at the transition that creates retained state;
every acceptance event preserved and portable; runtime provenance mandatory and
recorded at execution; a CP export that is self-contained and independently
recomputable; and the optional-dependency proof suites actually run in CI — is
closed.

Still roadmap (honestly not done): the gateway remains a single-process,
in-memory, single-tenant advisory boundary. Retention is now lossless AND
bounded, but a bearer-token client can still hold retained slots until it acks;
per-tenant quotas, a TTL/durable evidence spool, and owner attribution are
infrastructure for the hosted service, marked experimental in the maturity table
(the delivery lifecycle and the atomic retained-cap transition are the
contract-level groundwork).

## Twentieth review round — Causal and Execution Attestation Closure

Round 19 made the earned bridge design-aware and the acceptance lifecycle
portable. Round 20 pressed on the same seam from the other side: structurally
valid evidence + a significant p-value + a partially balanced design is still not
a meaningful causal production signal, and an attestation that verifies its own
bytes is not the same as one whose forensic chain resolves. The most dangerous
defect was again in the earned bridge — a matched design could clear McNemar's
p<0.05 on a handful of discordant pairs while the NET absolute risk reduction was
2%, earning a production recommendation off a statistically-real but practically
negligible effect; and an independent design could pass with only aggregate-level
balance while each arm's per-scenario mix inverted (pure reweighting). Five
patches, each with proving tests. Suite green: **714 tests**.

| Round-20 finding | Fix | Proof |
|---|---|---|
| **P0** the matched bridge earned on McNemar significance alone, so a large N with many discordant pairs but a tiny net effect ((b−c)/completed ≈ 2%) earned a production config; the independent bridge checked only aggregate arm balance, so an inverse per-scenario mix (baseline 40 hard/20 easy vs governed 20 hard/40 easy) earned on pure reweighting | matched_pairs now requires net_risk_reduction = (b−c)/completed_pairs ≥ 0.10 as a SEPARATE practical-significance gate after p<0.05; independent_samples requires EXACT per-scenario arm balance before the interval is consulted; the planned denominator counts both-failed pairs as dropped; a single ASR aggregate per condition and an explicit baseline are required (no order-dependence) | `test_cp_bridge_policy.py::TestCausalValidity` |
| **P1** runtime provenance was recorded at execution for the runner but the schema still ADMITTED a completed trial with no runtime_config_hash (config_provenance would silently recompute one), so an export could present a reconstructed config as the measured one; divergent hashes for the same (scenario, condition) were merged | the bundle schema REQUIRES trace_ref + runtime_config_hash + config_compiler_version on every completed trial; config_provenance flags provenance_status recorded_at_execution vs reconstructed_legacy (the evidence export refuses the latter) and raises on a divergent per-(scenario, condition) hash; the resolved kernel fingerprint (incl. behaviour flags) is recorded distinct from the declared condition kernel | `test_cp_bridge_policy.py::TestExecutionProvenanceEnforcement` |
| **P1** verify-cp-export recomputed only the recommended runtime config — proving DERIVABILITY of one artifact while ignoring the source-bundle/, regression-set/ and bridge-analysis in the tree — and had no way to attest AUTHENTICITY; a re-export into a populated dir left stale files intermixed | the export writes a signed full-file manifest (sha256 of every file); verify-cp-export runs INTEGRITY (every listed file hashes + no unlisted), AUTHENTICITY (verify the manifest signature; a signed export with no key → EXIT_UNVERIFIED) and DERIVABILITY (recompute); re-export refuses a non-empty dir unless --overwrite, which clears it first | `test_cp_bridge_policy.py::TestBridgeExportPortability` |
| **P1** a persisted reacceptance/v1 was restored verbatim once its own signature + binding verified, but its supersedes.previous_ref was never resolved against the append-only history — so deleting or corrupting the archived predecessor left a re-attestation the server served while an offline verifier would reject the unresolvable chain | on cold load the server resolves a reacceptance's previous_ref against a hash-checked history index; a missing or tampered predecessor is a forensic broken-chain event — the record is archived and the server re-attests with a linked reacceptance whose chain now resolves (converging, not re-stamped every reload) | `test_server_acceptance.py::TestAcceptanceHistoryChainOnLoad` |
| **P1/P2** _reserve_retained rejected an incoming run larger than the whole byte budget (or under a disabled retention) by falling through into the eviction loop, deleting every acknowledged trace before still returning False; the design-aware bridge tests fed graph-INVALID bundles that a real export_cp would reject; the crypto CI never asserted its tests ran and the real kernel floated at >=0.9 | the impossible case returns False before any eviction, so acknowledged evidence survives an un-admittable finalize; the design-aware fixtures are now graph- and schema-valid and routed through export_cp/verify_bundle; the crypto job fails on any skipped=N and axor-core is pinned to an exact version | `test_gateway_conformity.py::TestLosslessRetention`, `test_cp_bridge_policy.py::TestDesignAwareBridge`, `.github/workflows/ci.yml` |

The round-20 goal — an earned bridge that gates practical significance and exact
allocation, not just a p-value; runtime provenance the schema makes mandatory and
the export refuses to reconstruct; a CP export whose integrity, authenticity and
derivability are each checked over the WHOLE directory; an acceptance chain the
server proves resolves before serving; and a retention path that never sacrifices
acknowledged evidence for a run it cannot admit — is closed.

Still roadmap (honestly not done): the earned bridge's practical-significance
floor (0.10) and minimum effective N (20) are fixed defaults, not per-domain
calibrated thresholds; a hosted deployment would want them configurable per
metric with the calibration itself recorded in the receipt. The gateway's
single-tenant, in-memory advisory nature is unchanged from round 19.
