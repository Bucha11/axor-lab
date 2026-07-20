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
