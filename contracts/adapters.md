# Axor — Adapters (v1)

**The adapter is the only place a user's agent physically meets Axor.** Kernel, gates, backends, schemas — all invisible to them. If this layer isn't clear, nothing else is actionable, because the adapter is the entire answer to "what do I actually have to do?"

---

## 1. What an adapter is

An adapter **wraps an existing agent entrypoint**. It does not reimplement the agent: the user's loop, prompts, and orchestration stay theirs. The adapter adds three things around them:

1. **Describes** the agent's shape — tools, models — so Axor can reason about it.
2. **Intercepts two decision points** — the model call and the tool call — so the kernel can build provenance and gate effects.
3. **Emits a trace** of what happened, in the shared `trace/v1` schema.

That's the whole job. Everything downstream (EvidenceCase, replay, statistics, enforcement) is derived from a conformant trace produced by these three duties.

## 2. Why exactly two interception points

They are the only two places that matter:

- **Model-call boundary** — where new values are *created* from context. This is where provenance is assigned by conservative join (`provenance-semantics.md`): a value the model produces inherits the join of every untrusted value live in its context.
- **Tool-call boundary** — where effects *leave*. This is where the kernel gates: `decide(π(driving args), policy) → ALLOW | DENY`.

Everything else in an agent loop is bookkeeping. Intercept these two and you have both the provenance graph and the enforcement point; miss either and you have neither.

## 3. Where the adapter sits

```
              user's agent loop (unchanged)
                         │
   ┌─────────────────────┴──────────────────────┐
   │  ADAPTER                                    │
   │   describe()          → tools, models  ─────┼──→ tool manifests
   │   on model call       → mint values    ─────┼──→ provenance (conservative join)
   │   on tool intent      → GATE FIRST     ─────┼──→ ALLOW → dispatch (real | simulated)
   │                                              │    DENY  → not dispatched, refusal + record
   │   emit events         → trace_sink     ─────┼──→ trace/v1
   └─────────────────────┬──────────────────────┘
                         │
              local axor-core  (ledger, 9 gates, decide)
                         │
         ┌───────────────┴───────────────┐
   PlaneClient (axcp_)            LabRuntimeClient (axlab_)
   live operation                  experiment trials
```

Enforcement is **local** — the decision never crosses the network. Both clients sit *outside* the adapter and know nothing about each other.

## 4. Three kinds of adapter

| Kind | What it knows | User cost |
|---|---|---|
| **`axor-claude`** (framework) | the Anthropic loop: `tool_use` blocks, tool results, streaming; converts Anthropic events into kernel events; drives `IntentLoop` | near-zero — point it at an existing session |
| **`axor-langchain`** (framework) | LangChain/LangGraph middleware: model-call and tool-call interception, context policy, tool governance, trace generation | add middleware to an existing agent |
| **generic / custom wrapper** | nothing framework-specific — the user implements `AgentAdapter` around their own entrypoint | implement 3 methods + declare tools once |

**The generic path is first-class, not a fallback.** Custom agents are a required scenario: framework adapters are pre-built conveniences, not the only way in. Downstream behavior is identical — the framework adapters just pre-fill the interception the generic wrapper does by hand.

Choosing: on a supported framework → framework adapter; otherwise → generic.

## 5. The interface

```python
class AgentAdapter(Protocol):
    async def describe(self) -> AgentDescription: ...
    async def run(self, input: AgentInput, ctx: ExecutionContext) -> AgentRunResult: ...
    async def reset(self) -> None: ...

@dataclass
class AgentDescription:
    name: str; adapter_kind: str; models: list[str]; tools: list[ToolDescription]

@dataclass
class AgentRunResult:
    output: object; trace: Trace; status: str
```

`LabRuntimeClient` sees only `AgentAdapter` — it never knows whether the agent is Claude, LangChain, OpenAI, custom Python, or a wrapper around an external process.

## 6. `ExecutionContext` — what an adapter receives for one run

The context is built by whoever drives the run: `LabRuntimeClient` for a trial, the runtime for production.

```python
@dataclass
class ExecutionContext:
    condition:   Condition        # enforcement on/off + kernel_ref + policy_ref
    tools:       ToolRegistry     # bound callables — real, or simulated/fixtured (Lab)
    fixtures:    Fixtures | None  # Lab: canned tool results with $injection placed
    trace_sink:  TraceSink        # append-only; the ONLY way events leave the adapter
    limits:      Limits           # wall-time, token, tool-call budget
    cancel:      CancelToken
```

Rules:
- `condition.enforcement == "off"` → **observe-only**: the kernel still labels values and records decisions, but never blocks. *ungoverned ≠ unobserved.*
- In Lab, `tools` is the **simulated** registry: a `side_effecting` tool never dispatches for real unless its manifest explicitly opts in (`threat-model.md` — an attack benchmark must not create a real incident).
- `trace_sink` is the only egress for events. An adapter must not open its own HTTP connection.

## 7. The gate runs BEFORE dispatch (normative)

```
agent emits a tool intent
  → adapter binds each argument to a ledger value  (arg_bindings)
  → kernel.decide(π(driving args), policy)
       ALLOW → dispatch (real tool, or simulator/fixture in Lab)
               → result minted into the ledger (per-field labels)
       DENY  → tool is NOT dispatched; a refusal is returned to the agent; the decision is recorded
  → agent continues
```

**Gating after execution is replay, not governance.** A governed live run must never dispatch first and judge after — by then the effect has happened.

## 8. Provenance hooks — the two writes

- **On tool result:** mint a value per field; fields listed in the manifest's `untrusted_fields` get `labels: [untrusted_derived]` and `sources: [{kind: external_read, origin_ref: …}]`.
- **On model output:** mint the produced value with `transformations: [model_extraction]` and `derived_from` = **all untrusted context values live at that call** (conservative join — no per-token attribution exists; over-taint is the sound direction).

An adapter that performs both writes earns `provenance_fidelity: explicit_flow_tracked`. One that can't emits `heuristic_attribution` and must say so — the EvidenceCase renders a warning rather than presenting it as sound.

## 9. Two different resets

- **`adapter.reset()`** — clears the *agent's* state between trials (conversation, memory, scratch files).
- **`tool-manifest.reset.strategy`** — restores *tool-side* state (`fixture` reload, `snapshot_restore`).

Both must run between trials, or trials aren't independent — and `statistics.md` assumes independence for every interval and test it computes.

## 10. One adapter, two clients

The adapter has no knowledge of Control Plane or Lab. The clients bolt on outside it:

```python
adapter = MyAgentAdapter(...)                    # or axor_claude.adapter(session)

plane = PlaneClient(url=CONTROL_URL, token=AXCP_TOKEN)                 # live operation
lab   = LabRuntimeClient(url=LAB_URL, token=AXLAB_TOKEN, adapter=adapter)  # experiments
```

- **Production:** the user runs their agent normally; `PlaneClient` ships telemetry and applies desired state (pause/resume/replan) to the local session.
- **Lab trial:** `LabRuntimeClient` polls a job, builds the `ExecutionContext`, calls `adapter.run()`, uploads events + trace, completes the trial.

One integration of the agent; two independent outbound connections (`agent-connection.md`).

## 11. What an adapter must NOT do

- Reimplement the agent's control flow.
- Contain Lab or Control Plane business logic — no job polling, no product HTTP clients inside the adapter.
- Make governance decisions itself — it *calls* the kernel; the kernel decides.
- Behave differently per product — one adapter, two clients attached outside.
- Open its own network egress for traces — everything goes through `trace_sink`.

## 12. Failures

- Adapter raises → trial `failed` with a reason, **recorded not dropped** (missingness is reported, per `statistics.md` §5).
- `limits` exceeded → `failed: limit_exceeded`.
- `cancel` token → cooperative stop; already-completed trials are preserved.
