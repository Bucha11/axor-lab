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

## Which aggregates may back a statistical claim

The two kinds above say what a claim IS. This says which numbers are allowed to
make one, and it is a second line inside the statistical kind.

- **Derived** — the metric is a predicate the evidence can re-evaluate: a
  verifier reads each frozen trace, applies the scenario's own predicate, and
  gets the number back. `ASR` and `task_success` are these. Only a derived
  aggregate may back a `statistically_reproducible` claim, and the publication
  records `statistics_integrity: recomputed_from_traces`.
- **Self-reported** — the metric is the runner's own measurement and appears in
  no trace: latency, tokens, spend. A verifier can re-apply the declared
  `estimator` to the reported per-trial values and check the ARITHMETIC; it
  cannot check the observations. Such an aggregate is published, carries NO
  claim, and the publication records `statistics_integrity: self_reported` —
  which is what `publication.schema.json` already meant by the word.

Refusing the second kind is not the safe choice: a suite declaring
`mean(duration_ms)` was once unpublishable, which made an honest figure
unpublishable while teaching nobody anything about attestation. Publishing it
UNMARKED is the unsafe one — a latency mean and an attack-success rate look
identical in a results table and are not the same kind of claim.

Consequences: the publish handshake refuses a `rate` over a metric outside the
derived registry (an arbitrary label must not launder into a server-recomputed
claim) and refuses a comparison TEST on a self-reported metric; the Run Report
and `axor-lab report` both print the tier per row; and an aggregate over a
metric no trial measured is refused outright — weaker than self-reported, since
it is not reported at all.

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
