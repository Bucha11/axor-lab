# Incident-to-Regression Pilot — checklist

The bounded first engagement (axor-packaging.md §6). Fixed price **$7,500**,
**4–6 weeks**, run on a hosted Security-tier Lab (see `deploy/README.md`). This
doc maps the pilot scope to the features that actually ship, with acceptance
criteria per step — so both sides know what "done" means.

## Scope — exactly one of each

| Pilot item | Where it happens in the deployed Lab |
|---|---|
| one agent | **bring an agent** (`#/agent-ingest`) — instrument an endpoint, or wrap the code with axor-wrap |
| one incident | **import incident** (`#/import`) — the Control Plane → Lab handoff, or a hand-built `axor-lab-incident/v1` package |
| one simulator / tool path | the scenario's `tool-manifest/v1` set (effect classes + driving args) |
| one EvidenceCase | the imported incident's replayed EvidenceCase (`#/i/{id}`), verified bit-identical at import |
| one policy | baseline vs governed comparison — replay the incident under a candidate policy (`axor-lab replay` / counterfactual) |
| a small regression pack | pin the incident's verdict and run the corpus (`axor-lab pin` / regression) |
| a final technical report | the **compliance export** (`#/workspace`) plus a written summary and the next-step recommendation |

## NOT in the pilot (these are the annual contract)
- universal framework integration · full CI · production deployment (Control Plane
  runtime) · a custom adapter · multi-agent fleets · SSO/RBAC.

## Credit rule
100% of the $7,500 is credited toward a first annual contract **≥ $25k signed
within 60 days**. Below that threshold or past 60 days: no credit.

---

## Pre-pilot setup (operator)
- [ ] Hosted Lab deployed per `deploy/README.md`, reachable at `lab.<domain>`.
- [ ] `AXOR_LAB_HOSTED=1`; the three bearer tokens set; store volume persisted.
- [ ] Security-tier license issued (`axor-license issue --workspace-tier security
      --private-lab …`) and installed; `GET /api/license/status` shows
      `workspace_tier: "security"`.
- [ ] `GET /api/audit` returns 200 (not 402) — the paid surface is unlocked.
- [ ] Cloudflare Full-strict; origin firewalled to Cloudflare only.

## Execution loop (with acceptance criteria)
1. **Bring the agent** — [ ] tools detected / manifests declared; effect classes
   and driving args classified (no `UNKNOWN` left).
2. **Capture the incident** — [ ] an incident package is produced (from a real
   Control Plane run, or authored) and imports at `#/import` with
   `replay: "match"` (the recorded verdicts reproduce bit-identically).
3. **EvidenceCase** — [ ] `#/i/{id}` shows the trace, the driving-value
   provenance, and the honest replay-fidelity note (taint_floor reproduced;
   content gates marked not-reproducible).
4. **Policy comparison** — [ ] the incident replays under the candidate policy and
   the verdict delta (baseline vs governed) is recorded.
5. **Regression** — [ ] the verdict is pinned; the corpus run reports the pin as
   held/passed (not skipped).
6. **Approve** — [ ] the incident is signed off in the UI; the approval appears in
   `#/workspace` history and on the incident listing.
7. **Report** — [ ] a compliance report is generated and downloaded; a written
   technical summary + recommendation is delivered.

## Exit criteria (pilot is "done")
- [ ] One EvidenceCase reproduced bit-identically and explained.
- [ ] One policy comparison with a measured verdict delta.
- [ ] One regression pin held under the corpus run.
- [ ] Audit trail shows import → approval → export for the pilot incident.
- [ ] Technical report + next-step recommendation delivered.

## Deliverables to the customer
- The reproducible incident bundle (portable, re-runnable with `axor-lab replay`).
- The compliance report JSON (from `#/workspace`).
- The written technical report + a proposal for the annual tier.
