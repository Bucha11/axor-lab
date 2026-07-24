# Axor Lab — hosted pilot runbook (Hetzner + Cloudflare)

A single-tenant hosted Private Lab for an Incident-to-Regression pilot
(axor-packaging.md §6). One Hetzner box, Cloudflare in front for DNS + TLS +
DDoS. The Lab runs in **hosted mode** — the paid Security-Workspace features
(history, approvals, compliance export) are entitlement-gated by the workspace
license; everything safety-related stays free.

```
Cloudflare (edge TLS, proxy)  ──►  Hetzner VPS
  lab.<your-domain>                 nginx (443, Origin Cert)  ──►  lab-server:8000  (catalog/publish + paid API)
                                                              └─►  lab-server:8010  (runtime-jobs)
```

## 0. Prerequisites
- A Hetzner Cloud VPS (CX22 or larger), Ubuntu 24.04.
- A domain on Cloudflare (e.g. `lab.useaxor.net`).
- The vendor Ed25519 keypair (from `axor-license keygen`) — keep the **private**
  key offline; you only put the **public** key on the server.

## 1. Provision the box
```bash
ssh root@<hetzner-ip>
apt-get update && apt-get install -y docker.io docker-compose-plugin git ufw
```

Firewall — SSH plus HTTP/HTTPS, and lock the origin so only Cloudflare can
reach it (the box is never hit directly):
```bash
ufw allow OpenSSH
for cidr in $(curl -s https://www.cloudflare.com/ips-v4); do ufw allow from $cidr to any port 443 proto tcp; done
ufw --force enable
```

## 2. Cloudflare DNS + TLS
1. **DNS**: add an `A` record `lab` → `<hetzner-ip>`, **proxied** (orange cloud).
2. **SSL/TLS mode**: set to **Full (strict)**.
3. **Origin Certificate**: SSL/TLS → Origin Server → *Create Certificate*.
   Save the cert and key on the box:
   ```bash
   mkdir -p /opt/axor-lab/certs
   # paste the two blocks:
   nano /opt/axor-lab/certs/origin.pem   # Origin Certificate
   nano /opt/axor-lab/certs/origin.key   # Private Key
   chmod 600 /opt/axor-lab/certs/origin.key
   ```

## 3. Get the code + configure
```bash
git clone https://github.com/Bucha11/axor-lab /opt/axor-lab-src
cd /opt/axor-lab-src
cp .env.example .env
```
Fill in `.env`:
- `AXOR_LAB_WRITE_TOKEN`, `AXOR_LAB_ADMIN_TOKEN`, `AXOR_LAB_CONTROL_TOKEN` — each
  `openssl rand -hex 32`.
- `AXOR_VENDOR_PUBKEY` — the vendor **public** key (hex).
- keep `AXOR_LAB_HOSTED=1`.

Point compose at the certs you saved:
```bash
ln -s /opt/axor-lab/certs ./certs
```

## 4. Issue and install the pilot license
On your **offline** vendor machine (where the private key lives), issue a
Security-tier license for the pilot org:
```bash
axor-license issue \
  --key-file vendor.key \
  --org "PilotCo" \
  --workspace-tier security \
  --private-lab \
  --expires-at 2026-12-31 \
  > license.json
```
Copy it onto the box where compose mounts it:
```bash
mkdir -p /opt/axor-lab-src/secrets
scp license.json root@<hetzner-ip>:/opt/axor-lab-src/secrets/license.json
```
(No license? The server still boots — as the community tier, with the paid
endpoints returning 402. Safety features never require one.)

## 5. Build + run
`axor-core` is needed for license verification. If it is a private package, put
your pip index/token in a `pip.conf` and pass it as a BuildKit secret; otherwise
plain `docker compose` works:
```bash
DOCKER_BUILDKIT=1 docker compose up -d --build
docker compose logs -f lab-server   # look for "… — security workspace · hosted (entitlements enforced)"
```

## 6. Verify
```bash
curl -s https://lab.<your-domain>/api/license/status | jq
#   { "active": true, "workspace_tier": "security", "modules": { … } }

# a write needs the token; a paid read is gated:
curl -s -o /dev/null -w '%{http_code}\n' https://lab.<your-domain>/api/audit
#   200 with the security license — 402 without one
```
Open `https://lab.<your-domain>/` — the SPA loads; **bring an agent → import an
incident → approve → compliance report** is the pilot loop.

## 7. Operate
- **Logs / status**: `docker compose logs -f`; the audit log persists in the
  `labdata` volume under `audit/log.jsonl`.
- **Backups**: snapshot the `labdata` volume (incidents, publications, audit).
- **Update**: `git pull && docker compose up -d --build`.
- **Renew the license**: re-issue with a later `--expires-at`, replace
  `secrets/license.json`, `docker compose restart lab-server`. Expiry degrades
  paid features to read-only; it never touches safety.
- **Refresh Cloudflare IPs** in `frontend/nginx.conf` and the ufw rules if the
  published ranges change.

## Notes
- This is the **single-tenant pilot** shape. Multi-tenant SaaS (accounts,
  workspaces, per-tenant isolation, hosted-trial billing) is a separate step.
- The Control Plane (production governance) is a separate deployment with its own
  compose (`axor-control-plane/docker-compose.yml`) and domain — added when the
  pilot converts to production enforcement.
