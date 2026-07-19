"""The HTTP surface — a stdlib `http.server` app over the PublicationStore.

Routes:
  GET  /                                  catalog (HTML)
  GET  /e/{id}                            publication page (HTML)
  GET  /e/{id}/evidence/{trace_id}        EvidenceCase (HTML)
  GET  /api/publications/{id}             publication (JSON) + provenance axes
  POST /api/publications                  publish handshake
  POST /api/publications/{id}/reproductions   append an attestation

Deliberately dependency-free (http.server). Size limits and untrusted-string
escaping (in html.py) implement threat-model §4; the publish handshake and
`origin=local` honesty live in store.py.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .errors import NotFound, PublishRejected, ServerError
from .html import render_catalog, render_evidence, render_publication
from .store import PublicationStore

MAX_BODY_BYTES = 32 * 1024 * 1024  # uploaded bundles are bounded (threat-model §4)
_EVIDENCE_RE = re.compile(r"^/e/([A-Za-z0-9_]+)/evidence/([A-Za-z0-9_]+)$")
_PUB_RE = re.compile(r"^/e/([A-Za-z0-9_]+)$")
_API_PUB_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)$")
_API_REPRO_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/reproductions$")


def make_server(store_root: Path, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """Build (do not start) an HTTP server bound to host:port."""
    store = PublicationStore(root=store_root)

    class Handler(BaseHTTPRequestHandler):
        server_version = "axor-lab-server/0.1"

        def log_message(self, *args: object) -> None:  # quiet by default
            pass

        # -- GET ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            try:
                if self.path == "/" or self.path == "":
                    self._html(render_catalog(store.catalog()))
                    return
                evidence = _EVIDENCE_RE.match(self.path)
                if evidence:
                    stored = store.get(evidence.group(1))
                    if evidence.group(2) not in stored.traces:
                        raise NotFound("trace not found")
                    self._html(render_evidence(stored, evidence.group(2)))
                    return
                api_pub = _API_PUB_RE.match(self.path)
                if api_pub:
                    stored = store.get(api_pub.group(1))
                    self._json(200, {**stored.publication, "provenance": stored.axes()})
                    return
                page = _PUB_RE.match(self.path)
                if page:
                    self._html(render_publication(store.get(page.group(1))))
                    return
                raise NotFound("no such route")
            except ServerError as exc:
                self._error(exc)

        # -- POST ---------------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = self._read_json()
                if self.path == "/api/publications":
                    stored = store.publish(
                        bundle=payload["bundle"],
                        traces=payload["traces"],
                        question=str(payload.get("question", "")),
                        license_id=str(payload.get("license", "CC-BY-4.0")),
                        visibility=str(payload.get("visibility", "public")),
                    )
                    pid = stored.publication["publication_id"]
                    self._json(201, {"publication_id": pid, "url": f"/e/{pid}"})
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
            status = getattr(exc, "status", 404 if isinstance(exc, NotFound) else 500)
            self._json(status, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)
