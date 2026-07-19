# Axor Lab — Claim Boundary (v1)

The most-repeated review point across two rounds: Lab sometimes sells replay wider than it reproduces. This document draws the line once, and every surface (Published page, export, EvidenceCase, results) must classify each claim as one of exactly two kinds.

---

## Two claim kinds

### Exactly replayable
A **governance decision over a fixed trace**. Deterministic, carries no confidence interval, reproducible bit-for-bit forever (given the pinned kernel).

> On trace T, axor-core 0.4.2 returns **DENY** on `send_money` because the driving recipient is `untrusted_derived`.

What makes it exact: `decide` is a pure function of the projection and policy (paper O1). Given the same recorded events and the same kernel, the verdict cannot differ. `axor lab replay ./bundle` recomputes exactly this.

### Statistically reproducible
An **aggregate over live runs**. Stochastic, carries a CI, reproduced by re-running — matched *within the interval*, never bit-for-bit.

> In 30 live repetitions, governed agents reached cooperation rate **0.88 [0.83, 0.92]**.

What makes it statistical: the model is sampled anew each run. `axor lab run --repeats 30` produces a fresh sample; a reproduction matches within CI (see publication reproduction kind `fresh_live`).

---

## The rule

**A behavioral delta is never exactly replayable.** These are all statistical, not exact:

- task utility of the governed agent
- cooperation / defection rate
- number of subsequent attacks after an intervention
- federation behavior after a member is contained
- any "governance raised/lowered X" over outcomes

Because a DENY changes the trajectory, and what the agent does *after* the DENY (retry? abandon? succeed honestly?) is not in the frozen ungoverned trace. Recovering it requires a fresh live run. Replay gives you the **verdict on the recorded events**, not the **counterfactual continuation**.

## Consequence for each surface

- **Published page** separates a *Exactly replayable* block (verdict-on-trace claims) from a *Statistically reproducible* block (aggregate claims). Never one merged "reproducible" badge.
- **Export** prints two reproduce commands with distinct meaning: `replay` (verdicts, exact) and `run` (fresh live, new sample). Already in the results export; the labels must say which is which.
- **EvidenceCase** twin toggle offers **three** views, not two, because "governed" is ambiguous:
  1. **Observed: ungoverned** — the trajectory actually recorded.
  2. **Counterfactual: policy replay** — the same trace, showing the verdict the gate *would* return (DENY). This is exact for the verdict, but it does NOT claim the agent reached an identical call under governance.
  3. **Observed: governed live twin** — only shown if a governed run was actually executed; otherwise absent, not faked.
  The default trace is (1); (2) is the counterfactual; (3) appears only with real data.
- **Results** significance/finding text may state a statistical claim; it must not describe an aggregate as "reproduced exactly".

## Two phrasings to retire

- "reproduce the governance conclusion exactly" — replace with "replay the governance **verdicts** exactly (behavioral outcomes are statistical)".
- "every reframing lands in the same fiber" (as if one EvidenceCase proves it) — one case shows a **content-independent decision over this provenance/effect state**. The equivalence-class claim is the theorem's (paper §5), argued there; an EvidenceCase illustrates it, it does not prove it. Phrase: "this verdict is content-independent — it turns on provenance, not wording".

## Regression wording

A pinned regression does **not** mandate DENY forever — policy can intentionally change. The guarantee is:

> Future kernel/policy versions must **surface any change from the pinned expected verdict**; the user then decides: regression (unintended) or approved baseline update (intended).

Not "must still DENY it."
