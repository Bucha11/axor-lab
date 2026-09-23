# Plans catalog

`axor-plans.json` is a **reference** operator catalog that maps the product's
tiers to the entitlement gate. It is loaded with:

```
axor-lab serve --plans-file docs/pricing/axor-plans.json
```

The catalog is deliberately **not** baked into the code — the server ships an
example placeholder (`EXAMPLE_PLAN_CATALOG`) with no pricing authority, and an
operator supplies the real one here. This file records the owner's tier ladder.

## What is authoritative here vs operator-tunable

- **Tier names, prices, and capabilities** come from the product's packaging
  and are the authoritative part of this file.
- **Numeric resource limits** (`max_suites`, `max_artifacts`,
  `max_hosted_runtimes`) are left `null` (unlimited) on purpose: they are
  operator-tunable and are **not** a product pricing claim. Set them to match
  your deployment's capacity and commercial terms. `null` means no limit.

## The tiers

| key (plan id) | tier | price | capabilities |
|---|---|---|---|
| `free` | Community | $0 | — (local only, no hosted execution) |
| `team` | Team Workspace | $299 / month | `hosted_execution` |
| `security` | Security Workspace | $1,500 / month | `hosted_execution`, `private_registry`, `governance` |
| `enterprise` | Enterprise Platform | $30,000 / year | `hosted_execution`, `private_registry`, `governance`, `control_plane` |

The `free` key is the lapse fallback (a non-active subscription drops here), so a
catalog must always define it.

## How a tier reaches a workspace

An axor-identity access token carries the org's `tier`; on first login the Lab
provisions a workspace for the org and puts it on the catalog plan whose id
equals the tier (`team` → the `team` plan). Identity's free rung is called
`community` and maps to `free`. An unknown tier fails **closed** to `free`.
Machine/static tokens are unaffected.

The tier itself is set by billing in axor-identity. Paddle's webhook moves it
when a subscription starts, lapses or is cancelled, and an operator grants it
by hand for contracted Enterprise. One subscription therefore covers the Lab
and the Control Plane, and the Lab picks the new plan up at the next token
refresh.

## Capabilities

- `hosted_execution` — the platform provisions and runs managed runtimes.
- `private_registry` — an org-private shared suite registry.
- `governance` — permission to **run** suites that declare a governance
  capability (the open format still validates and stores them anywhere; the gate
  is on execution).
- `control_plane` — the Production Governance / Control Plane add-on
  (governed-node operation). Sold as an add-on from ~$500/month plus per-node
  pricing; grant it to a Security workspace with a custom plan, or use the
  Enterprise tier which includes it. Per-node metering is an operator/billing
  concern, not a Lab entitlement limit.

  **Lab does not enforce this one.** The first three are checked by
  `require_capability` and answer 402; `control_plane` is carried so a tier can
  express it end to end, and no Lab route reads it — governed-node operation is
  the Control Plane's, and `architecture-boundary.md` puts entitlement at
  platform level. Granting it changes nothing a Lab user can observe today.
  Whether the HOSTED Control-Plane handoff (`POST /handoff/export`) belongs
  behind it is an open pricing question: the CLI `export-cp` runs locally and is
  free by the Line-1 rule, so gating the hosted route would be consistent, and
  it has not been decided. Tracked in `docs/POST_MVP_PLAN.md` §B10.
