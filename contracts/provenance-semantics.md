# Axor Lab — Provenance Semantics (v1)

The question raised in every review round: **how does the runtime know `v_recipient` was derived from `v_inj`** when an LLM produced the recipient string? A plain HTTP wrapper sees "tool returned text" then "model called a tool with an argument" and cannot know the second came from the first. This document answers it, sourced from the Axor paper (§5, O2), and states plainly what is and isn't knowable — so no surface claims soundness it doesn't have.

---

## 1. The honest core: no per-token attribution exists

Nobody can reliably attribute which output token came from which input token through a transformer. Any design that claims "the model copied *this* untrusted span into the recipient" is guessing. Lab does **not** attempt per-token attribution.

Instead, provenance is **conservative and structural**, exactly as the paper's O2 (label soundness): it **over-taints, never silently under-taints**. Over-tainting costs some utility (a legitimate value may be flagged untrusted); under-tainting would be a soundness hole (an attacker value slips through clean). Lab chooses the safe direction, always.

## 2. Conservative join at the model-call boundary

The rule that makes lineage tractable:

> When the model emits a value (a tool-call argument), that value's provenance is the **join of every untrusted value that was live in the model's context at the moment of the call.**

So for the slice: at the `send_money` call, the model's context contained `v_inj` (the untrusted transaction description). The recipient the model produced therefore inherits `untrusted_derived`, with `derived_from = [v_inj]` (all untrusted context values) and `sources = ⋃ their sources`. We did not prove the model copied `v_inj` into the recipient — we **conservatively assume any model output could depend on any untrusted input in context**, and label accordingly. If the recipient were in fact prompt-given, it would still be tainted here (over-taint) — and that is the known cost the paper measures (banking −17±7pp), recoverable only by a declared allowlist, never by guessing.

This is why `trace.value.derived_from` for a `model_extraction` value = all untrusted context values at the call, not a guessed subset.

## 3. The closed constructor set (why induction works)

Provenance is defined over a **closed set of value constructors** (paper §5.4); every value in the ledger is built by exactly one:

| constructor | meaning | label rule |
|---|---|---|
| `constant` | literal / prompt-given | trusted / prompt_given |
| `external_read` | returned by an untrusted tool field | untrusted_derived (roots a taint) |
| `mint` | freshly created by the agent | per policy |
| `parse` | structured extraction from another value | inherits the parsed value's join |
| `cross_process_in` | arrived across a process/federation boundary | re-derived per trust level (v2 Ch.1) |
| **model_extraction** (Lab addition) | produced by a model call | **join of all untrusted context values** (§2) |

Because the set is closed, the soundness argument is an induction over constructors: if each constructor's join rule preserves "sources ⊇ every untrusted influence", the whole ledger does. **Adding a constructor requires adding a join rule, or the taint suite fails** — the set cannot silently grow. (The paper is explicit this is pen-and-paper induction over the closed set, not machine-checked; Lab inherits that caveat and states it.)

## 4. What this requires of the producer — and what it forbids

Establishing this lineage requires the labels to **travel with values inside the loop**. That is only possible when the runtime carries labeled values through the agent's execution:

- **`wrapped_code`** — the adapter wraps tool I/O and the model call, so every value passes through the ledger. Full `explicit_flow_tracked` fidelity.
- **`instrumented_endpoint`** — the agent emits value-carrying events (SDK/MCP proxy). Fidelity `explicit_flow_tracked` if labels are carried, else `heuristic_attribution` (flagged).
- **`black_box` endpoint** — sees only task-in/answer-out. **Cannot** build a ledger; produces no conformant trace; governance impossible. This is why the schema omits `black_box` from `trace.producer.mode`.

## 5. Fidelity levels (what a trace is allowed to claim)

- `explicit_flow_tracked` — lineage from the closed constructor set with conservative join. **Sound** (over-taints not under-taints) for **explicit flow**. The EvidenceCase may show the provenance chain as authoritative.
- `heuristic_attribution` — a weaker best-effort mapping (e.g. events without carried labels). **Not sound**; the EvidenceCase renders a warning and never presents it as a guarantee.

## 6. The explicit-flow boundary (stated, not hidden)

Provenance covers **explicit** flow: a value influencing another through data. It does **not** cover **implicit** flow: the model's behavior conditioned on untrusted content without that content appearing in a value (e.g. the injection changes *which* tool the agent picks, not an argument's data). The paper draws this boundary explicitly (O2, shared with FIDES) and so does Lab. An EvidenceCase for an explicit-flow breach is authoritative; implicit-flow influence is out of scope and labeled as such. Claiming to catch implicit flow would be the dishonest over-reach we refuse.

## 7. Consequence for the slice

`v_recipient` in the trace: `transformations=[model_extraction]`, `derived_from=[v_inj]`, `sources=[external_read:read_txns...]`, `labels=[untrusted_derived]`. This is not "we detected the model copied the IBAN" — it is "the recipient was produced by a model call whose context held an untrusted value, so conservatively it is untrusted_derived, and the egress gate denies." Sound, honest, and exactly what the paper's guarantee supports — no more.
