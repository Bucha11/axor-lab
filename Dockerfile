# syntax=docker/dockerfile:1
# The Axor Lab server (catalog/publish + runtime-jobs + the paid Security
# features). stdlib at the core; the crypto + kernel extras are needed only to
# VERIFY a workspace license (axor-packaging.md §4) — without them the server
# still runs, as the community tier.
FROM python:3.12-slim AS base

WORKDIR /app

# Copy the source (the .dockerignore keeps node_modules / dist / .git out).
COPY . .

# crypto = pynacl (Ed25519); axor-core supplies the SAME JCS canonicalizer the
# Control Plane signs licenses with, so verification stays byte-identical. axor-core
# may be a private package — point pip at your index/token with a BuildKit secret
# (`--secret id=pip_conf,src=pip.conf`). If it cannot be installed the image still
# runs (community tier only); a hosted paid deployment needs it, so the runbook
# flags that.
RUN --mount=type=secret,id=pip_conf,required=false,target=/etc/pip.conf \
    pip install --no-cache-dir ".[crypto]" \
    && ( pip install --no-cache-dir "axor-core>=0.9,<0.10" \
         || echo "WARN: axor-core not installed — license verification unavailable" )

# Drop privileges; the store lives on a mounted volume owned by this user.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/lab-store && chown -R app /data
USER app

# Hosted posture by default in the image (entitlements enforced); a self-hosted
# operator overrides with AXOR_LAB_HOSTED=0.
ENV AXOR_LAB_HOSTED=1 \
    AXOR_LAB_LICENSE_FILE=/run/secrets/license.json

# 8000 = catalog/publish + paid API; 8010 = runtime-jobs.
EXPOSE 8000 8010

# All secrets/tokens come from the environment (see .env.example); the command
# only fixes the bind + ports + store path.
ENTRYPOINT ["python", "-m", "lab_server", \
            "--host", "0.0.0.0", "--port", "8000", \
            "--runtime-port", "8010", "--root", "/data/lab-store"]
