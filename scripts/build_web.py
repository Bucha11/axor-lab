#!/usr/bin/env python3
"""Build the web UI and stage it as package data, so the wheel ships it.

    python scripts/build_web.py          # npm ci && npm run build, then sync
    python scripts/build_web.py --sync   # sync an existing frontend/dist only

Why this exists: an installed user has no `frontend/` directory, so without a
copy inside the package `pip install axor-lab` could never serve the UI and node
would be a hard prerequisite for seeing the product at all. This is the same
pattern `lab_contracts/schemas/` already uses — a build-time copy of a
source-of-truth directory, kept honest by tests/test_packaging.py.

Run this before `python -m build`. It is NOT part of the test suite or the
runtime: a checkout serves `frontend/dist` directly.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"
DIST = FRONTEND / "dist"
PACKAGED = REPO_ROOT / "lab_server" / "web"


def build() -> None:
    # follow the lockfile that is actually committed — installing with the other
    # package manager resolves a different tree than the one CI and the image
    # build from, which is how "works on my machine" starts
    if (FRONTEND / "pnpm-lock.yaml").is_file():
        manager, install = "pnpm", ["install", "--frozen-lockfile"]
    elif (FRONTEND / "package-lock.json").is_file():
        manager, install = "npm", ["ci"]
    else:
        manager, install = "npm", ["install"]
    exe = shutil.which(manager)
    if exe is None:
        raise SystemExit(
            f"{manager} not found — install node/{manager}, or use --sync with an existing build"
        )
    subprocess.run([exe, *install], cwd=FRONTEND, check=True)
    subprocess.run([exe, "run", "build"], cwd=FRONTEND, check=True)


def sync() -> None:
    if not (DIST / "index.html").is_file():
        raise SystemExit(f"no build at {DIST} — run without --sync, or build the frontend first")
    # replace wholesale: a stale asset left behind from a previous build is
    # served forever by content-hashed filenames that nothing references
    if PACKAGED.exists():
        shutil.rmtree(PACKAGED)
    shutil.copytree(DIST, PACKAGED)
    files = sum(1 for p in PACKAGED.rglob("*") if p.is_file())
    try:
        where = PACKAGED.relative_to(REPO_ROOT)
    except ValueError:  # staged somewhere else entirely — say where, don't crash
        where = PACKAGED
    print(f"staged {files} file(s) into {where} — the wheel will ship the UI")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sync", action="store_true",
        help="skip npm; copy an already-built frontend/dist into the package",
    )
    args = parser.parse_args(argv)
    if not args.sync:
        build()
    sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
