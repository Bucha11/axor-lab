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
