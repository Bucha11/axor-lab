# Axor Lab — Architecture Boundary (v1) — READ FIRST

**Axor Lab is the experiment and evidence layer over Axor runtime traces.** Not a second runtime, proxy, gateway, sandbox, observability backend, and publication platform at once. This file defines the boundary; where any other doc implies Lab executes, proxies, or connects to agents itself, this file overrides it.

---

## The one rule

**Control Plane and Axor Lab are two separate products** — separate repos, separate backends, separate URLs, separate APIs, separate credentials. They run independently; either works with the other absent. What is shared is the **local** layer and the **trace schema**, never a backend.

```
                        one agent
                            │
              one framework / generic adapter
                            │
                   one local axor-core        ← executes agent + applies governance LOCALLY
                            │
            ┌───────────────┴───────────────┐   two independent outbound clients
            ▼                               ▼
      PlaneClient                    LabRuntimeClient
   axcp_… → control.useaxor.net    axlab_… → lab.useaxor.net
            │                               │
     Control Plane backend            Axor Lab backend
   desired state, live operation    jobs, trials, traces, Results,
   telemetry, topology              EvidenceCase, replay, publication
   (its own store)                  (its own registry + trace store)
```

**Shared:** one local axor-core, one agent adapter (`adapters.md`), one runtime process, and the **trace/event + tool-manifest schemas** (defined in axor-core). **Not shared:** backends, URLs, APIs, credentials, trace stores, runtime registries, job queues. **No Axor backend — CP or Lab — connects to, executes, or proxies the agent** (both are outbound-only; the adapter runs the agent locally). There is **no shared "trace fabric" backend** — each product ingests and stores its own traces; only the *schema* is common. There are **no module flags** and no combined bootstrap: a runtime registers with each product separately, using that product's own protocol and token.

The earlier draft's "one trace stack / connect once / both modules see it / module flags" is retired — that collapsed two products into one backend. Correct model: two products, shared local layer + schema, two clients.

## Schema ownership — one source of truth per schema

| Schema | Owner | Why |
|---|---|---|
| **trace / event** | **axor-core** (shared) | one portable JSONL *schema*; each product ingests into its OWN store. Shared schema, not shared store. Never three schema copies. |
| **tool-manifest** | **axor-core** (shared) | the runtime detects/declares tools; Lab consumes, doesn't own |
| **kernel policy / config identity** | **axor-core** (shared) | the thing a `condition` references |
| scenario, predicate, experiment, condition, bundle, publication | **Lab-owned** | the experiment/evidence layer proper |
| attestation | Lab, **deferred** | not v1 (see below) |

`condition` becomes a thin Lab wrapper over shared refs:

```json
{ "enforcement": "on", "kernel_ref": "...", "policy_ref": "...", "runtime_config_hash": "..." }
```

TypeScript types generate from the shared schemas; Lab does not redefine them.

## Lab's connection model (Lab-side only)

How an agent's traces reach **Lab** (Control Plane has its own, separate connection via PlaneClient — not Lab's concern):

| Mode | What happens |
|---|---|
| **Demo** | Axor-hosted template, no agent — zero-setup |
| **Connected runtime** | a runtime registers with Lab (own `axlab_` token, Lab Runtime Registry), polls Lab jobs, runs the trial locally, uploads events + trace |
| **Trace import** | analyze a production incident or a published run |
| **Offline runner** | CI, air-gapped, private code |

"Connect runtime" registers with **Lab** using Lab's own Runtime Registry and issues an `axlab_` runtime token. It does **not** reuse a Control Plane connection. In *integrated* deployments Lab can **import a runtime reference from Control Plane** (server-side provider) and map ids, but it still issues its own Lab credential and owns its own jobs — see agent-connection.md. Black-box endpoint eval is removed entirely.

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
- **Module-flag entitlement** (`modules: {private_lab, control_plane}`) — removed. Entitlement is **per product**; a single commercial org may hold both and get token-exchange for UX, but there is no single backend with module flags. See `axor-packaging.md` (commercial: one org/billing) and agent-connection.md (technical: two products, two tokens).
- **Complex attestation / reacceptance / tombstone chains** — deferred; v1 keeps publication + bundle hash + optional signature + reproduction records.

## Deferred (in vision, not in v1)

Multi-agent games · population scale · arbitrary topology · full catalog with ranking/leaderboards/reputation · PDF/MD export (after Results/EvidenceCase UI) · independent-reproduction attestations (after simple publications).

## The product sentence

> Axor Lab is the experiment and evidence layer over Axor runtime traces.

Everything that tries to make Lab connect, execute, or proxy an agent is out. Everything that turns runtime traces into experiments, statistics, EvidenceCases, replay, regressions, and publications stays.
