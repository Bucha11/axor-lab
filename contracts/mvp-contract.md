# Axor Lab — MVP Contract vs Product Vision (v1)

The earlier spec said "everything is in scope; the order is sequence, not scope cut." That phrasing is retired: it makes MVP formally include everything and defers nothing, which is how a research tool eats the main product for months. Vision and MVP are now separated hard.

---

## Product Vision (the whole thing — NOT the MVP)

Cloud arbitrary-code execution · generic endpoint governance · instrumented-endpoint contract · multi-agent game runtime · arbitrary interaction topology · population-scale experiments · Lab-paid inference · public catalog at scale · full boolean predicate DSL with live authoring. All real, all later.

## MVP Contract (what v1 must have to exist, and nothing more)

Hosted-first — the user sees the product before installing anything (installing-then-seeing is the wrong funnel). Thirteen items:

1. **Hosted Lab UI** — browser, no install to start.
2. **Zero-setup demo** — Axor-hosted template: open → pick experiment → Run → Results → EvidenceCase, no agent.
3. **Shared workspace / auth** — one account, platform-level entitlement (not a Lab-specific system).
4. **Connected runtime selector** — connect once via the shared Axor adapter, or select an already-connected runtime; no second integration.
5. **Trace import** — reproduce a production incident or a published run.
6. **Experiment builder** — scenarios × conditions × repeats, binding a `runtime_ref`.
7. **Assignment execution beside the agent** — the runtime claims the job and runs locally (Lab assigns, never executes).
8. **Trace ingestion with provenance** — the shared `trace/v1` fabric.
9. **Results + honest statistics** — Wilson/bootstrap/McNemar, rendered from stored aggregates.
10. **EvidenceCase** — the three-mode view over a trial's trace.
11. **Replay + regression pinning** — exact verdict replay; pin (trace, expected verdict).
12. **Simple bundle / publication** — `bundle/v1` + immutable `publication/v1`.
13. **Promote policy to Control Plane** — create a production configuration from an experiment result (a ref between shared artifacts, not an export/import).

**Local runner stays — but as offline / CI / enterprise / open-research reproduction, NOT the onboarding path.**

### Explicitly NOT in the MVP
Lab gateway / MCP proxy owned by Lab · black-box endpoint eval · arbitrary cloud-code sandbox · multi-agent games · population scale · complex attestation chains · a separate entitlement system · any duplicate trace format. Each is either removed outright (architecture-boundary.md) or deferred to Vision; none blocks a useful first Lab.

## The First/Then/Later sequence (within the Vision, after MVP ships)

- **Then:** full boolean predicate authoring UI · richer local tool binding · BYOK inference in-app · Control Plane export · cloud runner for *trusted templates* only.
- **Later:** instrumented-endpoint contract · arbitrary cloud code (with the full sandbox) · multi-agent games · arbitrary topology · population scale.

## Why this order

The MVP executes no untrusted code on Lab servers (local runner only), so the single most expensive subsystem — the sandbox — is not on the critical path to a working, publishable, reproducible Lab. It demonstrates the entire idea (author → run → EvidenceCase → replay → publish → regression) on the vertical slice, and every later capability is built outward from that spine rather than in parallel with it.
