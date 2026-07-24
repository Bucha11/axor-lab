"""The HTTP surface — a stdlib `http.server` app over the PublicationStore.

Routes:
  GET  /                                  catalog (HTML, public only)
  GET  /api/publications                  catalog (JSON, public only)
  GET  /e/{id}                            publication page (HTML; private → 404)
  GET  /e/{id}/evidence/{trace_id}        EvidenceCase (HTML)
  GET  /api/publications/{id}             publication (JSON) + provenance axes
  POST /api/publications                  publish handshake        [write token]
  POST /api/publications/{id}/reproductions   append an attestation [write token]
  POST /api/publications/{id}/takedown    remove from catalog       [admin token]
  POST /api/incidents                     import an incident package [write token]
  GET  /api/incidents                     list imported incidents (JSON)
  GET  /api/incidents/{id}                one incident: full package + bundle
  POST /api/incidents/{id}/approve        sign off on an incident   [write token]
                                          (Security tier when hosted_mode)
  POST /api/incidents/{id}/pin            pin the incident's verdict into the
                                          regression corpus       [write token]
  GET  /api/traces/{trace_id}             resolver: publications + incidents
                                          containing this trace (404 if none)
  GET  /api/audit                         workflow history (Security tier when hosted)
  GET  /api/regression                    the incident regression corpus (Security)
  POST /api/regression/run                re-verify the corpus (Security)
  GET  /api/compliance/report             period compliance report (Security tier)
  GET  /api/license/status                the workspace entitlement (tier + modules)

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

from lab_runner.errors import IncidentImportError, IncidentReplayMismatch

from .audit import (
    APPROVAL_GRANTED,
    INCIDENT_IMPORTED,
    INCIDENT_PINNED,
    REGRESSION_RUN,
    REPORT_EXPORTED,
    AuditLog,
)
from .errors import NotFound, PublishRejected, ServerError
from .html import render_catalog, render_evidence, render_publication
from .incidents import IncidentStore
from .license import LicenseRequired, require_workspace_tier
from .regression_store import MUST_BLOCK, PinStore, side_for
from .store import PublicationStore

MAX_BODY_BYTES = 32 * 1024 * 1024  # uploaded bundles are bounded (threat-model §4)
# trace_ids carry the scenario slug (e.g. banking-exfil-01), so the evidence
# route must accept hyphens in the trace_id segment; both segments stay to a
# safe character class (no '/', '.', or path-traversal metacharacters)
_EVIDENCE_RE = re.compile(r"^/e/([A-Za-z0-9_-]+)/evidence/([A-Za-z0-9_-]+)$")
_PUB_RE = re.compile(r"^/e/([A-Za-z0-9_-]+)$")
_API_PUB_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)$")
_API_BUNDLE_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/bundle$")
_API_REPRO_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/reproductions$")
_API_TAKEDOWN_RE = re.compile(r"^/api/publications/([A-Za-z0-9_]+)/takedown$")
_API_INCIDENT_RE = re.compile(r"^/api/incidents/([A-Za-z0-9_-]+)$")
_API_APPROVE_RE = re.compile(r"^/api/incidents/([A-Za-z0-9_-]+)/approve$")
_API_PIN_RE = re.compile(r"^/api/incidents/([A-Za-z0-9_-]+)/pin$")
# trace_ids carry the scenario slug (hyphens), same safe class as the evidence route
_API_TRACE_RE = re.compile(r"^/api/traces/([A-Za-z0-9_-]+)$")


class Unauthorized(ServerError):
    """A mutation was attempted without the required bearer token."""


def _opaque_500() -> ServerError:
    """A generic 500 that leaks NO internal detail to the client (review r13).
    Unexpected exceptions map to this so a stack of internal state never reaches
    the socket; the real exception can still be logged server-side."""
    exc = ServerError("internal server error")
    exc.status = 500  # type: ignore[attr-defined]
    return exc


def _compliance_report(
    incidents: IncidentStore,
    audit: AuditLog,
    since: str | None,
    until: str | None,
) -> dict[str, object]:
    """A period compliance report aggregated over the audit log: action counts in
    the window, and each incident imported in the window with its approval state.
    A read-only projection of the append-only log — the evidence, summarized."""
    from .audit import _utc_now_iso

    events = audit.list(since=since, until=until)
    counts: dict[str, int] = {}
    for event in events:
        action = str(event.get("action", ""))
        counts[action] = counts.get(action, 0) + 1
    rows: list[dict[str, object]] = []
    for event in events:
        if event.get("action") != INCIDENT_IMPORTED:
            continue
        incident_id = str(event.get("target", ""))
        approvals = audit.approvals_for(incident_id)
        pinned = any(
            e.get("action") == INCIDENT_PINNED and e.get("target") == incident_id
            for e in events
        )
        rows.append({
            "incident_id": incident_id,
            "trace_id": (event.get("detail") or {}).get("trace_id"),  # type: ignore[union-attr]
            "imported_at": event.get("ts"),
            "approved": bool(approvals),
            "pinned": pinned,
            "approvals": [
                {"actor": a.get("actor"), "ts": a.get("ts"),
                 "note": (a.get("detail") or {}).get("note")}  # type: ignore[union-attr]
                for a in approvals
            ],
        })
    return {
        "schema_version": "axor-lab-compliance/v1",
        "window": {"since": since, "until": until},
        "generated_at": _utc_now_iso(),
        "action_counts": counts,
        "total_events": len(events),
        "incidents": rows,
    }


def _run_regression(incidents: IncidentStore, pins: PinStore) -> dict[str, object]:
    """Re-verify every pinned incident under its recorded condition, the SAME
    `check_pins` the CLI `axor-lab regress` runs. A must_block pin that still
    denies is held; one that no longer denies escaped. A must_pass pin with no new
    denial passed; a new denial regressed. A trace that cannot be located/replayed
    is skipped with its reason — never silently counted as a pass."""
    from lab_runner.axor_backend import resolve_recorded_kernel_for_trace
    from lab_runner.errors import UnknownKernelError
    from lab_runner.regression import (
        STATUS_DIFFERS,
        STATUS_MATCHES,
        RegressionPin,
        check_pins,
    )

    rows: list[dict[str, object]] = []
    for record in pins.list():
        trace_id = str(record["trace_id"])
        side = str(record["side"])
        try:
            stored = incidents.get(str(record["incident_id"]))
        except NotFound:
            rows.append({"trace_id": trace_id, "side": side, "outcome": "skipped",
                         "status": "incident_missing"})
            continue
        bundle = stored.bundle
        trace = stored.trace
        trial: dict[str, object] = trace["trial"]  # type: ignore[assignment]
        conditions = {str(c["id"]): c for c in bundle["conditions"]}  # type: ignore[union-attr]
        scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
        manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
        condition = conditions[str(trial["condition_id"])]
        inputs = scenarios[str(trial["scenario_id"])].get("inputs", {})  # type: ignore[union-attr]
        pin_obj = RegressionPin(
            trace_id=trace_id, trace_ref=str(record["trace_ref"]),
            expected_verdict=str(record["expected_verdict"]),
            expected_sequence=tuple(str(v) for v in record["expected_sequence"]),  # type: ignore[union-attr]
        )
        try:
            kernel = resolve_recorded_kernel_for_trace(bundle, trace)
        except UnknownKernelError:
            rows.append({"trace_id": trace_id, "side": side, "outcome": "skipped",
                         "status": "kernel_unsupported"})
            continue
        result = check_pins((pin_obj,), {trace_id: trace}, condition, kernel, manifests, inputs)[0]
        status = str(result["status"])
        if status == STATUS_MATCHES:
            outcome = "held" if side == MUST_BLOCK else "passed"
        elif status == STATUS_DIFFERS:
            outcome = "escaped" if side == MUST_BLOCK else "regressed"
        else:
            outcome = "skipped"
        rows.append({
            "trace_id": trace_id, "incident_id": record["incident_id"], "side": side,
            "expected": result["expected"], "actual": result["actual"],
            "status": status, "outcome": outcome,
        })
    tally = {k: sum(1 for r in rows if r["outcome"] == k)
             for k in ("held", "passed", "regressed", "escaped", "skipped")}
    return {"rows": rows, **tally,
            "safe_to_ship": tally["regressed"] == 0 and tally["escaped"] == 0}


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
    license_obj: object | None = None,
    hosted_mode: bool = False,
) -> ThreadingHTTPServer:
    """Build (do not start) an HTTP server bound to host:port.

    `license_obj` is an optional verified `lab_server.license.License` — the
    workspace's entitlement (tier + modules), surfaced at GET /api/license/status.
    None means the community tier (free, local/public); a paid workspace passes a
    license the vendor signed.

    `hosted_mode` turns on entitlement ENFORCEMENT (axor-packaging.md §3): the
    paid Security-Workspace endpoints (history, approvals, compliance export)
    require a security-tier license and answer 402 otherwise. Off (the default) is
    the self-hosted / local posture — unlimited local use, nothing gated. A safety
    feature never consults either flag."""
    store = PublicationStore(
        root=store_root, known_keys=known_keys or {},
        server_id=server_id, server_key_id=server_key_id,
        server_signing_key=server_signing_key,
    )
    # incidents live in a SEPARATE namespace under the same root — imported
    # production traces are not publications and never enter the catalog
    incidents = IncidentStore(root=store_root / "incidents")
    # the append-only workflow log — the spine of the paid history / approvals /
    # compliance-export features (Security tier when hosted_mode is on)
    audit = AuditLog(root=store_root / "audit")
    # the incident regression corpus (Security tier): pinned verdicts re-verified
    # by a regression run, closing the incident → regression → report chain
    pins = PinStore(root=store_root / "pins")

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
                if path == "/api/publications":
                    # the JSON mirror of the HTML catalog, same visibility
                    # semantics as render_catalog/store.catalog(): PUBLIC only.
                    # unlisted stays capability-URL-reachable (GET by id) but is
                    # never listed; private is never served anywhere (review §7).
                    listing = [
                        {
                            "publication_id": s.publication["publication_id"],
                            "question": s.publication["question"],
                            "url": f"/e/{s.publication['publication_id']}",
                            "license": s.publication.get("license"),
                            "provenance": s.axes(),
                        }
                        for s in sorted(
                            store.catalog(),
                            key=lambda s: str(s.publication["publication_id"]),
                        )
                    ]
                    self._json(200, {"publications": listing})
                    return
                if path == "/api/license/status":
                    # the workspace entitlement (axor-packaging.md §4): tier +
                    # modules the UI reads to unlock vs render locked. None → the
                    # community tier; a safety feature never consults this.
                    if license_obj is None:
                        self._json(200, {"active": False, "workspace_tier": "community"})
                        return
                    from .license import KNOWN_MODULES
                    self._json(200, {
                        "active": True,
                        "organization": license_obj.organization,
                        "workspace_tier": license_obj.workspace_tier,
                        "modules": {m: license_obj.has_module(m) for m in KNOWN_MODULES},
                        "governed_node_ceiling": license_obj.governed_node_ceiling,
                        "self_hosted_runner": license_obj.self_hosted_runner,
                        "expires_at": license_obj.expires_at,
                    })
                    return
                if path == "/api/audit":
                    # Security-tier paid feature: the workflow history — the
                    # ordered record of imports, approvals and exports.
                    self._gate("security")
                    q = parse_qs(split.query)
                    self._json(200, {"events": audit.list(
                        since=q.get("since", [None])[0], until=q.get("until", [None])[0],
                    )})
                    return
                if path == "/api/compliance/report":
                    # Security-tier paid feature: a period compliance report
                    # aggregated over the audit log (the export is itself logged).
                    self._gate("security")
                    q = parse_qs(split.query)
                    since = q.get("since", [None])[0]
                    until = q.get("until", [None])[0]
                    report = _compliance_report(incidents, audit, since, until)
                    audit.append(REPORT_EXPORTED, detail={"since": since, "until": until})
                    self._json(200, report)
                    return
                if path == "/api/regression":
                    # Security-tier paid feature: the incident regression corpus
                    self._gate("security")
                    self._json(200, {"pins": pins.list()})
                    return
                if path == "/api/incidents":
                    pinned_traces = {str(p["trace_id"]) for p in pins.list()}
                    self._json(200, {"incidents": [
                        {**s.summary(),
                         "approved": audit.is_approved(s.incident_id),
                         "pinned": s.trace_id in pinned_traces}
                        for s in incidents.list()
                    ]})
                    return
                api_incident = _API_INCIDENT_RE.match(path)
                if api_incident:
                    stored_incident = incidents.get(api_incident.group(1))
                    self._json(200, {
                        "incident_id": stored_incident.incident_id,
                        "imported_at": stored_incident.imported_at,
                        # the accepted envelope, verbatim (trace/scenario/
                        # manifests/condition/source) …
                        **stored_incident.package,
                        # … plus the trace-replay bundle the shared core built,
                        # so `axor-lab replay` can consume the download directly
                        "bundle": stored_incident.bundle,
                    })
                    return
                api_trace = _API_TRACE_RE.match(path)
                if api_trace:
                    # resolver: where does this trace_id live? Searches the
                    # published bundles (private never revealed) AND the
                    # imported incidents; 404 when it is nowhere.
                    trace_id = api_trace.group(1)
                    pubs = store.publications_with_trace(trace_id)
                    incs = incidents.incidents_with_trace(trace_id)
                    if not pubs and not incs:
                        raise NotFound(f"trace {trace_id} not found")
                    self._json(200, {
                        "trace_id": trace_id, "publications": pubs, "incidents": incs,
                    })
                    return
                evidence = _EVIDENCE_RE.match(path)
                if evidence:
                    stored = self._readable(store.get(evidence.group(1)))
                    if evidence.group(2) not in stored.traces:
                        raise NotFound("trace not found")
                    policy = parse_qs(split.query).get("policy", [None])[0]
                    self._html(render_evidence(stored, evidence.group(2), policy))
                    return
                api_bundle = _API_BUNDLE_RE.match(path)
                if api_bundle:
                    # the reproduction PACKAGE — the bundle + every trace body, so
                    # a reader can actually reconstruct the bundle dir and run
                    # `axor-lab replay`. Without this the page's reproduce command
                    # named a directory the reader never received (review r13).
                    stored = self._readable(store.get(api_bundle.group(1)))
                    self._json(200, {
                        # a VERSIONED reproduction package — the verifier treats a
                        # package bearing this schema as server-issued, so every
                        # proof object (receipt/publication/acceptance) is MANDATORY
                        # and stripping one is a verification failure (review r16)
                        "schema_version": "axor-reproduction-package/v1",
                        # the PUBLICATION body travels too, so an offline reader can
                        # verify the author/server actually asserted THESE claims —
                        # not just that the bundle bytes are intact (review r15)
                        "publication": stored.publication,
                        "bundle": stored.bundle,
                        "traces": list(stored.traces.values()),
                        # a PORTABLE verification receipt so a reader can verify
                        # the download offline (author/key_id/signature/signed_ref)
                        # without trusting this server (review r14)
                        "receipt": stored.receipt(),
                        # the server's signed ACCEPTANCE receipt (review r15). v0.3
                        # keeps publication + bundle hash + optional signature +
                        # reproduction records; the reacceptance/history chains are
                        # deferred, so there is no acceptance_history to carry.
                        "acceptance": store.acceptance(stored),
                    })
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
                        traces=payload["traces"],
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
                if self.path == "/api/incidents":
                    # the HTTP twin of `axor-lab import-incident`: the SAME
                    # shared core (validation + config hash + replay under the
                    # recorded condition) runs BEFORE anything is stored.
                    # Gated by the write token like publish; the body cap in
                    # _read_json already bounds the upload.
                    self._require(write_token)
                    try:
                        stored_incident, created = incidents.import_package(payload)
                    except IncidentReplayMismatch as exc:
                        # the honest 422: the recorded verdicts do not reproduce
                        # under the shipped condition — show both sides
                        self._json(422, {"error": str(exc), "replay": exc.detail})
                        return
                    except IncidentImportError as exc:
                        raise PublishRejected(str(exc), status=422) from exc
                    if created:
                        # the workflow log records a real intake; an idempotent
                        # re-import mints nothing, so it is not re-logged
                        audit.append(
                            INCIDENT_IMPORTED, target=stored_incident.incident_id,
                            detail={"trace_id": stored_incident.trace_id},
                        )
                    self._json(201 if created else 200, {
                        "incident_id": stored_incident.incident_id,
                        "trace_id": stored_incident.trace_id,
                        "replay": "match",
                        "url": f"/i/{stored_incident.incident_id}",
                    })
                    return
                approve = _API_APPROVE_RE.match(self.path)
                if approve:
                    # Security-tier paid feature: sign off on an incident so its
                    # incident→regression conversion is auditable. Write-token
                    # gated like other mutations; 402 in hosted mode below tier.
                    self._require(write_token)
                    self._gate("security")
                    incident_id = approve.group(1)
                    incidents.get(incident_id)  # 404 if the incident is unknown
                    approver = str(payload.get("approver", "")).strip() or "operator"
                    note = str(payload.get("note", "")).strip()
                    entry = audit.append(
                        APPROVAL_GRANTED, actor=approver, target=incident_id,
                        detail={"note": note} if note else None,
                    )
                    self._json(200, {
                        "incident_id": incident_id, "approved": True, "approval": entry,
                    })
                    return
                pinm = _API_PIN_RE.match(self.path)
                if pinm:
                    # Security-tier paid feature: pin the incident's verdict into
                    # the regression corpus so a later run re-verifies it. Uses the
                    # SAME lab_runner.regression.pin the CLI does.
                    from lab_runner.regression import pin as pin_trace
                    self._require(write_token)
                    self._gate("security")
                    incident_id = pinm.group(1)
                    stored = incidents.get(incident_id)  # 404 if unknown
                    trace = stored.trace
                    # the last recorded gate verdict is the headline the pin asserts
                    verdicts = [str(e["decision"]["verdict"])  # type: ignore[index]
                                for e in trace["events"]  # type: ignore[union-attr]
                                if e.get("type") == "gate_decision"]
                    if not verdicts:
                        raise PublishRejected("incident trace has no gate decision to pin", status=422)
                    final = verdicts[-1]
                    pin_obj = pin_trace(trace, final)
                    record = pins.add(
                        incident_id=incident_id, trace_id=pin_obj.trace_id,
                        trace_ref=pin_obj.trace_ref, expected_verdict=pin_obj.expected_verdict,
                        expected_sequence=list(pin_obj.expected_sequence), side=side_for(final),
                    )
                    audit.append(INCIDENT_PINNED, target=incident_id,
                                 detail={"verdict": final, "side": record["side"]})
                    self._json(200, {"incident_id": incident_id, "pinned": True, "pin": record})
                    return
                if self.path == "/api/regression/run":
                    # Security-tier paid feature: re-verify the corpus and record
                    # the run in the audit trail (closing incident → regression →
                    # report). Reads no body.
                    self._gate("security")
                    report = _run_regression(incidents, pins)
                    audit.append(REGRESSION_RUN, detail={
                        k: report[k] for k in ("held", "passed", "regressed", "escaped", "skipped")
                    })
                    self._json(200, report)
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

        def _gate(self, tier: str) -> None:
            """Entitlement gate for a paid workspace feature. A no-op unless
            `hosted_mode` is on; when it is, a workspace below `tier` gets a 402
            that names what to buy. Self-hosted / local use is never gated, and a
            safety feature never calls this."""
            if not hosted_mode:
                return
            try:
                require_workspace_tier(license_obj, tier)  # type: ignore[arg-type]
            except LicenseRequired as exc:
                raise PublishRejected(str(exc), status=402) from exc

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
