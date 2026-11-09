#!/bin/sh
# Build the web app and copy it into the package, so the wheel carries a UI.
#
#   tools/bundle_web.sh && python -m build
#
# `pip install axor-lab && axor-lab serve` then opens the app at
# http://127.0.0.1:8871 with nothing else installed. Run from the repo root.
set -eu
cd "$(dirname "$0")/.."
npm --prefix web ci --no-audit --no-fund
npm --prefix web run build
rm -rf lab_server/_web
cp -R web/dist lab_server/_web
echo "bundled web app -> lab_server/_web ($(find lab_server/_web -type f | wc -l | tr -d ' ') files)"
