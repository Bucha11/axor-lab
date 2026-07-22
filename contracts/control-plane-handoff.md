# Axor Lab → Control Plane Handoff (v1)

The earned bridge, made precise. Retires "nothing is re-done."

## The shape: promote via a server-side port, not export, not a shared store

Lab and Control Plane are **separate products with separate stores** (agent-connection.md). Promotion is **not** export-package → verify → import, and **not** a shared workspace either. It is a Lab-side `ControlPlanePromotionBackend` port that pushes/references a Lab artifact into CP, cross-server:

```
Lab experiment result → Promote (server-side, via ControlPlanePromotionBackend) → CP config + bindings/credentials/topology
```

The Lab policy/manifests/regressions live in **Lab**; promote hands CP the config to create a production configuration from, plus the production-only additions below. Cross-links (Lab EvidenceCase ↔ CP runtime) go through the same server-side integration and the runtime identity mapping, not a merged store.

## What carries over (reused, identical)
- The **validated policy** (`condition.policy` + `config_hash`) — the exact governance config the researcher measured.
- The **tool manifests** (`tool-manifest/v1`) — args/result schemas, effect model, driving args.
- Any **regression cases** pinned in Lab — they become Control Plane regression tests.

## What must be added for production (NOT reused — this is the honest part)
- Real **tool bindings** (Lab ran simulators/fixtures; production calls real tools).
- **Credentials** / secrets (vault, per §14.2 / federation vault).
- **Deployment topology** (single agent vs federation, where nodes run).
- **Notifications, owners, failure policy** (operational, absent in Lab).

## The wording
Not "nothing is re-done." The honest line:

> Reuse the same validated policy and tool manifest; add production bindings, credentials, topology, and deployment settings.

## Trigger (unchanged)
Earned, not nagged: the handoff surfaces only after a result where governance changed the outcome on the researcher's own agent. Free/paid line holds: Lab stays free; crossing to Control Plane is crossing to the product.

## Second funnel (production-incident path)
Import a production incident/trace → reproduce the attempted consequence in Lab (trace mode) → test a policy → pin a regression → export the policy+manifest → open in Control Plane and add production bindings. This is the path with a real buyer on the other end.
