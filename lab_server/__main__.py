"""`python -m lab_server` — run the Axor Lab catalog/publish server locally."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .app import make_server


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
    args = parser.parse_args(argv)
    server = make_server(
        Path(args.root), host=args.host, port=args.port,
        write_token=args.write_token, admin_token=args.admin_token,
    )
    auth = "token-gated" if args.write_token else "OPEN (local dev only — do not expose)"
    print(f"axor-lab server on http://{args.host}:{args.port} (store: {args.root}) — writes: {auth}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
