# Axor Lab — one image, one process: the screen API plus the built web app.
#
#   docker compose up --build        # then http://localhost:8871
#
# Two stages. The first builds the React app with node; the second carries only
# its `dist/` into the Python image, so node never ships to production.
#
# The app is installed normally — `pip install .`, no source tree left behind —
# and told where its UI lives with AXOR_LAB_WEB_ROOT. The implicit default
# (`lab_server/../../web/dist`) only resolves for a source checkout, which is
# why this used to need an editable install to work at all.

# ── stage 1: the web app ─────────────────────────────────────────────────────
FROM node:22-slim AS web
WORKDIR /web
# package files first, so a source-only change does not re-resolve npm
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ── stage 2: the platform ────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# The extras are the hosted server's: postgres (AXOR_LAB_DATABASE_URL — without
# it the server refuses to start when compose sets a DSN), identity (login via
# axor-identity), yaml (the Builder's YAML mode), crypto (signed bundles).
#
# One install, reading pyproject — deliberately not split into a cached
# dependency layer. Splitting means naming the axor-core/-wrap/-eval ranges a
# second time here, and a Dockerfile pin that drifts from pyproject is
# an image built against a version nothing tested. A slower rebuild is the
# cheaper mistake.
COPY pyproject.toml README.md ./
COPY lab_contracts/ ./lab_contracts/
COPY lab_runner/ ./lab_runner/
COPY lab_analysis/ ./lab_analysis/
COPY lab_adapters/ ./lab_adapters/
COPY lab_server/ ./lab_server/
COPY lab_service/ ./lab_service/
COPY lab_suite/ ./lab_suite/
COPY lab_capabilities/ ./lab_capabilities/
COPY contracts/ ./contracts/
COPY examples/ ./examples/
COPY docs/pricing/ ./docs/pricing/
# Every dependency is a PyPI release, so no git, no token, no apt layer.
RUN pip install --no-cache-dir ".[postgres,identity,yaml,crypto]"

# The UI, built in stage 1, at a path the server is told about explicitly.
COPY --from=web /web/dist /srv/axor-lab/web

# The data directory is what makes a deployment durable; compose mounts a volume
# here. Without it the server keeps everything in memory and says so at startup.
ENV AXOR_LAB_DATA_DIR=/data \
    AXOR_LAB_WEB_ROOT=/srv/axor-lab/web
RUN mkdir -p /data && useradd -r -u 10001 axor \
    && chown -R axor:axor /app /data /srv/axor-lab
USER axor

EXPOSE 8871
CMD ["axor-lab", "serve", "--host", "0.0.0.0", "--port", "8871"]
