# Axor — Packaging (v1) — SINGLE SOURCE OF TRUTH

Canonical for tier names, prices, included modules, usage metrics, bundles, hosted/self-hosted split, upgrade and pilot rules. Where `lab-economics.md`, `cp-monetization.md`, `spec-lab.md`, or any mock disagree with this file, **this file wins**. Those docs describe *why*; this one fixes *what and how much*.

---

## 0. The frame: one ladder, two modules — not two products

Private Lab and Control Plane are **two modules under one Axor account and one commercial ladder**. Control Plane is a **production add-on to a Lab workspace**, not a separate customer journey. One organization, one entitlement model, one renewal — two modules, two price components. (We do **not** promise a literal single invoice: enterprise procurement may still cut a separate order form. "One ladder," not "one bill.")

```
Community  →  Team Workspace  →  Security Workspace  →  + Production Governance add-on  →  Enterprise Platform
   $0            $299/mo             $1,500/mo               from $500/mo (CP nodes)          from $30k/yr
```

### Three ways in, two meters (2026-11)

Buyers arrive by one of three journeys, and each sees the plans for it (Pricing page selector, onboarding chooser):

| Journey | Who | Meter | Plans shown |
|---|---|---|---|
| **Coding agents** | a team governing Claude Code / Codex through their hooks; no production runtime | **developer seats** | Community (1 developer free) · **Coding $25/developer/mo** · Enterprise |
| **A production agent** | a team wrapping its own production agent | **governed nodes** (daily peak) | Community · Team · Security · Enterprise |
| **Both** | coding agents and a production fleet | nodes **and** seats | Team (incl. **5 developer seats**) · Security (incl. **25 developer seats**) · Enterprise |

A **developer** is someone whose coding agents reported in the month, counted once however many sessions, agents or trees they ran — never a session: a coding session is not a governed node, or one developer would be a fleet by lunchtime. The Control Plane learns it from the profile each coding session posts about itself (kind `coding`, an opaque hashed developer id — never an address). Seats past a rung's allowance are **$25/developer/mo** on every rung.

## 1. Canonical price matrix

| Tier | Price | What it unlocks | Module |
|---|---|---|---|
| **Community** | **$0** | Public Lab, local BYOK runs, local private projects, replay, local regressions, public publish | Lab (free) |
| **Coding** | **$25/developer/mo** (card) | Claude Code + Codex hooks, one taint ledger per repository, agent trees (planner → readers → implementer), checked findings, context flow + cut, every session on the Control Plane, reputation per agent/model; 1 governed node as in Community | Coding agents + CP |
| **Team Workspace** | **$299/mo** (card) | Hosted private workspace, private scenarios/incidents, shared scenarios, basic CI, limited retention; includes hosted trial allowance; **5 developer seats** (+$25/developer/mo) | Lab (paid) |
| **Security Workspace** | **$1,500/mo** (card) | Everything in Team + production-incident workflow, scheduled regression CI + history, approvals, policy/kernel comparison, compliance/audit exports; larger trial allowance; **25 developer seats** (+$25/developer/mo) | Lab (paid) |
| **Production Governance add-on** | **from $500/mo** | Control Plane runtime enforcement, first governed nodes, production integrations — activated on an existing workspace | CP (add-on) |
| **Enterprise Platform** | **from $30k/yr** | Security Workspace + SSO/RBAC + self-hosted/VPC runner + SLA + negotiated governed-node band + private benchmark registry + support | both, contracted |

There is exactly one "Team," one "Security," one "Enterprise" across the whole product. The old parallel "Lab Team vs CP Team" and "Lab Enterprise vs CP Enterprise" are retired — CP is the **Production Governance add-on**, priced per governed node on top of a workspace, never a second Team/Enterprise tier.

## 2. What you meter (and how it's messaged)

Buyers pay for **capabilities and organizational maturity**, not volume. Trials are an included allowance that protects infra cost and deters abuse — never the headline.

- Pricing page says: *"Security Workspace — $1,500/mo, including 50,000 hosted trials and 100 retained EvidenceCases."* (Team includes 10,000 hosted trials.)
- It does **not** say "$0.04 per trial." Overage exists but is never the primary message.
- **EvidenceCases** are included with a generous limit, never the meter (metering them makes users avoid creating them).
- **Inference is BYOK by default** — Axor does not resell tokens.

## 3. Hosted vs self-hosted — different metering, stated plainly

Usage-metering and offline licensing don't mix: an air-gapped install can't phone home a trial count. So:

**Hosted:** `workspace subscription + included hosted trials + hosted storage overage`. Usage allowance lives in the billing system, not the license file.

**Self-hosted:** `annual flat/banded license + organizational features + runner/node ceiling + support`. **Unlimited local execution within the purchased tier** — we do not technically count self-hosted trials; scope is bounded by contract and node ceiling, not telemetry.

## 4. The entitlement — one license, both modules

The same Ed25519-signed, offline-verifiable license file as `cp-monetization.md` §4 carries the whole ladder. Modules are flags, not separate licenses:

```json
{
  "organization": "acme",
  "workspace_tier": "security",
  "modules": { "private_lab": true, "control_plane": true },
  "governed_node_ceiling": 20,
  "self_hosted_runner": true,
  "expires_at": "..."
}
```

Expiry degrades to read-only EE; it never disables safety features (Line 1). Hosted trial allowance is enforced in billing, not in this file — so the same license works air-gapped.

## 5. A concrete bundle (so $30k isn't a random number)

```
Axor Security Platform — $30,000/year
  Includes:
    - Private Lab: Security Workspace
    - SSO / RBAC
    - self-hosted / VPC runner
    - scheduled regression CI
    - compliance exports
    - Control Plane for 10 governed nodes
    - support (SLA)
  Additional governed nodes: $75 / node / month
```

This is the Enterprise Platform floor, itemized. Node overage is the expansion lever inside the same contract.

## 6. The pilot — tight scope, bounded credit

**Incident-to-Regression Pilot — $7,500, 4–6 weeks.** Fixed scope, exactly:
- one agent · one incident · one simulator/tool path · one EvidenceCase · one policy · a small regression pack · a final technical report.

Explicitly **not** in the pilot: universal framework integration, full CI, production deployment, custom adapter. (Those are the annual contract, not six founder-weeks at $7.5k.)

**Credit rule:** 100% of the pilot credited toward a first annual contract **≥ $25k signed within 60 days**. Below that threshold or past 60 days, no credit — otherwise a small buyer purchases a pilot and effectively gets a near-free first year of Team.

## 7. Target ACV (honest ranges, not entry-tier fiction)

The economics summary is **target ACV**, not "every customer pays this":

```
Private Lab:    $2.4k–18k ARR  self-serve (Team $299 → Security $1,500/mo)
                $30k+ ARR       enterprise
Control Plane:  $6k–20k ARR     early production (add-on nodes)
                $30k–150k+ ARR  enterprise (node bands + org complexity)
```

Public Lab is not a P&L line — it is CAC reduction and credibility.

## 8. Upgrade rules

- Community → Team → Security is self-serve, card, prorated, same account.
- Production Governance is an **add-on toggle** on a Team/Security workspace, not a migration — the workspace, scenarios, policies, and manifests carry over unchanged (per `control-plane-handoff.md`: reuse policy + manifest, add production bindings).
- Enterprise Platform is contracted; it wraps a Security Workspace rather than replacing it.
- A pilot converts into any annual tier; credit applies per §6.

## 9. Free / paid line (unchanged, restated for canon)

Free forever: Public Lab, local BYOK runs, local private projects, replay, local regressions, all safety features (gates, EvidenceCase *capture*). Paid: hosted private org workspace, scheduled CI, approvals, retention, SSO, compliance exports, fleet, production runtime. Trigger is organizational use, never a safety feature and never hobby-scale privacy.
