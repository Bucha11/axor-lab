# Deploying Axor Lab (self-hosted, single node)

What this gets you: the screen API and the web app behind a proxy that
terminates TLS and rate-limits, with durable storage. What it is not: a
horizontally-scalable service. Read **Limits** before you commit to it.

```
cp .env.example .env                       # set AXOR_LAB_CONTROL_TOKEN
docker compose up --build                  # → http://localhost:8443
```

## Limits — decide against these first

- **One node.** Live run state (in-flight assignments and their events) is held
  in memory by a single process. Two replicas would each answer for runs the
  other is executing, so do not scale `lab`. Everything finished — suites,
  artifacts, evidence, regressions, workspaces — is on disk and shared by
  nothing.
- **A restart drops runs in flight.** Finished work survives (verified below);
  a run mid-execution does not. Deploy when nothing is running, or accept it.
- **Rate limiting lives at the proxy, not in the app.** The app has none. If you
  put something other than `deploy/nginx.conf` in front of it, carry the limits
  over — especially the one on `/guest-session`.
- **Checkout is not wired to a payment provider.** `/billing/checkout` returns a
  placeholder URL. The webhook, activation and lapse-to-free paths are real, so
  a plan is granted by an admin today. See the maturity table in `README.md`.
- **No TLS until you provide certificates.** The shipped config listens on plain
  HTTP and says so. Three lines in `deploy/nginx.conf` switch it.

## First boot

1. `openssl rand -hex 24` → `AXOR_LAB_CONTROL_TOKEN` in `.env`. There is no
   default; the stack refuses to start without one, because a Lab with no token
   is an open write surface.
2. Put your price list where `AXOR_LAB_PLANS_FILE` points. The image ships
   `docs/pricing/axor-plans.json`; tier names and prices are the product's,
   numeric limits are yours to set for your capacity.
3. Optional — human login: point `AXOR_LAB_IDENTITY_JWKS_URL` at an
   axor-identity deployment. A token's `org` + `tier` provision a workspace on
   the matching plan, failing **closed** to free on an unknown tier. Without it,
   only the control token authenticates.
4. Leave `AXOR_LAB_GUEST_SESSIONS` empty until you have watched the rate limits
   under real traffic. It is the one route an anonymous stranger can spend
   resources on.

Check it came up the way you meant:

```
docker compose logs lab | head -5
#   storage:  durable → /data          ← "in-memory (lost on restart)" means
#   web app:  /app/web/dist               you forgot the volume
#   auth:     token-gated
```

## TLS

Put `fullchain.pem` and `privkey.pem` in `deploy/certs/`, then in
`deploy/nginx.conf` replace `listen 8080;` with the three commented lines above
it and map the port in `docker-compose.yml`. Renew by replacing the files and
`docker compose restart proxy` — nginx reads them at start.

## Backup and restore

One directory is the whole system of record: the `labdata` volume (`/data` in
the container). Everything else in the image is rebuildable from the repo.

```
# backup
docker run --rm -v axor-lab_labdata:/d -v "$PWD":/out alpine \
  tar czf /out/axor-lab-$(date +%F).tgz -C /d .

# restore into a fresh stack
docker compose down
docker volume rm axor-lab_labdata
docker volume create axor-lab_labdata
docker run --rm -v axor-lab_labdata:/d -v "$PWD":/in alpine \
  tar xzf /in/axor-lab-YYYY-MM-DD.tgz -C /d
docker compose up -d
```

There is no schema migration to run: the store is JSON files under `/data`,
content-addressed where immutability matters. A backup taken while a run is
executing is consistent for everything finished, which is everything a backup is
for here.

## Upgrade

1. Back up (above).
2. `git pull && docker compose up -d --build`.

Rollback is the previous image plus the pre-upgrade archive. Watch the first
`docker compose logs lab` for the storage line — an upgrade that silently lost
its volume reports `in-memory` and will look fine until the first restart.

## When something is wrong

- **The UI loads but every action 401s.** The browser is not sending the control
  token. Check `auth: token-gated` in the logs and that your client carries
  `Authorization: Bearer …`, or wire identity login so humans get their own.
- **A live run appears frozen in the browser while the server logs progress.**
  Something between the browser and the app is buffering the SSE stream.
  `deploy/nginx.conf` disables buffering on `/runs/{id}/events` specifically;
  a different proxy in front needs the same.
- **429s under normal use.** The `api` bucket is 20r/s with a burst of 40 per
  address. A NAT'd office can exceed that legitimately — raise the rate rather
  than remove the limit, and keep `/guest-session` tight regardless.
- **`storage: in-memory (lost on restart)`.** `AXOR_LAB_DATA_DIR` is unset or
  the volume did not mount. Stop before anyone puts work in.
- **A build fails cloning axor-wrap / axor-eval.** They are pinned to git refs
  because the API Lab needs is not on PyPI. Both repositories are public; set
  `GITHUB_TOKEN` only if your build network needs one to reach GitHub.

## What was verified, and how

Against a real `axor-lab serve` with `deploy/nginx.conf` in front of it, not
inside Docker (no daemon was available where this was written — the image build
itself is unverified):

- the SPA, `/home` 401 without a token and 200 with one, `/suites` returning the
  built-in catalog;
- `POST /guest-session` returning 404 while guest sessions are off;
- the rate limit: 4 requests through (1 + burst 3) then `429`, while 20
  consecutive API reads all returned 200;
- the security headers nginx adds, and `server_tokens off`;
- durability: a suite created through the proxy landed at
  `/data/suite/<id>.json` and was still listed after the process was stopped and
  started again;
- `nginx -t` on the shipped configuration, and `docker compose config`,
  including that it refuses to render without `AXOR_LAB_CONTROL_TOKEN`.
