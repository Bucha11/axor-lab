"""`python -m lab_server` — run the Axor Lab catalog/publish server locally.

With `--runtime-port` the runtime-jobs API (runtime registration, experiment
planning and run assignment — see runtime_jobs.py) is served alongside the
catalog on a second port, from a daemon thread in the same process.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from .app import make_server
from .runtime_jobs import make_runtime_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab-server", description="Axor Lab catalog server")
    parser.add_argument("--root", default="./lab-store", help="publication store directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--write-token", default=os.environ.get("AXOR_LAB_WRITE_TOKEN"),
        help="require this bearer token for publish/attest (or AXOR_LAB_WRITE_TOKEN)",
    )
    parser.add_argument(
        "--admin-token", default=os.environ.get("AXOR_LAB_ADMIN_TOKEN"),
        help="require this bearer token for takedown (or AXOR_LAB_ADMIN_TOKEN)",
    )
    # ON by default. It used to default to 0 (off), which meant the documented
    # start command brought up the catalog and left the whole run surface dark —
    # the builder, runs, results and "bring an agent" all failed against a server
    # that looked healthy. Half a product behind an undocumented flag is worse
    # than a missing flag, so the default is now the whole product.
    parser.add_argument(
        "--runtime-port", type=int, default=8010,
        help="serve the runtime-jobs API (connect runtimes, plan/assign runs, "
             "local runs) on this port [default: 8010]",
    )
    # Signing a CP export is an operator action vouching for a production config,
    # so the key belongs in the Control Plane's signing vault — which signs and
    # never surrenders. Lab hands over the manifest bytes and never holds a key.
    # Unset → exports come back honestly UNSIGNED.
    parser.add_argument(
        "--cp-url", default=os.environ.get("AXOR_LAB_CP_URL"),
        help="Control Plane base URL; its signing vault signs CP export manifests "
             "(or AXOR_LAB_CP_URL). Unset = exports are unsigned",
    )
    parser.add_argument(
        "--cp-signing-token", default=os.environ.get("AXOR_LAB_CP_SIGNING_TOKEN"),
        help="bearer for the Control Plane vault's signing subsystem "
             "(or AXOR_LAB_CP_SIGNING_TOKEN)",
    )
    parser.add_argument(
        "--no-runtime-api", action="store_true",
        help="catalog only — do not serve the runtime-jobs API. The builder, runs, "
             "results and agent ingest will be unavailable",
    )
    parser.add_argument(
        "--control-token", default=os.environ.get("AXOR_LAB_CONTROL_TOKEN"),
        help="require this bearer token on the runtime-jobs control surface "
             "(or AXOR_LAB_CONTROL_TOKEN); runtime-facing endpoints stay gated "
             "by their per-runtime ingest_key",
    )
    parser.add_argument(
        "--license-file", default=os.environ.get("AXOR_LAB_LICENSE_FILE"),
        help="workspace license file (axor-packaging.md §4); none = community tier",
    )
    parser.add_argument(
        "--vendor-pubkey", default=os.environ.get("AXOR_VENDOR_PUBKEY"),
        help="vendor Ed25519 public key (hex) to verify the license against "
             "(or AXOR_VENDOR_PUBKEY)",
    )
    parser.add_argument(
        "--hosted", action="store_true",
        default=os.environ.get("AXOR_LAB_HOSTED", "").lower() in ("1", "true", "yes"),
        help="hosted posture: ENFORCE workspace entitlements (paid Security "
             "features answer 402 below tier). Off = self-hosted, unlimited local "
             "use, nothing gated (or AXOR_LAB_HOSTED=1)",
    )
    args = parser.parse_args(argv)

    # the workspace entitlement is OPTIONAL: without a valid license the server
    # runs as the community tier (free, local/public). An invalid or unverifiable
    # license is a loud warning, never a hard stop — safety never checks it.
    license_obj = None
    if args.license_file:
        from .license import LicenseError, verify_license
        if not args.vendor_pubkey:
            print("license file given but no --vendor-pubkey / AXOR_VENDOR_PUBKEY — "
                  "running as community tier", file=sys.stderr)
        else:
            try:
                license_obj = verify_license(
                    Path(args.license_file).read_text(), args.vendor_pubkey,
                )
            except (LicenseError, OSError) as exc:
                print(f"license not activated ({exc}) — running as community tier",
                      file=sys.stderr)

    server = make_server(
        Path(args.root), host=args.host, port=args.port,
        write_token=args.write_token, admin_token=args.admin_token,
        license_obj=license_obj, hosted_mode=args.hosted,
    )
    auth = "token-gated" if args.write_token else "OPEN (local dev only — do not expose)"
    tier = f"{license_obj.workspace_tier} workspace" if license_obj else "community tier"
    if args.hosted:
        tier += " · hosted (entitlements enforced)"
    # report the BOUND port (server_address), not the requested one — with
    # --port 0 the OS picks an ephemeral port and the printed URL must work
    bound_port = server.server_address[1]
    print(f"axor-lab server on http://{args.host}:{bound_port} (store: {args.root}) — "
          f"writes: {auth} — {tier}")
    if args.no_runtime_api:
        print("runtime-jobs API disabled (--no-runtime-api) — the builder, runs, "
              "results and agent ingest will not work", file=sys.stderr)
    elif args.runtime_port:
        runtime_server = make_runtime_server(
            host=args.host, port=args.runtime_port, control_token=args.control_token,
            store_root=Path(args.root) / "runtime-jobs",
            cp_url=args.cp_url, cp_signing_token=args.cp_signing_token,
        )
        threading.Thread(
            target=runtime_server.serve_forever, daemon=True, name="runtime-jobs",
        ).start()
        control = "token-gated" if args.control_token else "OPEN (local dev only — do not expose)"
        runtime_port = runtime_server.server_address[1]
        print(f"axor-lab runtime-jobs on http://{args.host}:{runtime_port} — control: {control}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
