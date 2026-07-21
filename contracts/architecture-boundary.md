# Axor Lab — Architecture Boundary (v1) — READ FIRST

**Axor Lab is the experiment and evidence layer over Axor runtime traces.** Not a second runtime, proxy, gateway, sandbox, observability backend, and publication platform at once. This file defines the boundary; where any other doc implies Lab executes, proxies, or connects to agents itself, this file overrides it.

---

## The one rule

```
Agent / Axor Runtime (adapter)
        │  executes the agent locally
        │  applies governance locally
        │  sends traces/events outward (outbound-only)
        ▼
   Shared Axor trace fabric   ← one trace stack, defined in axor-core
        │
   ┌────┴─────┐
   ▼          ▼
Control Plane   Axor Lab
operation       experiments
live topology   statistics
interventions   EvidenceCase
notifications   replay / regression / publication
```

The adapter opens the connection and pushes traces. **No Axor backend — CP or Lab — connects to, executes, or proxies the agent.** Lab does not need its own proxy; it reads the same fabric CP reads. A user connects an agent **once**; both modules see it. The retired anti-pattern (named aloud in the old ui-backend-contract): "climb the same onboarding shape twice." One runtime connection, two viewing modules.

## Schema ownership — one source of truth per schema

| Schema | Owner | Why |
|---|---|---|
| **trace / event** | **axor-core** (shared) | one portable JSONL artifact for runtime, storage, replay; CP and Lab both import it. Never three copies. |
| **tool-manifest** | **axor-core** (shared) | the runtime detects/declares tools; Lab consumes, doesn't own |
| **kernel policy / config identity** | **axor-core** (shared) | the thing a `condition` references |
| scenario, predicate, experiment, condition, bundle, publication | **Lab-owned** | the experiment/evidence layer proper |
| attestation | Lab, **deferred** | not v1 (see below) |

`condition` becomes a thin Lab wrapper over shared refs:

```json
{ "enforcement": "on", "kernel_ref": "...", "policy_ref": "...", "runtime_config_hash": "..." }
```

TypeScript types generate from the shared schemas; Lab does not redefine them.

## Connection model (replaces the demo→proxy→full ladder)

Not four product rungs — one runtime connection with fidelity variants, plus non-runtime sources:

| Mode | What happens |
|---|---|
| **Demo** | Axor-hosted template, no agent — zero-setup |
| **Connected runtime** | an existing Axor runtime receives an experiment assignment and pushes traces (proxy vs full were just fidelity of this) |
| **Trace import** | analyze a production incident or someone's published run |
| **Offline runner** | CI, air-gapped, private code |

"Connect runtime" issues a **scoped ingest/job key for the same Axor adapter** that later serves Control Plane. Existing CP users just **select** an already-connected runtime. No second integration. Black-box endpoint eval is removed entirely.

## Execution contract (Lab assigns, runtime executes)

Lab backend never calls a tool or an agent. It hands out assignments; the runtime pulls, runs locally, and uploads:

```
GET  /runtime/jobs                                  runtime polls for assignments
POST /runtime/jobs/{id}/claim                        runtime claims one
POST /runtime/jobs/{id}/trials/{trial_id}/events     runtime streams kernel events
POST /runtime/jobs/{id}/trials/{trial_id}/complete   runtime finalizes the trial
```

Enforcement, tool dispatch, provenance construction all happen in the runtime (paper mechanism), not in Lab.

## Removed from Lab (was scope creep, now out)

- **Lab gateway / MCP proxy / `POST /runs/{id}/tools/{call_id}/result`** — Lab does not dispatch tools, hold tool credentials, do SSRF for user endpoints, or act as a synchronous enforcement boundary. Runtime/adapter territory. (`endpoint-protocol.md` is retired from Lab.)
- **Black-box endpoint evaluation** — no provenance, no governance, no EvidenceCase; pulls Lab onto generic-eval turf (LangSmith/Braintrust) off Axor's territory. Deleted.
- **Arbitrary cloud code upload + Lab-owned sandbox** — enterprise/later, not core. Connected runtime covers most cases without moving code. Core keeps only hosted curated templates + safe simulated tools.
- **Second trace schema** — deleted; trace lives in axor-core.
- **Lab-specific entitlement subsystem** — entitlement is platform-level (`workspace_tier`, `private_lab`, `control_plane`, `self_hosted_runner`), per `axor-packaging.md`.
- **Complex attestation / reacceptance / tombstone chains** — deferred; v1 keeps publication + bundle hash + optional signature + reproduction records.

## Deferred (in vision, not in v1)

Multi-agent games · population scale · arbitrary topology · full catalog with ranking/leaderboards/reputation · PDF/MD export (after Results/EvidenceCase UI) · independent-reproduction attestations (after simple publications).

## The product sentence

> Axor Lab is the experiment and evidence layer over Axor runtime traces.

Everything that tries to make Lab connect, execute, or proxy an agent is out. Everything that turns runtime traces into experiments, statistics, EvidenceCases, replay, regressions, and publications stays.
