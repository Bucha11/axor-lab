# Axor Lab — web UI

The SPA for the Lab: landing + catalog, experiment builder, run progress,
results, published experiments (with per-trial EvidenceCases), bring-an-agent,
and the scenario author. Stack: React 18 + TypeScript + Vite 5, Zustand for UI
state, TanStack Query for server state, a dependency-free hash router
(deep links: `#/runs/{run_id}`, `#/e/{publication_id}`,
`#/e/{publication_id}/evidence/{trace_id}`).

## Run it (dev)

Three processes: the two stdlib backends and Vite.

```bash
# 1) publications/catalog server (:8000) + runtime-jobs API (:8010), one process
cd ..     # repo root
python -m lab_server --root ./lab-store --port 8000 --runtime-port 8010

# 2) the SPA
cd frontend
pnpm install
pnpm dev            # http://localhost:5173
```

The Vite dev server proxies:

| prefix      | target                   | note                              |
|-------------|--------------------------|-----------------------------------|
| `/api`, `/e`| `http://127.0.0.1:8000`  | publications server               |
| `/jobs-api` | `http://127.0.0.1:8010`  | runtime-jobs API, prefix stripped |

If the runtime-jobs control surface is token-gated
(`--control-token` / `AXOR_LAB_CONTROL_TOKEN`), paste the token into the
"control token" field on the *bring an agent* screen — it is stored locally and
sent as the bearer on every `/jobs-api` call.

## Seed some data

```bash
# a publication for the catalog / published tabs
axor-lab run examples/banking-exfil-01.axl --out ./bundle --yes
axor-lab publish ./bundle --question "Does governance stop the exfil?" \
    --visibility public --server http://127.0.0.1:8000

# a run for the runs/results tabs: connect a runtime on the "bring an agent"
# screen, compose + assign an experiment in the builder, then drive the
# runtime side with the ingest key (claim → events → complete), e.g. the flow
# in tests/test_runtime_jobs.py
```

Empty states in the UI carry the same commands — no data is faked.

## Build

```bash
pnpm build          # tsc -b && vite build → dist/
```
