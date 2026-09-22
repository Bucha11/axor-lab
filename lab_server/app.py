"""The HTTP surface — a stdlib `http.server` app over the PublicationStore.

Routes:
  GET  /                                  catalog (HTML, public only)
  GET  /e/{id}                            publication page (HTML; private → 404)
  GET  /e/{id}/verify                     verify this publication (HTML)
  GET  /e/{id}/evidence/{trace_id}        EvidenceCase (HTML)
  GET  /api/publications/{id}             publication (JSON) + provenance axes
  POST /api/publications                  publish handshake        [write token]
  POST /api/publications/{id}/reproductions   append an attestation [write token]
  POST /api/publications/{id}/takedown    remove from catalog       [admin token]
  POST /api/publications/{id}/verify      verify this publication         [open]
  POST /api/verify                        verify an uploaded package      [open]

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
from .html import render_catalog, render_evidence, render_publication, render_verification
from .store import PublicationStore

MAX_BODY_BYTES = 32 * 1024 * 1024  # uploaded bundles are bounded (threat-model §4)
# trace_ids carry the scenario slug (e.g. banking-exfil-01), so the evidence
# route must accept hyphens in the trace_id segment; both segments stay to a
# safe character class (no '/', '.', or path-traversal metacharacters)
_EVIDENCE_RE = re.compile(r"^/e/([A-Za-z0-9_-]+)/evidence/([A-Za-z0-9_-]+)$")
_PUB_RE = re.compile(r"^/e/([A-Za-z0-9_-]+)$")
_PAGE_VERIFY_RE = re.compile(r"^/e/([A-Za-z0-9_-]+)/verify$")
_API_PUB_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)$")
_API_BUNDLE_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/bundle$")
_API_REPRO_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/reproductions$")
_API_TAKEDOWN_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/takedown$")
_API_PUB_VERIFY_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/verify$")


class Unauthorized(ServerError):
    """A mutation was attempted without the required bearer token."""


def _opaque_500() -> ServerError:
    """A generic 500 that leaks NO internal detail to the client (review r13).
    Unexpected exceptions map to this so a stack of internal state never reaches
    the socket; the real exception can still be logged server-side."""
    exc = ServerError("internal server error")
    exc.status = 500  # type: ignore[attr-defined]
    return exc


def _package_of(stored: object, store: PublicationStore) -> dict[str, object]:
    """The reproduction package for a stored publication.

    One shape, built once: the download route serves exactly what the verify
    route checks, so "verified on the page" and "verified from the file you
    downloaded" can never be answers about two different documents.
    """
    return {
        # a VERSIONED reproduction package — the verifier treats a package
        # bearing this schema as server-issued, so every proof object
        # (receipt/publication/acceptance) is MANDATORY and stripping one is a
        # verification failure (review r16)
        "schema_version": "axor-reproduction-package/v1",
        # the PUBLICATION body travels too, so an offline reader can verify the
        # author/server actually asserted THESE claims — not just that the
        # bundle bytes are intact (review r15)
        "publication": stored.publication,  # type: ignore[attr-defined]
        "bundle": stored.bundle,            # type: ignore[attr-defined]
        "traces": list(stored.traces.values()),  # type: ignore[attr-defined]
        # a PORTABLE verification receipt so a reader can verify the download
        # offline (author/key_id/signature/signed_ref) without trusting this
        # server (review r14)
        "receipt": stored.receipt(),        # type: ignore[attr-defined]
        # the server's signed ACCEPTANCE receipt (review r15). v0.3 keeps
        # publication + bundle hash + optional signature + reproduction records;
        # the reacceptance/history chains are deferred, so there is no
        # acceptance_history to carry.
        "acceptance": store.acceptance(stored),  # type: ignore[arg-type]
    }


def _traces_of(envelope: dict[str, object]) -> dict[str, dict[str, object]]:
    """A package's traces as a map, from either shape, as a 400 on junk."""
    from lab_service.packages import PackageMalformed, normalise_traces

    try:
        return normalise_traces(envelope.get("traces"))
    except PackageMalformed as exc:
        raise PublishRejected(str(exc), status=400) from exc


def _verified(
    envelope: dict[str, object],
    *,
    pubkey: object = None,
    author: object = None,
    verified_by: str,
) -> dict[str, object]:
    """Run the SAME verification the CLI runs, and report it check by check.

    `lab_service.verify_package_document` is the one implementation — the CLI,
    the authoring UI and this public route cannot drift into three opinions
    about whether a package holds up.
    """
    from lab_service import verify_package_document

    bundle = envelope.get("bundle")
    if not isinstance(bundle, dict):
        raise PublishRejected("package must carry a bundle", status=400)
    traces = _traces_of(envelope)
    result = verify_package_document(
        bundle, traces, envelope,
        pubkey=pubkey if isinstance(pubkey, str) else None,
        author=author if isinstance(author, str) else None,
        allow_bare=bool(envelope.get("allow_bare")),
    )
    return {
        "outcome": result.outcome.value,
        "bit_identical": result.bit_identical,
        "traces": result.trace_count,
        # a report claims only the checks that actually RAN
        "checks": [
            {"name": c.name, "status": c.status.value, "message": c.message}
            for c in result.checks
        ],
        # what this result is worth. A server verifying a package it served is
        # still the server talking: it catches corruption and a doctored file,
        # not a server that lies. The caveat travels with the answer.
        "verified_by": verified_by,
    }


def make_server(
    store_root: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    write_token: str | None = None,
    admin_token: str | None = None,
    known_keys: dict[str, str] | None = None,
    server_id: str = "lab.local",
    server_key_id: str | None = None,
    server_signing_key: str | None = None,
    dsn: str | None = None,
) -> ThreadingHTTPServer:
    """Build (do not start) an HTTP server bound to host:port."""
    store = PublicationStore(
        root=store_root, dsn=dsn, known_keys=known_keys or {},
        server_id=server_id, server_key_id=server_key_id,
        server_signing_key=server_signing_key,
    )

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
                    stored = self._readable(store.get(evidence.group(1)))
                    if evidence.group(2) not in stored.traces:
                        raise NotFound("trace not found")
                    policy = parse_qs(split.query).get("policy", [None])[0]
                    self._html(render_evidence(stored, evidence.group(2), policy))
                    return
                page_verify = _PAGE_VERIFY_RE.match(path)
                if page_verify:
                    # a verification is a READ — it changes nothing and retains
                    # nothing — so it is a GET a reader can click, bookmark and
                    # send to a colleague, with no JavaScript in the page (the
                    # CSP here forbids inline script, and rightly: every string
                    # rendered on these pages is untrusted upload).
                    stored = self._readable(store.get(page_verify.group(1)))
                    report = _verified(
                        _package_of(stored, store),
                        verified_by="this server, over its own stored copy",
                    )
                    self._html(render_verification(
                        report, back=f"/e/{page_verify.group(1)}"))
                    return
                api_bundle = _API_BUNDLE_RE.match(path)
                if api_bundle:
                    # the reproduction PACKAGE — the bundle + every trace body, so
                    # a reader can actually reconstruct the bundle dir and run
                    # `axor-lab replay`. Without this the page's reproduce command
                    # named a directory the reader never received (review r13).
                    stored = self._readable(store.get(api_bundle.group(1)))
                    self._json(200, _package_of(stored, store))
                    return
                api_pub = _API_PUB_RE.match(path)
                if api_pub:
                    stored = self._readable(store.get(api_pub.group(1)))
                    self._json(200, {**stored.publication, "provenance": stored.axes()})
                    return
                page = _PUB_RE.match(path)
                if page:
                    stored = self._readable(store.get(page.group(1)))
                    self._html(render_publication(stored))
                    return
                raise NotFound("no such route")
            except ServerError as exc:
                self._error(exc)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                # a malformed EvidenceCase / policy / kernel-resolution error is a
                # client-shaped 400, not a 500 traceback leaked to the socket
                self._error(PublishRejected(f"bad request: {exc}", status=400))
            except Exception:  # noqa: BLE001 — last-resort boundary
                self._error(_opaque_500())

        # -- POST ---------------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = self._read_json()
                if self.path == "/api/publications":
                    self._require(write_token)
                    stored = store.publish(
                        bundle=payload["bundle"],
                        # either shape: the download route emits a LIST, so a
                        # package taken off one server could not be handed to
                        # another one's publish — the two halves of "portable"
                        # disagreed about what a package looks like.
                        traces=_traces_of(payload),
                        question=str(payload.get("question", "")),
                        license_id=str(payload.get("license", "CC-BY-4.0")),
                        # safe default: an upload is NOT public unless it says so
                        visibility=str(payload.get("visibility", "unlisted")),
                        signature=payload.get("signature"),  # type: ignore[arg-type]
                        author=payload.get("author"),  # type: ignore[arg-type]
                    )
                    pid = stored.publication["publication_id"]
                    # ACCEPTANCE receipt: the server's SIGNED attestation of what it
                    # verified before minting (schema, content hashes, replay, stat
                    # recompute), content-addressing the semantic report. The
                    # publisher keeps it as portable, checkable proof the server
                    # accepted this bundle (review r14/r15).
                    self._json(201, {
                        "publication_id": pid, "url": f"/e/{pid}",
                        "integrity": stored.publication["integrity"],
                        "acceptance": store.acceptance(stored),
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
                    # appending a reproduction is a WRITE (it changes a published
                    # record's provenance axis) — it needs the write token, not
                    # an open endpoint anyone can inflate (review r7)
                    self._require(write_token)
                    stored = store.add_attestation(repro.group(1), payload["attestation"])
                    self._json(201, {"reproductions": stored.axes()["reproductions"]})
                    return

                # -- verification, OPEN by design ------------------------
                #
                # Every other POST here is a write and carries a token. These two
                # are reads that happen to compute: they change nothing, and the
                # ONE person who most needs them — a reviewer holding a link to a
                # publication — has no token and never will. Gating them left
                # `axor-lab verify` (install Python, install the package) as the
                # only way for an outsider to check a claim, which for almost
                # every reader means the claim simply goes unchecked.
                #
                # The cost is bounded: MAX_BODY_BYTES caps the input, the work is
                # linear in the package, and nothing is written or retained.
                pub_verify = _API_PUB_VERIFY_RE.match(self.path)
                if pub_verify:
                    stored = self._readable(store.get(pub_verify.group(1)))
                    self._json(200, _verified(
                        _package_of(stored, store),
                        # this server checking its own bytes cannot prove the
                        # server honest — say so in the payload, not only on the
                        # page, so an API caller gets the caveat too
                        verified_by="this server, over its own stored copy",
                    ))
                    return
                if self.path == "/api/verify":
                    # a reader drops in the file they downloaded — from here or
                    # from anywhere else. Accept the envelope bare (the download
                    # IS the envelope) or wrapped in {package: ...}.
                    envelope = payload.get("package") if "package" in payload else payload
                    if not isinstance(envelope, dict):
                        raise PublishRejected(
                            "verify requires a reproduction package", status=400)
                    self._json(200, _verified(
                        envelope,
                        pubkey=payload.get("pubkey"),
                        author=payload.get("author"),
                        verified_by="this server, over the package you supplied",
                    ))
                    return
                raise NotFound("no such route")
            except ServerError as exc:
                self._error(exc)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                # a shape the request body didn't satisfy (missing key, wrong
                # type, non-object nested where an object was expected) is a
                # client error → 400, never a dropped connection / 500
                self._error(PublishRejected(f"malformed request: {exc}", status=400))
            except Exception:  # noqa: BLE001 — last-resort boundary
                self._error(_opaque_500())

        # -- helpers ------------------------------------------------------

        def _readable(self, stored: object) -> object:
            """A private publication is never served on ANY read route — not the
            HTML page, the JSON API, or an EvidenceCase URL (review r7: private
            must mean private everywhere, not just on the main page)."""
            if stored.publication.get("visibility") == "private":  # type: ignore[attr-defined]
                raise NotFound("no such publication")
            return stored

        def _require(self, expected: str | None) -> None:
            """Enforce a bearer token when one is configured (constant-time)."""
            if expected is None:
                return  # local-dev open mode
            header = self.headers.get("Authorization", "")
            presented = header[7:] if header.startswith("Bearer ") else ""
            if not hmac.compare_digest(presented, expected):
                raise Unauthorized("missing or invalid bearer token")

        def _read_json(self) -> dict[str, object]:
            raw = self.headers.get("Content-Length")
            try:
                length = int(raw) if raw is not None else 0
            except ValueError as exc:
                # a non-numeric Content-Length is a malformed request, not a 500
                raise PublishRejected(f"invalid Content-Length {raw!r}", status=400) from exc
            if length < 0:
                # a negative length would slip past the size cap and make
                # rfile.read(-1) block reading to EOF — reject it (review r8)
                raise PublishRejected("negative Content-Length", status=400)
            if length > MAX_BODY_BYTES:
                raise PublishRejected("request body too large", status=413)
            data = json.loads(self.rfile.read(length) or b"{}")
            # the top level MUST be a JSON object — a `[]` or a bare scalar would
            # otherwise reach `payload["bundle"]` / `.get(...)` and raise a
            # TypeError/AttributeError deep inside, dropping the request thread
            # with a 500 + traceback instead of a clean 400 (review r13)
            if not isinstance(data, dict):
                raise PublishRejected("request body must be a JSON object", status=400)
            return data

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
