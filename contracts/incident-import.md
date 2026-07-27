# Incident & trace import (v1)

Resolves a real confusion. **Replay requires an Axor-produced trace**, but the
commercial funnel says "bring us your production incident" — and that incident
happened *before* Axor was installed. Both are true. They are two different
paths, and conflating them under one "trace import" mode was the error.

## The rule

> **Replay re-runs the judge, not the agent — but it needs the judge's inputs on
> record.**

`decide(π(x), policy)` is pure, so a recording of its inputs is enough to
recompute a verdict without the agent, the model, or the tools. Those inputs are
the value ledger (labels, `sources`, `derived_from`) and `arg_bindings`. **Only
an Axor adapter emits them.** A LangSmith / OTel / application-log trace records
*what was called*, never *where each value came from* — so there is nothing for
the gate to read, and no exact replay is possible from it.

This is not a gap to paper over. It is the honest boundary, and it has a clean
product answer.

## Two paths, named separately

| Input | Path | Fidelity | What you actually get |
|---|---|---|---|
| **Axor `trace/v1`** (an Axor-instrumented run — yours or a published bundle) | **Trace replay** — `POST /api/incidents`, UI `#/import` | `explicit_flow_tracked` | exact verdict replay · full EvidenceCase · policy/kernel comparison · regression pinning · `exactly_replayable` claims |
| **Generic trace / logs** (LangSmith, OTel, app logs, a postmortem) | **Incident reconstruction** — `POST /incidents/reconstruct`, UI `#/reconstruct` | `heuristic_attribution` → then a real run | a *scenario draft* extracted from the incident → run it under Axor → real trace, real EvidenceCase |

## Why reconstruction, not "degraded replay"

The tempting move is to guess provenance from a generic trace — substring-match
the attacker IBAN in a tool result against a later tool argument and call it
lineage. That guess is unsound in both directions (it over-taints *and*
under-taints), so **it must never produce a verdict**, and `lab_runner/
reconstruct.py` never does.

But it *is* excellent at one thing: **seeding a scenario.** From a messy incident
trace, Lab extracts and proposes:

- the tools that were called → draft `tool-manifest` entries (arg/result shapes,
  likely effect class, a `noop_stub` simulation for anything side-effecting)
- the task the agent was given → `task`
- the suspicious untrusted content and where it entered → `injection` +
  `fixtures.injection_placement`
- the harmful call that followed → a draft `violation` predicate
- the legitimate outcome → **left empty on purpose** (see below)

The user confirms/corrects the draft, and now there is a real `scenario/v1`. Run
it under Axor (ungoverned / governed / compare) and you get a **genuine**
conformant trace, a real EvidenceCase, and a regression case.

So the generic trace's job is to **author a scenario**, not to be replayed. That
is the whole resolution.

### What the extractor refuses to invent

`task_success` — the predicate that says the agent did its job. The recording
shows what went *wrong* and never what right looks like. Inventing it would
quietly define utility, which is half of every number this product reports, so
the draft leaves it empty, lists it in `unresolved`, and
`POST /runs/local {reconstructed}` **refuses to run without it** rather than
report a gate's cost as zero for the wrong reason.

### Two things the draft must get right or the run lies

Both are pinned by `tests/test_reconstruct.py`:

- **The untrusted-field pattern drops the index.** `runner._expand_field` matches
  `transactions[]`, never `transactions[1]`. A literal index taints nothing, the
  sink argument is minted clean, and the run reports ASR 0 in *both* arms — an
  attack that silently cannot land. The concrete index stays on
  `injection_placement`, which is where the payload literally goes.
- **The sink argument is renamed to `recipient`, and the rename is disclosed.**
  The reference runner drives a read → sink slice whose argument is literally
  `recipient` (`runner._faithful_input_key`). Keeping the incident's own name
  would emit a violation predicate on an argument the runner never binds: the
  attack lands, the predicate never fires, ASR 0 for a reason nobody can see.

## Where the agent comes from (reconstruction gives you a world, not an agent)

The most important thing reconstruction does **not** recover: **the agent
itself.** A trace records the decisions an agent made; you cannot rebuild the
decider from a recording of its decisions. So the split is:

| Piece | Recovered from the incident trace? |
|---|---|
| the task | ✅ → `task` |
| tools that existed, their shapes | ✅ → draft `tool-manifest`s |
| **what the tools returned, including the poisoned field** | ✅ → `fixtures` + `injection_placement` |
| the harmful call that followed | ✅ → `violation` predicate |
| **the agent that decided to make that call** | ❌ — it is the thing under test |

Therefore the pitch must be exact:

> ✅ "We rebuild the incident's **world** and run **your agent** through it."
> ❌ "We replay your incident." — nothing of the agent is replayed.

**Wrapping is still required.** What the customer is spared is not the
integration — it is having had Axor installed *at the moment the incident
happened*.

### What they gain by not needing production tools

Because tool results are frozen from the trace into fixtures, the reconstruction
needs **none of their production tools, backends, or credentials**. The
environment is deterministic; only the agent is live:

```
world      (tool results + the injection)  ← fixtures, from the incident trace
agent      (the thing that decides)        ← live, wrapped, theirs
governance (labels, gates, decide)         ← the kernel
```

This is the same property that keeps an attack scenario from creating a real
incident (`threat-model.md`): side-effecting tools are simulated by default, and
a reconstructed sink gets `noop_stub` — the attempt is recorded, nothing is
pretended.

### Fidelity ladder for the agent

| Agent used | What it proves | Honest label |
|---|---|---|
| **Their agent, wrapped** (`POST /incidents/reconstruct/compose` → assign to a connected runtime) | governance behaviour on *their* agent — the actual deliverable | the real result |
| **A stand-in** (`POST /runs/local {reconstructed}`, `scripted@…`) | that the *mechanism* fires on this shape of incident | must be marked "not your agent — a stand-in on your incident's world" |

The stand-in is legitimate for a first-contact demo before any integration
exists, and it is the honest bridge from "we cannot touch your agent yet" to a
real run. It must never be presented as a measurement of their agent — so the
label is **not** left to whichever screen renders the number:
`store._limitations_for` stamps it onto the publication whenever a reconstructed
scenario ran against a scripted provider.

### Why bother, if the agent has to be wrapped anyway

Because the incident stops being a one-off event and becomes a **repeatable
test**: run it 30 times and get a rate with a CI, run it under a different
policy, pin it as a regression, re-check it on every future kernel and policy
change. You no longer wait for the next incident to learn anything.

## What this means for claims

- The run itself is real: its trace is conformant and its verdicts replay
  exactly. The claim that it reproduces **the incident** is what does not hold.
- A scenario drafted this way carries `reconstructed_from: {fidelity}` — a
  machine-readable field, not prose — and `store._limitations_for` turns it into
  a **mandatory limitation** on any publication built from that run. A limitation
  that depends on someone remembering to add it is not a limitation.
- Any EvidenceCase rendered from `heuristic_attribution` data carries the
  non-soundness warning (`provenance-semantics.md` §5) and is labeled
  *indicative*, not authoritative.
- A run of a reconstructed scenario against a **stand-in** carries a second
  mandatory limitation naming that fact. Both are derived from the artifacts, so
  neither depends on a renderer remembering them.

## The commercial story (stronger, not weaker)

> Your first incident gets **reconstructed** — we rebuild it as a scenario and
> show you what governance does to it. Every incident after that is **exactly
> replayable**, because Axor was there when it happened.

That is a reason to install, not an excuse.

```
incident (pre-Axor) → import logs → assisted scenario reconstruction (the WORLD)
                                          + wrap their agent (the DECIDER)
                                          → governed/ungoverned run
                                                                              ↓
                                                                  EvidenceCase + regression
                                                                              ↓
                                                Axor installed → future incidents replay exactly
```

## Lifecycle

**Trace replay** is a run:

```
validating → importing → replaying → analyzing → completed
```

**Incident reconstruction** is *not* a run lifecycle — it is an authoring
pipeline that ends in a scenario:

```
importing → extracting → scenario draft → user confirms → (then a normal run:
                                                          demo | connected_runtime)
```

Modeling it as a "run" was part of the confusion: nothing is executed during
reconstruction. `POST /incidents/reconstruct` writes nothing, executes nothing
and decides nothing; it is a pure function of its input.

## Honest phrasings

- ✅ "We can reproduce the *governance verdicts* of any Axor-instrumented run,
  exactly."
- ✅ "We can reconstruct a pre-Axor incident as a scenario and measure governance
  on it."
- ✅ "We rebuild your incident's world from the trace and run *your* agent
  through it — wrapped, governed vs ungoverned."
- ✅ "You do not need your production tools or credentials for this — the tool
  results are frozen from the incident."
- ❌ "Import your incident and replay it" — not if Axor was not there, and never
  for the agent's behaviour.
- ❌ "We infer provenance from your existing traces" — heuristic attribution is
  never presented as sound.
- ❌ Showing a stand-in agent's result as their agent's result.

## Refusals, and why each one is a refusal

| Input | Response | Reason |
|---|---|---|
| an Axor `trace/v1` posted to `/incidents/reconstruct` | `422`, pointing at replay | degrading a conformant trace to a guess is the worst trade available: the verdict is right there |
| a recording with no tool calls | `422` with the shapes that are accepted | "we found nothing" is a fact the user needs stated, not a draft with no tools in it |
| a confirmed draft with empty `task_success` | `400` | the utility side of the result would mean nothing |
| more than 500 calls | `413` | that is a data export, and it belongs in the CLI |
