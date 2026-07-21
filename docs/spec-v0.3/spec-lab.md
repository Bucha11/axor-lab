# Axor Lab — Specification (v0.3)

**Standalone** research product at `lab.useaxor.net`. Not a tab on the Control Plane — its own front door. A researcher arrives with their own agent and a question, no Axor deployment required. Same engine underneath (kernel, gates, replay), different surface and audience.

**Positioning (fixed):** two economies (see `lab-economics.md`) — **Public Lab** is free-for-research (trust, distribution, CAC reduction) and **Private Lab** is a standalone paid product for teams (private incidents, EvidenceCases, regression CI), the first paid rung of the same Axor license that Control Plane sits atop. Not "lead-gen, not revenue" — Private Lab is revenue; Public Lab is the funnel into it. Launches *with* the paper as its reproducibility companion. And it is the **top of the funnel into the Control Plane product**.

**This spec is the product narrative; the engineering contract lives in `contracts/`** — 9 JSON Schemas (trace, scenario, tool-manifest, predicate, condition, experiment, bundle, publication, attestation), plus statistics.md, claims.md, provenance-semantics.md, domain-model.md, the protocol/threat/lifecycle/handoff docs, mvp-contract.md, and a fully-worked vertical-slice.md. A no-dependency `validate.py` + `validate_slice.py` machine-checks every slice example against the schemas (currently green: 8/8). Where this narrative and a contract disagree, the contract wins. The vertical slice is the readiness test: when it runs locally on simulated tools and reproduces from its bundle, the core exists.

---

## 1. How your agent reaches Lab — the shared runtime connection

**Lab is the experiment & evidence layer over Axor runtime traces (see `contracts/architecture-boundary.md` — read first).** Lab does not connect to, execute, or proxy your agent: the Axor runtime adapter (the same one that serves Control Plane) runs the agent locally and pushes traces outward; Lab hands out experiment assignments and reads the resulting traces. You connect a runtime **once** — both modules see it.

| Mode | What happens | Source |
|---|---|---|
| **Demo** | Axor-hosted template, no agent — zero-setup: open → Run → Results → EvidenceCase | Lab-hosted |
| **Connected runtime** | an existing Axor runtime claims an assignment, runs your agent locally, pushes events | runtime |
| **Trace import** | analyze a production incident or a published run | import |
| **Offline runner** | CI / air-gapped / private code — run beside the agent, upload only the bundle | offline_runner |

"Connect runtime" issues a scoped ingest/job key for the shared adapter; existing Control Plane users just **select** an already-connected runtime — no second integration. There is no Lab gateway, no MCP proxy owned by Lab, and no black-box endpoint evaluation (all removed: Lab must not dispatch tools, hold tool credentials, or be a synchronous enforcement boundary — that is runtime territory).

Privacy is the default: the runtime executes locally and sends observations, never raw bodies; the offline runner keeps code and execution on your machine and uploads only the signed bundle.

## 2. Run modes — ungoverned is first-class

Lab is for studying *your* agent, not for imposing governance on it. Three run modes; the first is the default and stands alone:

- **ungoverned (observe-only).** Run the agent as-is. The proxy watches and records everything — that's how EvidenceCase is built (§6) — but enforces nothing, blocks nothing. This is the plain "just run my agent and show me what it did" mode, valuable on its own, and also the honest baseline. **ungoverned ≠ unobserved:** observation is always on; enforcement is what's off.
- **governed.** The same run with axor-core gates enforcing.
- **compare.** Both on identical scenarios — the paired governance Δ, with CI over repeats. This is what produces Table-1-shaped results.

Governance is a comparison the researcher opts into, never a tax on every run. A researcher can live entirely in ungoverned mode (Lab as a plain agent-observability harness) and only reach for governed/compare when they want the delta.

## 2.5. EvidenceCase — the investigation surface (core, not optional)

Aggregates ("governance helped 23/30") are the least interesting thing Lab shows. The differentiating screen is the **single-trial EvidenceCase**: for any trial, the exact injection the agent read, how provenance carried to the sink, the tool call it emitted, the gate that fired, and why the verdict is invariant to reframing — with a governed/ungoverned twin and a one-click path to pin it as a regression. Without this, Lab is just another eval dashboard; with it, Lab surfaces the mechanism that is Axor's actual contribution. The results table links into it per trial.

**Replay honesty carries here too:** the verdict on a recorded trace replays bit-identical; the agent's behavior *after* a DENY does not (that needs a fresh live run). The EvidenceCase states this in the UI — verdict = deterministic replay, downstream continuation = live/stochastic.

## 3. Authoring first (unchanged from v0.2)

The core is writing your own scenarios (data, not code: task, tools, injection, breach/success predicates — the gates stay the kernel's) and composing your own bench. AgentDojo and other benchmarks import as presets. Format in `formats/bench-format.md`.

## 4. Two reproducibility layers (unchanged)

- **live** — runs the model: stochastic, n + 95% CI + the appropriate test (statistics.md) (honest "inconclusive" under n<10). Same as the paper's Table 1.
- **replay** — governance verdicts over frozen traces: bit-identical. Publish freezes the traces; anyone reproduces the governance conclusion exactly, a fresh behavioral run is live-with-CI.

## 5. Experiment types (unchanged)

Benchmark (1 agent × suite, defended vs undefended — the Table-1 shape) · Game (multi-agent; players are singles or federations, composition is a variable).

## 6. Lab → Control Plane bridge (new in v0.3)

The funnel's job: a researcher who just watched governance contain an attack on *their* agent is the warmest possible Control Plane lead. The bridge is offered, never forced, and only when it's genuinely earned.

- **Trigger — earned, not nagged.** The upgrade path surfaces only after a real result: an experiment where governance changed the outcome on the researcher's own agent (a contained breach, a fabricated→honest flip). Not a banner on arrival; a footer on a result that already impressed them.
- **What carries over.** The wrapped agent and the emitted config are the *same artifacts* the Control Plane consumes — the Lab wrap IS a Control Plane deployment minus the live topology. "Run this governed agent in production" reuses the config the researcher already built; nothing is re-done.
- **The one honest difference stated plainly.** Lab is measurement (does governance help, on scenarios); Control Plane is operation (govern a live agent, with the plane, topology, notifications). The bridge says exactly that — "you've measured it here; run it for real there" — and links the config across, not a fresh setup.
- **Direction is one-way by default.** Lab → CP is the funnel. CP → Lab exists too (a CP user can push a production agent into Lab to experiment safely off-prod), but that's a convenience, not the funnel; it reuses the same connected runtime, pointed at a CP-registered agent — no re-integration.
- **Free/paid line (canonical: `axor-packaging.md`).** Public Lab and local individual workflows stay free; private organizational Lab features follow the workspace tier (Team/Security); Control Plane is a production add-on on the same ladder — a module activated on the existing workspace, not a separate journey, with policy and manifest carried over unchanged. A *public* Lab run stays free and reproducible forever; a *private* org workspace is paid.

## 7. Export, catalog, out-of-scope (unchanged)

MD/PDF export with reproduce command; publish = reproducible bundle.

**The bundle is versioned, not vibes.** `bundle/v1` carries at minimum: schema version, scenario + bench, condition configs, agent/tool manifest, **kernel version + policy/config hash**, model provider + model id + inference params, seeds, raw attempts, traces, verdicts, aggregate results, content hashes, author, license. Kernel version is load-bearing: the same trace under a different `decide` can yield a different verdict, so a bundle without it is not reproducible. "config + seeds + traces" was shorthand; this is the real list.

**Provenance of a result is a status, not a checkmark.** A published run is tagged: *Lab-executed* (ran on our infra), *self-reported* (local run, bundle uploaded — integrity-hashed but not independently run), *independently reproduced* (a third party re-ran it). The catalog never visually equates a verified run with an uploaded JSON.

AgentDojo preinstalled + template.axl + seed game catalog. Out of scope v1: UI gate/game-logic authoring, leaderboards, live conditions blended into deterministic aggregates.

## 7.5. Core surfaces & run lifecycle

Beyond authoring/results, two surfaces are first-class:

- **Landing + catalog** — the top of funnel, with three entry points at descending barrier: *Explore experiments* (browse published runs, see the mechanism, fork — lowest barrier, no agent needed), *Reproduce a run* (drop a bundle, re-run its governance verdicts), *Bring your agent* (code/endpoint/traces). The catalog lists published experiments with their provenance status badge and reproduction count. Arriving straight at "bring your agent" is too high a first step; exploration is the door.

- **Published experiment page** — the immutable public record at `/e/{id}`: question, result with honest CI, methodology + artifacts (the versioned bundle, kernel version and config hash pinned), stated limitations (including the interested-result caveat), counted independent reproductions each with its own CI, reproduce command, fork, and citation. This is what a published run *is* — not a screenshot of a number but a re-runnable, forkable, citable artifact. The catalog links here.

- **Run lifecycle — an explicit state machine**, surfaced honestly: `draft → validating → ready → queued → provisioning → running → analyzing → completed | failed | cancelled → published`. Validation happens *before* run (tool bindings resolved, predicates type-checked, `$inputs` resolve, cost/trial estimate shown, privacy stated). Failures are specific and actionable, each tied to a stage: unknown-tool predicate (validating), missing key (provisioning), model timeout / rate limit with **partial results kept** (running), agent crash (running), malformed trace excluded-and-flagged (analyzing). Cancel keeps completed trials; retry can target only the failed subset. The UI never shows a spinner with no state.

## 8. Terminology (canonical)

One word per concept, everywhere: **ungoverned** (enforcement off, observation on) — never "undefended" or "bare" in UI. **governed** (enforcement on). **compare** (both, paired). Model runs are **stochastic**; only governance **verdicts** over frozen traces are **deterministic** / bit-identical. The word "deterministic" never attaches to a live run.

## 9. Build maturity — Vision vs MVP (see contracts/mvp-contract.md)

Vision and MVP are separated hard; "everything is in scope, just later" is retired — it makes the MVP formally include everything and de-risks nothing. The **MVP Contract** is the vertical slice productized (local runner · curated AgentDojo · minimal typed predicate DSL · simulated tools+fixtures · trace/v1 with provenance · ungoverned+governed live · replay · EvidenceCase · private bundle · publication · regression). Explicitly **not** in MVP: arbitrary cloud code, generic endpoint governance, multi-agent games, arbitrary topology, population scale, Lab-paid inference. Sequence after MVP:
- **First:** local runner + trace mode + replay + EvidenceCase + publish/catalog + one AgentDojo import + one authored benchmark. This alone demonstrates the whole idea and executes no untrusted code on our servers.
- **Then:** full structured-predicate DSL with sample-trace preview, local tool binding, BYOK inference, Control Plane export, cloud runner for trusted templates.
- **Later:** instrumented-endpoint contract, arbitrary cloud code execution (with the sandbox below), multi-agent games, arbitrary topology, population-scale.

**Sandbox is a real subsystem, not a phrase.** "Runs in the lab sandbox" means, when cloud code execution lands: gVisor/Firecracker-class isolation, CPU/RAM/disk/wall-time limits, ephemeral FS, no host mounts, egress deny-by-default with an API allowlist, secret injection without persistence, dependency lock, output-size caps, kill/cancel, audit trail, retention policy. Until that exists, code execution is local-only.

**Cost model.** Live runs cost inference money (conditions × repeats × scenarios can be large). v1 is **BYOK or fully local** — the researcher's key or their machine, so Lab isn't underwriting unbounded fan-out. A pre-run estimate (trial count × model) is shown before any live run; Lab-paid inference, if ever, is metered with free limits and anti-abuse.
