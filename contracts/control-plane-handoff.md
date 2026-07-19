# Axor Lab → Control Plane Handoff (v1)

The earned bridge, made precise. Retires "nothing is re-done."

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
Earned, not nagged: the handoff surfaces only after a result where governance changed the outcome on the researcher's own agent. Free/paid line (canonical: `axor-packaging.md`): Public Lab and local individual use stay free; private organizational Lab is paid (Team/Security); Control Plane is a production add-on on the same ladder, activated on the existing workspace — not a separate product to sell from scratch.

## Second funnel (production-incident path)
Import a production incident/trace → reproduce the attempted consequence in Lab (trace mode) → test a policy → pin a regression → export the policy+manifest → open in Control Plane and add production bindings. This is the path with a real buyer on the other end.
