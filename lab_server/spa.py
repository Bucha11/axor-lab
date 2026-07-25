"""Serve the built web UI, and bridge it to the runtime API.

The cheapest way to try Lab showed the least of it. `lab_server` served its own
server-rendered catalog at `/` and nothing else, while the React UI was raised by
a separate nginx container in the compose deployment. So `pip install axor-lab`
followed by the documented start command gave you a sparse HTML index — no run
button, no builder, no verifier — and the reasonable conclusion was that the
product was empty.

Two pieces make one command enough:

* the built SPA is served from `frontend/dist` when it exists;
* `/jobs-api/*` is proxied to the runtime API, because the SPA talks to two
  backends and only one of them is this port. In development vite proxies this;
  in the compose deployment nginx does. With neither, everything the runtime API
  serves — running, composing, replaying, evidence, pins, the CP handoff — would
  fail from a UI that looked fine.

The server-rendered pages are untouched. `/e/{id}` and its EvidenceCase stay
server-rendered because they are the citable, no-JS, crawlable artifacts; the
plain catalog moves to `/catalog` rather than disappearing. With no build present
`/` still answers with it, and says where the UI went.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

# Only these extensions are served, and only from inside the dist directory —
# the UI build is a fixed set of assets, not a file server.
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json",
}


#: the build shipped INSIDE the wheel, and the checkout's build — searched in
#: that order. An installed user has no `frontend/` at all, so without the
#: package-data copy `pip install axor-lab` could never serve the UI and node
#: would be a hard prerequisite for seeing the product at all.
PACKAGED_WEB = Path(__file__).resolve().parent / "web"
CHECKOUT_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def find_dist(explicit: str | None = None) -> Path | None:
    """The built UI, if there is one.

    Auto-detection lives here and is called from the ENTRYPOINT, never from
    `make_server`: a library factory whose behaviour depends on whether someone
    happened to run `npm run build` is not a factory anyone can test. Callers
    that want the UI pass the directory explicitly.

    A checkout build WINS over the packaged one: someone who just ran
    `npm run build` means the thing they built, not the copy frozen at release.
    """
    if explicit:
        path = Path(explicit)
        return path if (path / "index.html").is_file() else None
    for candidate in (CHECKOUT_DIST, PACKAGED_WEB):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def resolve_asset(dist: Path, path: str) -> tuple[bytes, str] | None:
    """Read an asset for `path`, or None when it is not one.

    The path is confined to `dist`: a request is untrusted, so `..` and symlinks
    that leave the build directory resolve to nothing rather than reading the
    filesystem.
    """
    relative = path.lstrip("/")
    if not relative or relative.endswith("/"):
        return None
    candidate = dist / relative
    if candidate.suffix not in _TYPES:
        return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(dist.resolve())
    except (ValueError, OSError):
        return None
    if not resolved.is_file() or candidate.is_symlink():
        return None
    return resolved.read_bytes(), _TYPES[candidate.suffix]


def index_html(dist: Path) -> bytes:
    return (dist / "index.html").read_bytes()


class JobsProxy:
    """Forward `/jobs-api/*` to the runtime API on its own port.

    The SPA is built against two backends. Without this bridge a one-command
    install would raise a UI whose every run, compose, replay and evidence call
    fails — worse than not serving it, because it looks like the product is
    broken rather than absent.
    """

    def __init__(self, runtime_base: str) -> None:
        self.base = runtime_base.rstrip("/")

    def forward(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str],
    ) -> tuple[int, bytes, str]:
        target = self.base + path[len("/jobs-api"):]
        request = urllib.request.Request(
            target, data=body, method=method,
            headers={k: v for k, v in headers.items()
                     if k.lower() in ("content-type", "authorization")},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return (
                    response.status,
                    response.read(),
                    response.headers.get("Content-Type", "application/json"),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("Content-Type", "application/json")
        except OSError as exc:
            # the runtime API is not up: say which one, rather than a bare 502
            message = (
                b'{"error": "the runtime API is not reachable from this server '
                b'(started with --no-runtime-api?): ' + str(exc).encode() + b'"}'
            )
            return 502, message, "application/json"
