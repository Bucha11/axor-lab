# Axor Lab — Economics (v0.1)

> Canonical packaging, tiers, and prices are defined in **axor-packaging.md**. If this document conflicts with it, axor-packaging.md wins. This doc is the reasoning; packaging is the price list.

The missing half of `cp-monetization.md`. That doc priced the Control Plane; this one explains Lab economics. **Canonical tier names, prices, modules, and pilot rules live in `axor-packaging.md` (single source of truth); where this doc and packaging disagree on a number, packaging wins.** This doc is the reasoning; packaging is the price list. It does **not** introduce a second product or a second bill: Private Lab is the **first rung of the same Axor license**, so a team climbs Community → Team → Security → Enterprise as one ladder, and Control Plane is an expansion on top, not a separate checkout.

Correction to earlier framing: Lab is **not** "lead-gen, not revenue." Lab has two parts with different economics.

---

## 1. Two Labs, two economies

| Part | What it is | Economic role |
|---|---|---|
| **Public Lab** | public catalog, reproduce, fork, local runner, public bundles | trust, distribution, open research → CAC reduction / credibility |
| **Private Lab** | private scenarios, incidents, EvidenceCases, regression CI, team workspace | a standalone paid SaaS — $5–30k ARR/team |
| **Control Plane** | runtime enforcement over production traffic | expansion product — $25–150k+ ARR/customer |

A team can pay for Private Lab for years without ever wiring up Control Plane runtime. The funnel is real but not one-way-to-CP:

```
public experiment → private evaluation / incident → regression suite → (optionally) production → Control Plane
```

## 2. The value unit is not a trace

Braintrust/LangSmith/Arize/Galileo meter traces, spans, storage. Copying that pricing literally is a strategic error: it makes Axor generic observability, competing on integration count where it is weakest. Axor's billable unit is its territory:

> a **private incident → EvidenceCase → policy fix → regression case**.

Everything downstream of "that agent did something bad, reproduce it and prove the fix holds" is where willingness-to-pay lives. Price the workflow, not the data volume.

## 3. Free / paid line (stitched to the two CP lines)

The CP doc's two lines still govern: **Line 1 — safety is free forever** (all gates, replay, EvidenceCase *capture*, fail-closed). **Line 2 — organizational use is paid** ("how do WE run this," not "is MY agent safe"). Applied to Lab:

**Free (Community):** browse public catalog · download bundles · replay public verdicts · fork public scenarios · local runs with BYOK · publish *public* experiments · a small number of private drafts. Charging to reproduce someone's public research would kill distribution — never do it.

**Paid (Private Lab):** private data · production incidents · team workspaces · private EvidenceCases · saved regression suites · scheduled/GitHub CI · policy/kernel-version comparison · long retention · approval workflow · audit history · compliance/security exports · Control Plane integration.

The trigger for paid is **org features** (private data, seats, scheduled CI, retention, SSO), never the mere fact of privacy at hobby scale, and never a safety feature. A solo researcher with a private paper draft = free. A company with 10 seats and incident-CI = paid. Same test as CP.

## 4. What to meter (and what not to)

Not **seats** — two engineers can carry huge business value; seat-metering taxes the wrong thing. Not **EvidenceCases as the sole usage metric** — users would avoid creating cases to avoid the bill, which breaks the very workflow the product exists for (same failure mode budget-caps solved for amplification: don't tax what should happen freely).

Meter instead:

```
base workspace subscription
  + included private trial volume   (the bulk metric — trials / processed data)
  + storage / retention overage
EvidenceCases: included with a generous limit, never the meter
inference: BYOK by default (don't resell expensive tokens or argue over run costs)
```

Landing-page framing stays simple: *"Includes 10,000 private trials and 100 retained EvidenceCases."*

## 5. Tiers — one ladder, stitched to the CP license

Same Ed25519-signed, offline-verifiable license file as CP (protocol §6); Lab org-features and CP features check the *same* license. Expiry degrades to read-only EE, never disables safety (Line 1).

| Tier | Price shape | Unlocks |
|---|---|---|
| **Community** | $0 | public Lab, local BYOK runs, public publish, small private drawer |
| **Team** | ~$199–399/mo, card | private workspace, private scenarios/incidents, saved regression suites, shared scenarios, basic CI |
| **Security** | ~$1,000–2,500/mo | multiple agents, production-incident workflow, scheduled regression CI + history, policy/kernel comparison, compliance exports, approval workflow |
| **Enterprise** | $25–50k+/yr | SSO/RBAC, VPC/self-hosted runner, retention/legal-hold, audit export, SLA, private benchmark registry |
| **Control Plane** | separate contract, per governed node | runtime enforcement (the CP monetization doc) |

This is one continuous upgrade — card-swipe Team → Security → procurement Enterprise → CP expansion — not two parallel price sheets. It fixes the old gap where the only path was "free Lab → big CP Enterprise contract" (long cycle): now there's a card-swipe middle rung that monetizes the segment that *already has budget and a real agent* (a company with an incident), earlier and with less friction.

## 6. First revenue motion — the paid pilot, not an Upgrade button

Before self-serve exists, sell Lab as a **paid design-partner pilot**. This is the CP doc's design-partner motion (§6) made concrete for Lab, and it is the honest test of whether people buy the *core workflow* or only treat Lab as a showcase for CP.

**Incident-to-Regression Pilot — $5–15k, 4–6 weeks.** Deliverables: import one real or synthetic incident · a reproducible scenario · ungoverned/governed comparison · EvidenceCase · policy configuration · regression suite · CI integration · production-deployment recommendations.

Credit the pilot into the first annual contract:

```
Pilot:                         $7,500
Annual Lab + Control Plane:   $30,000
$7,500 credited toward year one.
```

You get cash, a real willingness-to-pay signal, the feature the buyer actually needs, a case study, and no premature promise of universal self-serve.

## 7. Additional revenue (on-territory, optional)

- **Verified benchmark study** for an AI vendor wanting to show injection-resistance / effect-of-governance with independent reproductions: ~$10–30k, **mandatorily labeled sponsored**, methodology frozen before results (the buyer cannot edit method post-hoc — the moment they can, Lab's credibility is gone).
- **Private benchmark registry** (Enterprise add-on): a company's own scenarios, incidents, release gates, policy versions, audit proof — potentially more valuable than the public Lab.
- **Self-hosted runner** (banks/healthcare): execution stays in their infra; only metadata or a signed bundle leaves; Lab provides the control surface and verification. Standard enterprise upsell.

## 8. What NOT to sell

Access to public papers · forking a public experiment · publishing open research · per-replay charges · a "safety certificate" without very strict methodology · $9–20 micro add-ons. Cheap developer-SaaS add-ons drag Axor into an integration-count race with Braintrust/LangSmith/Phoenix — off Axor's territory, which is `incident → EvidenceCase → policy → regression → runtime enforcement`.

## 9. Economics summary

```
Public Lab    → CAC reduction / credibility (not a direct P&L line)
Private Lab   → $5–30k ARR per team           (standalone SaaS)
Control Plane → $25–150k+ ARR per production customer (expansion)
```

First dollars come through paid incident-to-regression pilots, not an Upgrade button — because pilots are what prove whether the EvidenceCase+regression workflow is bought as real value, or whether Lab only works as a beautiful window into Control Plane. Sequence-wise this slots into the CP motion: pilots now → Team self-serve at first ~10 external users → Security/Enterprise as compliance questions arrive.
