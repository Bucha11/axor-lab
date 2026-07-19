# Axor Lab — MVP Contract vs Product Vision (v1)

The earlier spec said "everything is in scope; the order is sequence, not scope cut." That phrasing is retired: it makes MVP formally include everything and defers nothing, which is how a research tool eats the main product for months. Vision and MVP are now separated hard.

---

## Product Vision (the whole thing — NOT the MVP)

Cloud arbitrary-code execution · generic endpoint governance · instrumented-endpoint contract · multi-agent game runtime · arbitrary interaction topology · population-scale experiments · Lab-paid inference · public catalog at scale · full boolean predicate DSL with live authoring. All real, all later.

## MVP Contract (what v1 must have to exist, and nothing more)

The vertical slice, productized. Twelve items:

1. **Local runner** — executes on the researcher's machine (`axor lab run`); Lab server never runs untrusted code in v1.
2. **Curated AgentDojo adapter** — one imported benchmark, materialized as `scenario/v1` objects.
3. **Minimal typed scenario DSL** — the `predicate/v1` subset actually needed: `event_match` + `matcher_map` + `equal/not_equal/in/provenance_is` + `$inputs`. Boolean composition (`all/any/not/sequence`) is defined in the schema but MAY be post-MVP in the *authoring UI*; the runner supports it. (This resolves the First/Then contradiction: an authored benchmark in v1 needs at least this typed subset, so it is in First, not deferred.)
4. **Simulated tools + fixtures** — every tool runs through a simulator by default; `side_effecting` tools never fire for real without explicit opt-in. Attack benchmarks cannot create incidents.
5. **Canonical trace/event format with provenance** — `trace/v1`, produced by the wrapped local runtime with `explicit_flow_tracked` lineage.
6. **Ungoverned live run** — observe-only (enforcement off), the plain baseline.
7. **Governed live run** — enforcement on.
8. **Decision replay** — `axor lab replay`, exact over frozen traces.
9. **EvidenceCase** — the three-mode view over one trial's trace.
10. **Private bundle upload** — `bundle/v1`, hash-verified.
11. **Public/unlisted publication** — `publication/v1`, immutable, with the exact/statistical claim split.
12. **Regression pinning** — pin (trace, expected verdict); surface changes.

### Explicitly NOT in the MVP
Arbitrary cloud code · generic endpoint governance · multi-agent game builder · arbitrary topology · population scale · Lab-paid inference. Each is in the Vision; none blocks a useful first Lab.

## The First/Then/Later sequence (within the Vision, after MVP ships)

- **Then:** full boolean predicate authoring UI · richer local tool binding · BYOK inference in-app · Control Plane export · cloud runner for *trusted templates* only.
- **Later:** instrumented-endpoint contract · arbitrary cloud code (with the full sandbox) · multi-agent games · arbitrary topology · population scale.

## Why this order

The MVP executes no untrusted code on Lab servers (local runner only), so the single most expensive subsystem — the sandbox — is not on the critical path to a working, publishable, reproducible Lab. It demonstrates the entire idea (author → run → EvidenceCase → replay → publish → regression) on the vertical slice, and every later capability is built outward from that spine rather than in parallel with it.
