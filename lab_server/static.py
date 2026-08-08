"""Serving the built web app.

The screen API and the app it serves come from one process, so `axor-lab serve`
gives a working product rather than an API plus instructions for building a
frontend separately.

Two things this is careful about:

  - **Path traversal.** A request path is resolved against the root and the
    result is checked to still be INSIDE it. Rejecting `..` textually is not
    enough — a symlink, a URL-encoded separator or a Windows-style path all get
    past a string check and none get past a resolved-prefix check.
  - **There is no catch-all fallback.** The app routes on the HASH, so the only
    document path a browser ever requests is `/`; every other path is an asset
    or an API route. An earlier version of this listed `/runs`, `/suites`,
    `/home` and the rest as "app routes" to fall back on, which was fiction
    twice over: the browser never requests them, and each one is already an API
    route that answers first. What the missing fallback buys is that a build
    which lost a chunk 404s instead of serving `index.html` in its place — a
    browser handed HTML for a `.js` request reports a syntax error in a file
    that was never JavaScript, and the real failure stays hidden.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

# The only document paths the app is served at. Everything else it navigates to
# lives after the `#`, which never reaches the server.
APP_ROUTES = ("/", "/index.html")


def default_root() -> Path | None:
    """`web/dist` beside the repo, when it has been built."""
    candidate = Path(__file__).resolve().parent.parent / "web" / "dist"
    return candidate if (candidate / "index.html").is_file() else None


def resolve(root: Path, path: str) -> Path | None:
    """The file this request path names, or None.

    None means "not a file here" — the caller decides between the SPA fallback
    and a 404, because only the caller knows whether the path is one the app
    routes.
    """
    relative = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if not relative:
        relative = "index.html"
    try:
        candidate = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    root_resolved = root.resolve()
    # containment check on the RESOLVED paths: a symlink pointing out of the
    # build directory is exactly what a textual `..` filter misses
    if root_resolved not in candidate.parents and candidate != root_resolved:
        return None
    return candidate if candidate.is_file() else None


def content_type(file: Path) -> str:
    guessed, _ = mimetypes.guess_type(file.name)
    if guessed:
        # a bare text/* served without a charset renders mojibake for any
        # non-ASCII byte the app legitimately contains
        return f"{guessed}; charset=utf-8" if guessed.startswith("text/") else guessed
    return "application/octet-stream"


def is_app_route(path: str) -> bool:
    """True for the document paths the app is served at."""
    clean = path.split("?", 1)[0]
    return (clean.rstrip("/") or "/") in APP_ROUTES
