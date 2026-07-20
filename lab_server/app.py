"""The HTTP surface — a stdlib `http.server` app over the PublicationStore.

Routes:
  GET  /                                  catalog (HTML, public only)
  GET  /e/{id}                            publication page (HTML; private → 404)
  GET  /e/{id}/evidence/{trace_id}        EvidenceCase (HTML)
  GET  /api/publications/{id}             publication (JSON) + provenance axes
  POST /api/publications                  publish handshake        [write token]
  POST /api/publications/{id}/reproductions   append an attestation [write token]
  POST /api/publications/{id}/takedown    remove from catalog       [admin token]

Auth: pass `write_token` / `admin_token` to gate mutations with a bearer token.
When a token is set, a mutation without a matching `Authorization: Bearer …`
header is rejected 401. Left unset (the local-dev default) the endpoint is
open — do NOT expose an unauthenticated server publicly (review P0.5).

Deliberately dependency-free (http.server). Size limits and untrusted-string
escaping (in html.py) implement threat-model §4; the publish handshake and
`origin=local` honesty live in store.py.
"""

from __future__ import annotations

import hmac
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .errors import NotFound, PublishRejected, ServerError
from .html import render_catalog, render_evidence, render_publication
from .store import PublicationStore

MAX_BODY_BYTES = 32 * 1024 * 1024  # uploaded bundles are bounded (threat-model §4)
# trace_ids carry the scenario slug (e.g. banking-exfil-01), so the evidence
# route must accept hyphens in the trace_id segment; both segments stay to a
# safe character class (no '/', '.', or path-traversal metacharacters)
_EVIDENCE_RE = re.compile(r"^/e/([A-Za-z0-9_-]+)/evidence/([A-Za-z0-9_-]+)$")
_PUB_RE = re.compile(r"^/e/([A-Za-z0-9_-]+)$")
_API_PUB_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)$")
_API_REPRO_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/reproductions$")
_API_TAKEDOWN_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/takedown$")


class Unauthorized(ServerError):
    """A mutation was attempted without the required bearer token."""


def make_server(
    store_root: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    write_token: str | None = None,
    admin_token: str | None = None,
    known_keys: dict[str, str] | None = None,
) -> ThreadingHTTPServer:
    """Build (do not start) an HTTP server bound to host:port."""
    store = PublicationStore(root=store_root, known_keys=known_keys or {})

    class Handler(BaseHTTPRequestHandler):
        server_version = "axor-lab-server/0.1"

        def log_message(self, *args: object) -> None:  # quiet by default
            pass

        # -- GET ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            try:
                from urllib.parse import parse_qs, urlsplit

                split = urlsplit(self.path)
                path = split.path
                if path == "/" or path == "":
                    self._html(render_catalog(store.catalog()))
                    return
                evidence = _EVIDENCE_RE.match(path)
                if evidence:
                    stored = store.get(evidence.group(1))
                    if evidence.group(2) not in stored.traces:
                        raise NotFound("trace not found")
                    policy = parse_qs(split.query).get("policy", [None])[0]
                    self._html(render_evidence(stored, evidence.group(2), policy))
                    return
                api_pub = _API_PUB_RE.match(path)
                if api_pub:
                    stored = store.get(api_pub.group(1))
                    self._json(200, {**stored.publication, "provenance": stored.axes()})
                    return
                page = _PUB_RE.match(path)
                if page:
                    stored = store.get(page.group(1))
                    if stored.publication.get("visibility") == "private":
                        raise NotFound("no such publication")  # never serve private
                    self._html(render_publication(stored))
                    return
                raise NotFound("no such route")
            except ServerError as exc:
                self._error(exc)

        # -- POST ---------------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = self._read_json()
                if self.path == "/api/publications":
                    self._require(write_token)
                    stored = store.publish(
                        bundle=payload["bundle"],
                        traces=payload["traces"],
                        question=str(payload.get("question", "")),
                        license_id=str(payload.get("license", "CC-BY-4.0")),
                        visibility=str(payload.get("visibility", "public")),
                        signature=payload.get("signature"),  # type: ignore[arg-type]
                        author=payload.get("author"),  # type: ignore[arg-type]
                    )
                    pid = stored.publication["publication_id"]
                    self._json(201, {
                        "publication_id": pid, "url": f"/e/{pid}",
                        "integrity": stored.publication["integrity"],
                    })
                    return
                takedown = _API_TAKEDOWN_RE.match(self.path)
                if takedown:
                    self._require(admin_token)  # takedown is admin-only
                    store.takedown(takedown.group(1))
                    self._json(200, {
                        "publication_id": takedown.group(1), "status": "taken_down",
                        "reproductions_preserved": len(store.reproductions_of(takedown.group(1))),
                    })
                    return
                repro = _API_REPRO_RE.match(self.path)
                if repro:
                    stored = store.add_attestation(repro.group(1), payload["attestation"])
                    self._json(201, {"reproductions": stored.axes()["reproductions"]})
                    return
                raise NotFound("no such route")
            except (KeyError, json.JSONDecodeError) as exc:
                self._error(PublishRejected(f"malformed request: {exc}", status=400))
            except ServerError as exc:
                self._error(exc)

        # -- helpers ------------------------------------------------------

        def _require(self, expected: str | None) -> None:
            """Enforce a bearer token when one is configured (constant-time)."""
            if expected is None:
                return  # local-dev open mode
            header = self.headers.get("Authorization", "")
            presented = header[7:] if header.startswith("Bearer ") else ""
            if not hmac.compare_digest(presented, expected):
                raise Unauthorized("missing or invalid bearer token")

        def _read_json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                raise PublishRejected("request body too large", status=413)
            return json.loads(self.rfile.read(length) or b"{}")

        def _html(self, markup: str) -> None:
            body = markup.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: object) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, exc: ServerError) -> None:
            if isinstance(exc, Unauthorized):
                status = 401
            elif isinstance(exc, NotFound):
                status = 404
            else:
                status = getattr(exc, "status", 500)
            self._json(status, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)
