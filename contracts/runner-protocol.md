# Axor Lab — Runner Protocol (v1)

How the local runner talks to the Lab web app. The MVP runner is **local-only**: the researcher's machine executes the agent and tools; Lab receives artifacts, never runs untrusted code. This is why the lifecycle branches by backend (see lifecycle.md).

## Local runner flow

```
axor lab run experiment.axl
  1. resolve  — load scenario/bench, tool manifests, conditions; validate against schemas
  2. estimate — trial count = conditions × repeats × scenarios; token/cost estimate; print, await confirm
  3. execute  — per trial: run agent on simulated tools + fixtures, emit trace/v1 with lineage
  4. gate     — apply condition (enforcement off/on) via pinned kernel; verdicts recorded in-trace
  5. analyze  — aggregate per statistics.md (unit=trial), compute intervals + tests
  6. bundle   — write bundle/v1 with content hashes
axor lab publish ./bundle   → uploads bundle, mints publication/v1 (origin=local)
axor lab replay  ./bundle   → recompute verdicts over frozen traces (offline, exact)
```

## Handshake (publish)

- Runner POSTs the bundle; server verifies `content_hashes`, sets `integrity = hash_verified` (or `signed` if a detached signature is present and the author key is known).
- `origin = local` always for the local runner (never claims `lab_infra`).
- Server assigns `publication_id`, returns the immutable page URL.

## What the server trusts

Nothing executable. The server validates schemas and hashes; it re-runs **replay** (deterministic, safe — no model calls, no tools) to confirm the published verdicts match the traces. It does NOT re-run the live agent. A `fresh_live` reproduction is always someone running the runner again, never the server.
