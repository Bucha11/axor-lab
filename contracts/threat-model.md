# Axor Lab — Threat Model (v1)

Lab executes or ingests other people's code, endpoints, and payloads, and publishes artifacts. The attack surface is real before any governance experiment runs.

## 1. Simulated-by-default execution (the load-bearing safety choice)
An attack benchmark tries to make an agent do a harmful thing. If the agent's tools are real, the benchmark *creates the incident it studies*. Therefore:
- Every `side_effecting` tool runs through a simulator/fixture by default (`ledger_stub`, `email_outbox`, `fs_sandbox`).
- Real side effects require explicit opt-in AND `isolated_test_account` + `resource_allowlist` + `dry_run_confirmed`.
- `ungoverned` (observe-only) means enforcement off, NOT "permit real side effects." Observation on, effects still simulated.

## 2. Untrusted code (cloud path, post-MVP)
gVisor/Firecracker-class isolation; CPU/RAM/disk/wall-time caps; ephemeral FS; no host mounts; egress deny-by-default + API allowlist; secret injection without persistence; dependency lock; output-size caps; kill/cancel; audit trail; retention policy. Until this exists, code execution is **local-only** (MVP).

## 3. Untrusted endpoints
SSRF, private-network, DNS-rebinding protections; auth; outbound allowlist; isolated egress runner. See endpoint-protocol.md.

## 4. Untrusted published payloads
Traces/bundles are uploaded JSON. Validate against schemas; treat all string content as untrusted in rendering (no HTML injection in EvidenceCase/catalog); redaction manifest enforced (observations only, never raw bodies); content-hash everything; size limits.

## 5. Trust in results
A `self_reported` / `local` origin bundle was not run on Lab infra and can be hand-edited. The server re-runs **replay** (safe, deterministic) to confirm verdicts match traces, but cannot attest the live run happened. The catalog shows origin+integrity honestly; a signed bundle from a known key is the strongest local claim, still not equal to `lab_infra`.
