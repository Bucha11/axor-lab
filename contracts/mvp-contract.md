# Axor Lab — MVP Contract vs Product Vision (v2, Suite Platform)

Axor Lab is a **reproducible experiment platform for AI agents**. It is not primarily a governance product; governance is one optional capability that a suite may declare. The MVP is defined against that sentence, and the v1 MVP was not: it was a governance-comparison instrument in which a run without conditions was literally unrepresentable.

Vision and MVP stay separated hard. "Everything is in scope; the order is sequence, not scope cut" is retired — it makes MVP formally include everything and defer nothing.

---

## The workflow the MVP must complete

One line, end to end, and every item below exists to serve it:

```
Choose a Suite → Configure → Preview one Trial → Run → Inspect a Trial
  → Create an EvidenceCase → Pin a Regression → Export an Artifact
```

**No step of this requires governance.** A suite that declares no capabilities and no conditions runs single-arm and completes every step. That is the acceptance shape of the whole product.

## MVP Contract (what v1 must have to exist, and nothing more)

Hosted-first — the user sees the product before installing anything.

1. **Hosted Lab UI** — browser, no install to start.
2. **Zero-setup simulated suite** — open → pick a built-in suite → Run → Run Report → Trial, with no agent connected.
3. **Suite as the one editable document** — `suite/v1`, edited through Basic / Advanced / YAML, all three over the same manifest.
4. **Built-in suites, three at launch** — Blank (the SDK is authorable from nothing), AgentDojo (an import path exists), Budget (the metrics layer is real, since it is nothing but metrics). The catalog renders the remaining design-board cards as unavailable, never as cards that run nothing.
5. **Shared workspace / auth** — one account, platform-level entitlement.
6. **Connected runtime selector** — connect once via the shared Axor adapter, or select an already-connected runtime; no second integration.
7. **Trace import** — reproduce a production incident or a published run.
8. **Assignment execution beside the agent** — the runtime claims the job and runs locally. Lab assigns, never executes.
9. **Trace ingestion with provenance** — the shared `trace/v1` fabric.
10. **Per-trial metrics** — duration, steps, tokens, cost, plus suite-defined keys, recorded on every trial and surviving the artifact round-trip. An unmeasured metric is absent, never zero.
11. **Playground — one trial** — execute a single trial for inspection, with no repeats, no aggregation and no artifact.
12. **Run Report + honest statistics** — rendered from stored aggregates, never recomputed in the client. Wilson intervals for rates; a comparison test only when a suite asks for one.
13. **EvidenceCase** — a curated investigation of one trial. Generic `kind` (latency_spike, budget_overflow, planner_failure, hallucination, …), open for a suite to register its own.
14. **Regression as an executable invariant** — `predicate`, `metric_threshold`, `evaluator_outcome`, and `verdict_sequence` under the governance capability. `expectation` is prose and is never evaluated.
15. **Artifact** — `artifact/v1`, the portable output and the user-facing noun. The UI says Artifact, never "bundle".
16. **Publication** — the immutable public record, `publication/v1`.

### The governance capability, inside the MVP

Governance ships in the MVP as a capability, not as the spine. Its own acceptance surface — paired ungoverned/governed comparison, exact verdict replay, verdict pinning, and the Control Plane handoff (control-plane-handoff.md) — stays green throughout, and is what today's ten acceptance criteria cover. Nothing hardened is deleted; the code moves into `lab_capabilities/governance/` and keeps its behaviour.

A suite opts in by declaring `governance` in `capabilities` and supplying `execution.conditions`. A suite that declares neither never resolves a kernel.

**Local runner stays** — as offline / CI / enterprise / open-research reproduction, not as the onboarding path.

### Explicitly NOT in the MVP

Remote-endpoint input (dropped by decision, INTEGRATION_PLAN §4.6) · Lab-owned gateway or MCP proxy · black-box endpoint eval · arbitrary cloud-code sandbox · multi-agent topologies · population scale · complex attestation chains · a separate entitlement system · any duplicate trace format.

## Product Vision (the whole thing — NOT the MVP)

Cloud arbitrary-code execution · generic endpoint governance · instrumented-endpoint contract · multi-agent topologies as executable suites (planner/workers, reviewer pipelines, attacker/defender) · population-scale experiments · Lab-paid inference · a public suite catalog at scale · full boolean predicate DSL with live authoring · a community suite registry.

## The First/Then/Later sequence

- **Then:** full predicate authoring UI · richer local tool binding · in-app inference · Control Plane promotion of a policy from an experiment result · cloud runner for trusted templates only.
- **Later:** instrumented-endpoint contract · arbitrary cloud code with the full sandbox · multi-agent topologies · population scale.

## Why this order

The MVP executes no untrusted code on Lab servers, so the single most expensive subsystem — the sandbox — is not on the critical path to a working, publishable, reproducible Lab. It demonstrates the whole idea (author → run → inspect → curate → pin → publish) on one vertical slice, and every later capability is built outward from that spine rather than in parallel with it.

The one inversion against v1: the slice that must work first is the **governance-free** one. The governance slice is the one the repo implements end-to-end today, which is exactly why it is not the thing left to prove.
