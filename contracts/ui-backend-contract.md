# Axor Lab — UI ↔ Backend Contract (v1)

**Principle: every screen renders a schema-conforming payload from a named endpoint — a mock's inline data is a fixture standing in for that response.** No screen invents data the backend doesn't return; no backend returns a shape no schema defines. Trace/tool-manifest come from the **shared axor-core fabric** (architecture-boundary.md), not a Lab-owned copy.

```
screen  ←  API endpoint  ←  payload conforming to a schema (Lab-owned or axor-core-shared)
```

## 1. Connection model — one runtime, not a two-product ladder

Lab does not connect to, execute, or proxy agents. The **Axor runtime adapter** (the same one that serves Control Plane) opens an outbound connection and pushes traces; Lab hands out experiment assignments and reads the resulting traces. A user connects a runtime **once** — both modules see it. (The old "climb the same onboarding shape twice" was the anti-pattern; it's gone.)

| Mode | What happens | Trace source |
|---|---|---|
| **Demo** | Axor-hosted template, no agent — zero-setup | Lab-hosted |
| **Connected runtime** | an existing Axor runtime claims an assignment, runs locally, pushes events | runtime |
| **Trace import** | analyze a production incident or a published run | import |
| **Offline runner** | CI / air-gapped / private code | offline_runner |

"Connect runtime" issues a scoped ingest/job key for the shared adapter. Existing CP users **select** an already-connected runtime — no second integration. No Lab gateway, no MCP proxy, no black-box eval.

## 2. API surface

**User/UI-facing:**

| Endpoint | Method | Response conforms to |
|---|---|---|
| `/experiments` | GET | list of `publication/v1` summaries |
| `/e/{id}` | GET | `publication/v1` + resolved reproduction records |
| `/runtimes` | GET | connected runtimes `[{ runtime_ref, agent_ref, model, status }]` |
| `/runtimes/{id}` | GET | one runtime |
| `/runtimes/{id}/manifests` | GET | `tool-manifest/v1[]` (**axor-core shared** schema) |
| `/runtimes/connect` | POST | `{ ingest_key }` for the shared adapter |
| `/scenarios/validate` | POST | `{ ok, errors[] }` |
| `/scenarios` | POST | `{ scenario_id }` (`scenario/v1`, Lab-owned) |
| `/experiments/plan` | POST | `{ trials, estimate }` from an `experiment/v1` |
| `/runs` | POST | `{ run_id }` (binds experiment + runtime_ref) |
| `/runs/{id}` | GET | `{ state }` (a `lifecycle` state) |
| `/runs/{id}/events` | SSE | lifecycle transitions + trial progress |
| `/runs/{id}/results` | GET | `bundle/v1.aggregates` (Lab-owned) |
| `/runs/{id}/trials/{trial_id}/trace` | GET | `trace/v1` (**axor-core shared**) |
| `/bundles` | POST | `{ bundle_ref }` (`bundle/v1`) |
| `/publications` | POST | `publication/v1` |

**Runtime-facing execution contract (Lab assigns, runtime executes — never the reverse):**

```
GET  /runtime/jobs                                  poll for assignments
POST /runtime/jobs/{id}/claim                        claim one
POST /runtime/jobs/{id}/trials/{trial_id}/events     stream kernel events (shared trace/event schema)
POST /runtime/jobs/{id}/trials/{trial_id}/complete   finalize the trial
```

Enforcement, tool dispatch, and provenance construction happen in the runtime, not in Lab.

## 3. Screen → endpoint → schema (binding table)

| Mock | Calls | Renders (schema → field) |
|---|---|---|
| **lab-landing** | `GET /experiments` | catalog cards ← `publication/v1` |
| **lab-published** | `GET /e/{id}` | ← `publication/v1` + reproduction records; claims split by `claims[].kind` |
| **lab-agent-ingest** | `GET /runtimes`, `POST /runtimes/connect`, `GET /runtimes/{id}/manifests` | connect/select runtime; tools ← `tool-manifest/v1` (**shared**) |
| **lab-scenario-author** | `POST /scenarios/validate`, `POST /scenarios` | errors ← `{errors[]}`; emits `scenario/v1` |
| **lab-builder** | `POST /experiments/plan` | trials+estimate; binds `runtime_ref` (a connected runtime, not a raw model) |
| **lab-run-progress** | `POST /runs`, `SSE /runs/{id}/events` | pipeline ← `lifecycle` state per mode |
| **lab-results** | `GET /runs/{id}/results` | table ← `bundle.aggregates` — **rendered, never computed** |
| **lab-evidencecase** | `GET /runs/{id}/trials/{trial_id}/trace` (×pair) | chain ← `trace/v1` (**shared**); 3 modes over the pair |

## 4. State binding (four lifecycles, per lifecycle.md)

```
demo:               validating → queued → running → analyzing → completed
connected_runtime:  validating → waiting_for_runtime → running → receiving_traces → analyzing → completed
trace_import:       validating → importing → replaying → analyzing → completed
offline_runner:     validating → waiting_for_upload → analyzing → completed
```

Delivered over `SSE /runs/{id}/events`; `ready/awaiting_confirmation` sits before run start, carrying the estimate the user confirms.

## 5. Pairing (EvidenceCase + McNemar)

A run exposes `{ pair_id, ungoverned_trial_id, governed_trial_id }`. The UI fetches both traces: observed-ungoverned renders the ungoverned trace; counterfactual renders it + the replayed governed verdict as an overlay; observed-governed renders the governed trace. statistics.md reads discordant pairs off these records.

## 6. Real vs mock

Mocks carry inline fixtures today. A screen is "integrated" when its fixture is deleted and it renders only endpoint output. Order: results (reads aggregates) and EvidenceCase (reads shared trace) first — fiction is most dangerous there.
