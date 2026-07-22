# Axor — Agent Connection (v1) — canonical

How a user's agent connects to Control Plane and/or Axor Lab. **Two separate products**; this file is the source of truth for the connection topology, and overrides any doc that implies one backend or module flags.

## Core rule

```
one agent → one framework/generic adapter → one local axor-core
                                                   │
                       ┌───────────────────────────┴──────────────────────────┐
                       ▼                                                        ▼
              PlaneClient (axcp_…)                                  LabRuntimeClient (axlab_…)
              → control.useaxor.net                                 → lab.useaxor.net
              live operation                                        experiment execution + evidence
```

Two independent outbound connections. Either can be absent. Enforcement is always local in axor-core; neither backend connects into the user's infra or executes the agent.

## Credentials — separate, scoped

```
AXOR_CONTROL_URL / AXOR_CONTROL_TOKEN   axcp_…   (Control Plane only)
AXOR_LAB_URL     / AXOR_LAB_TOKEN        axlab_…  (Lab only)
```

No single all-powerful token. No `AXOR_URL` + module discovery. A shared local config file may hold two independent `[control_plane]` / `[lab]` sections — local convenience, not a merged server.

## Three scenarios

**CP only** — agent → adapter → axor-core → PlaneClient → control URL. CP gets telemetry/heartbeat/live events/topology; sends desired state (pause/resume/stop/replan/budget). Lab absent ⇒ no errors.

**Lab only** — agent → adapter → axor-core → LabRuntimeClient → lab URL. Lab owns its runtime registry, credentials, job queue, experiment plans, trial assignments, trace ingest/store, Results, EvidenceCases, artifacts. It never calls Control Plane for mandatory registration. CP absent ⇒ no errors.

**Both** — one adapter, one local axor-core, one trace schema, one process; two clients with different URLs/credentials/reconnect-loops/lifecycles. Stopping one service doesn't break the other.

## The AgentAdapter interface (custom agents are a required scenario)

Framework adapters (axor-claude, axor-langchain) are ready-made implementations, not the only path. A custom agent implements:

```python
class AgentAdapter(Protocol):
    async def describe(self) -> AgentDescription: ...
    async def run(self, input: AgentInput, execution_context: ExecutionContext) -> AgentRunResult: ...
    async def reset(self) -> None: ...

@dataclass
class AgentDescription:  name: str; adapter_kind: str; models: list[str]; tools: list[ToolDescription]
@dataclass
class AgentRunResult:    output: object; trace: Trace; status: str
```

The adapter wraps the user's **existing** entrypoint — the user does not move their agent's logic into a new Axor runtime. `LabRuntimeClient` sees only `AgentAdapter`; it never knows whether the agent is Claude, LangChain, OpenAI, custom Python, or an external-process wrapper.

## Lab runtime job loop (the execution contract)

Lab **assigns**, the runtime **executes** locally and uploads. Lab never dispatches a tool or calls the agent.

```
GET  /runtime/jobs                                   poll
POST /runtime/jobs/{id}/claim                         claim
POST /runtime/jobs/{id}/trials/{trial_id}/events      stream kernel events (shared trace schema)
POST /runtime/jobs/{id}/trials/{trial_id}/complete    finalize
```

```python
while True:
    job = await lab.poll_job()
    if not job: await sleep(...); continue
    await lab.claim(job); await adapter.reset()
    result = await adapter.run(job.input, build_lab_context(job))
    await lab.upload_trace(job.trial_id, result.trace)
    await lab.complete_trial(job.trial_id, result.status)
```

## Client ownership (no shared platform client)

No mandatory `AxorConnection.connect()` that discovers modules. Instead: `PlaneClient(...)` and `LabRuntimeClient(...)`, separate. Shared low-level libs are fine (`axor-transport`: retry, durable queue, auth helpers), but public/domain clients stay separate. **`LabRuntimeClient` is owned by / published from the Lab repo**; Control Plane does not own the Lab client, and axor-core takes no dependency on axor-lab.

## Identity & token-exchange (how "log in once" works without one backend)

Separate backends do **not** mean separate logins. Identity is a distinct layer from the runtime backends; sharing it is what gives "authorize once, use both" without merging servers.

**The layer.** A shared identity/account service holds the org (or solo) login and, per account, an `entitled_products` list (a billing statement — see axor-packaging.md). It is not a runtime backend and holds no jobs, traces, or desired state.

**The flow (token-exchange):**
```
1. user logs in ONCE to the account (org SSO, or a personal account for solo/Community)
2. account session asserts identity + entitled_products
3. user opens Lab → Lab backend verifies the session with the identity layer
   → if "private_lab" ∈ entitled_products (or the free tier), Lab MINTS its own axlab_ runtime token
4. same for Control Plane → mints axcp_ — only if "control_plane" ∈ entitled_products
```
The user types credentials once; each product still issues and owns its own scoped runtime token. No shared all-powerful token, no module flags on a backend, no second manual login.

**No free ride across products.** Token-exchange mints a product's token only if the account is entitled to that product. If the org bought Lab but not Control Plane, opening CP yields no `axcp_` — entitlement gates minting. "Authorized once" ≠ "gets the other product free"; it means one login, and each product granted only if paid for.

**Solo / Community path.** A solo researcher uses a **personal account** (not an org): logs in once, `entitled_products` = the free Lab tier, token-exchange mints an `axlab_` for hosted use the same way. Local BYOK / offline runs need no account at all — the runner is credential-less against a local target. So the free funnel never requires an org account; identity scales from "no account (local)" → "personal account (Community hosted)" → "org account (Team+)".

**Standalone Lab identity.** Standalone Lab (CP absent) runs its own identity or a personal-account login; it does not call CP for auth. The `ControlPlaneIdentityProvider` below is only for *integrated* deployments that want one org SSO across both — an optional server-side link, never a dependency.

## Integrated deployment (server-side only)

Integrated ≠ shared backend. URLs stay separate. Integration is between servers via Lab-side ports:

- `ControlPlaneRuntimeProvider` — Lab shows runtimes already known to CP
- `ControlPlanePromotionBackend` — promote a Lab artifact/config into CP
- `ControlPlaneIdentityProvider` — *optional* integrated variant: use CP's org SSO as the shared identity layer, so one org login spans both. (Standalone Lab uses its own identity instead — see Identity & token-exchange above. Token-exchange itself is not CP-specific.)

It enables: cross-links (CP incident → Lab experiment, Lab EvidenceCase → CP runtime), shared org login, promotion. It does **not** merge stores: Lab jobs/Results stay in Lab; CP desired state stays in CP.

**Runtime identity mapping** (not one physical record across two backends):

```json
{ "lab_runtime_id": "lab_rt_123", "external_refs": { "control_plane_runtime_id": "cp_rt_789" } }
```

Standalone Lab may have no `control_plane_runtime_id` at all.

## Standalone means a separate running product

Standalone Lab = the Lab backend runs on its own, CP fully off. It is **not** one app with `lab=true, control_plane=false`. Minimal deploy: axor-lab backend + frontend + Lab DB/storage; the runtime connects straight to the Lab URL.

## Acceptance criteria

- **CP without Lab:** Lab off; agent connects to CP URL; runtime appears in CP; heartbeat; pause/stop apply locally; no errors from missing Lab.
- **Lab without CP:** CP off; runtime registers on Lab URL; gets a job; runs a trial locally; uploads trace; Lab builds Results; no errors from missing CP.
- **Both:** two URLs, two scoped tokens, one local adapter used by both clients; CP gets telemetry, Lab gets experiment traces; stopping one doesn't break the other.
- **Integrated:** CP and Lab still on separate URLs; Lab imports runtime refs via the CP provider; Lab keeps its own jobs/Results; CP keeps its own desired state; **no `lab=true/control_plane=true` in runtime registration**.

## What was deleted from the earlier draft

`modules: {control_plane, lab}` · module discovery · shared backend URL · combined bootstrap runtime endpoint · a shared `AxorConnection` managing both · "standalone = one app with a flag" · one shared connection token · shared trace *store* (only the schema is shared).
