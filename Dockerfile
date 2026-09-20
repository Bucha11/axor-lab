# Axor Lab — one image, one process: the screen API plus the built web app.
#
#   docker compose up --build        # then http://localhost:8871
#
# Two stages. The first builds the React app with node; the second carries only
# its `dist/` into the Python image, so node never ships to production.
#
# The Python install is DELIBERATELY editable (`pip install -e .`). `axor-lab
# serve` finds the web app at `lab_server/../../web/dist`, which resolves only
# while the package sits beside `web/` in a source tree; a normal install puts
# `lab_server` in site-packages, where that path is a directory that does not
# exist and the server starts with no UI and says so. Editable keeps the layout
# the code expects instead of teaching the image to work around it.

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

# git: axor-wrap and axor-eval are pinned to git refs in pyproject (they are not
# on PyPI with the API Lab needs). ca-certificates: to reach them over HTTPS.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Dependency metadata first: this layer is the slow one (a git clone per
# ecosystem dep) and it must not be invalidated by an ordinary source edit.
COPY pyproject.toml README.md ./
COPY lab_contracts/__init__.py ./lab_contracts/
RUN --mount=type=secret,id=github_token sh -eu -c '\
    if [ -s /run/secrets/github_token ]; then \
      git config --global url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf "https://github.com/"; \
    fi; \
    pip install --no-cache-dir -e . ; \
    rm -f /root/.gitconfig'

# Now the source, and the web bundle built in stage 1.
COPY lab_contracts/ ./lab_contracts/
COPY lab_runner/ ./lab_runner/
COPY lab_analysis/ ./lab_analysis/
COPY lab_adapters/ ./lab_adapters/
COPY lab_server/ ./lab_server/
COPY lab_suite/ ./lab_suite/
COPY lab_capabilities/ ./lab_capabilities/
COPY contracts/ ./contracts/
COPY examples/ ./examples/
COPY docs/pricing/ ./docs/pricing/
COPY --from=web /web/dist ./web/dist

# The data directory is what makes a deployment durable; compose mounts a volume
# here. Without it the server keeps everything in memory and says so at startup.
ENV AXOR_LAB_DATA_DIR=/data
RUN mkdir -p /data && useradd -r -u 10001 axor && chown -R axor:axor /app /data
USER axor

EXPOSE 8871
CMD ["axor-lab", "serve", "--host", "0.0.0.0", "--port", "8871"]
