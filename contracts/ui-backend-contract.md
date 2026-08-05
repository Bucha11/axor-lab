# Axor Lab — UI ↔ Backend Contract (v2, Suite Platform)

**Principle: every screen renders a schema-conforming payload from a named endpoint.** No screen invents data the backend does not return; no backend returns a shape no schema defines. Trace and tool-manifest come from the **shared axor-core fabric** (architecture-boundary.md), not a Lab-owned copy.

```
screen  ←  API endpoint  ←  payload conforming to a schema (Lab-owned or axor-core-shared)
```

**Rendered, never computed.** A screen displays aggregates, metrics and verdicts that the backend already stored; it never recomputes a rate, a threshold outcome or a verdict in the browser. Two implementations of the same statistic is how a published number and a displayed number diverge.

## 1. Connection model — one runtime, not a two-product ladder

Lab does not connect to, execute, or proxy agents. The **Axor runtime adapter** (the same one that serves the Control Plane) opens an outbound connection and pushes traces; Lab hands out assignments and reads the resulting traces. A user connects a runtime **once** — both products see it.

| Mode | What happens | `TraceSource` |
|---|---|---|
| **Simulated environment** | a Suite whose tools are Lab's simulator — zero setup, no agent | `demo` |
| **Live agent runtime** | a connected Axor runtime claims an assignment, runs locally, pushes events | `runtime` |
| **Uploaded traces** | analyse a production incident or a published run | `import` |
| **Recorded bundle / offline runner** | CI, air-gapped, private code | `offline_runner` |

There is deliberately **no remote-endpoint mode** — Lab never calls an agent out (INTEGRATION_PLAN §4.6). "Connect runtime" issues a scoped ingest/job key for the shared adapter. No Lab gateway, no MCP proxy, no black-box eval.

## 2. Screens

Navigation (Web UX RFC): **Home · Suites · Runs · Evidence · Regressions · Artifacts · Playground · Integrations · Settings**. Home is the default landing page and is **about the next action, not the past** — a catalog of what has already been published is a different screen and does not belong there.

| Screen | Endpoint | Payload conforms to |
|---|---|---|
| Home / Launchpad | `GET /home` | onboarding step, quick actions, built-in suites, recent activity |
| Suite Catalog | `GET /suites` | `[{ id, name, description, origin, available }]` |
| Suite detail | `GET /suites/{id}` | `suite/v1` |
| Suite Builder (save) | `PUT /suites/{id}`, `POST /suites` | `suite/v1` |
| Suite Builder (validate) | `POST /suites/validate` | `{ ok, errors[] }` |
| Playground — one trial | `POST /playground/trial` | one `trial` record + its `trace/v1` |
| Run (live) | `POST /runs`, `GET /runs/{id}`, `SSE /runs/{id}/events` | a lifecycle state + trial progress |
| Run Report | `GET /runs/{id}/report` | `bundle/v1.aggregates` + per-metric summaries |
| Trial detail | `GET /runs/{id}/trials/{tid}` | trial record + `trace/v1` (**shared**) |
| EvidenceCase | `GET /evidence`, `GET /evidence/{id}`, `POST /evidence` | `evidence-case/v1` |
| Regression | `GET /regressions`, `GET /regressions/{id}`, `POST /regressions`, `POST /regressions/{id}/run` | `regression/v1` + an `InvariantResult` |
| Artifacts | `GET /artifacts`, `GET /artifacts/{id}` | `artifact/v1` |
| Integrations | `GET /runtimes`, `POST /runtimes/connect`, `GET /runtimes/{id}/manifests` | runtime list; `tool-manifest/v1[]` (**shared**) |
| Published record | `GET /e/{id}`, `GET /api/publications` | `publication/v1` + reproduction records |

**The Suite Catalog shows an explicit unavailable state.** The design boards show six suite cards and three suites exist (Blank, AgentDojo, Budget). The catalog renders the other three as unavailable — never as a card that runs nothing.

**The Suite Builder's six sections — Agents · Scenarios · Environment & Tools · Execution · Evaluation · Artifact — and its three modes — Basic / Advanced / YAML — all edit ONE `suite/v1` document.** Every Builder field has a home in the manifest, or Basic mode owns state the YAML mode cannot see and the modes silently disagree about what the experiment is. That rule is testable without any frontend and is pinned in `tests/test_suite_platform_contracts.py`.

## 3. Runtime-facing execution contract

Lab assigns, the runtime executes — never the reverse.

```
GET  /runtime/jobs                                   poll for assignments
POST /runtime/jobs/{id}/claim                         claim one
POST /runtime/jobs/{id}/trials/{trial_id}/events      stream kernel events (shared trace/event schema)
POST /runtime/jobs/{id}/trials/{trial_id}/complete    finalize the trial
```

Enforcement, tool dispatch and provenance construction happen in the runtime, not in Lab.

## 4. Implemented today

`lab_server/runtime_jobs.py`:

```
GET  /suites                         GET  /runs/{id}              POST /suites/validate
GET  /suites/{id}                    GET  /runs/{id}/results      POST /runtimes/connect
GET  /runtimes                       GET  /runs/{id}/aggregates   POST /scenarios/validate
GET  /runtime/jobs                   SSE  /runs/{id}/events       POST /experiments/plan
POST /runtime/jobs/{id}/claim        GET  /runs/{id}/trials/{tid}/trace    POST /runs
POST /runtime/jobs/{id}/trials/{tid}/events      POST /runs/{id}/confirm
POST /runtime/jobs/{id}/trials/{tid}/complete
```

`GET /suites` serves `lab_suite.suite_catalog()` — the same function
`axor-lab suites` prints, so the terminal and the screen cannot disagree about
which suites exist. An announced-but-unimplemented suite has a catalog card and
a 404 on its manifest, which is what stops the Builder from opening an empty
document for a suite nobody wrote.

`lab_server/app.py`: `GET /` (catalog page), `GET /e/{id}`, `GET /e/{id}/evidence/{eid}`, `GET /api/publications`, `GET /api/publications/{id}` (+ `/bundle`, `/reproductions`, `/takedown`).

Everything in §2 that is not in this list has no implementation yet — that is Phase 3. This section exists so the table above reads as a target and not as a description.

## 5. Pairing (governance capability)

A governed comparison run exposes `{ pair_id, ungoverned_trial_id, governed_trial_id }`. The EvidenceCase screen fetches both traces: *observed* renders the ungoverned trace; *counterfactual policy replay* renders it with the replayed governed verdict as an overlay; *observed governed twin* renders the governed trace and is present only if a governed run actually executed. statistics.md reads discordant pairs off these records.

For a single-arm Suite there is no pair and no governance block — the EvidenceCase is a view over one trial, and the screen must render that without a second trace rather than showing an empty comparison.
