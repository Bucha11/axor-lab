"""The instrumented-endpoint gateway (endpoint-protocol.md).

A live HTTP surface an instrumented agent talks to:

  POST /runs                          → { run_id, run_secret }
  POST /runs/{run_id}/events          ← tool_result (values+labels) | tool_call_intent
       (a tool_call_intent is GATED synchronously — the gateway returns
        ALLOW/DENY + the authoritative args BEFORE the tool runs)
  POST /runs/{run_id}/finalize        → freeze the run; no further events
  GET  /runs/{run_id}/trace           → the assembled trace/v1 (only after finalize)

TRUST MODEL — read before calling this a "hard" enforcement boundary. The
gateway is a synchronous DECISION point, not a tool executor: it returns a
verdict + the authoritative args a caller must run, but it does not itself invoke
the caller's tool. Enforcement therefore depends on the caller ROUTING execution
through the verdict (a cooperating / attested SDK). An untrusted client can
still ignore an ALLOW's authoritative_args and run something else, and — because
it supplies the value labels — can mislabel an attacker value as prompt_given,
which the reference kernel would treat as trusted. That is why an untrusted
client's trace is `heuristic_attribution` (review r8/r9): the verdict is only as
sound as the labels, and the labels are self-reported. A real enforcement
boundary for an untrusted agent needs a trusted runtime that mints labels from
the tool manifest / observed execution graph, or a signed per-event envelope —
tracked as roadmap. What the gate DOES guarantee unconditionally: it decides on
the value the BINDING names (never a client-forged concrete arg, review r8), so
the recorded evidence and replay can never diverge from the decision.

Concurrency (review r3): the server is threaded, so every run's mutable state is
guarded. Run creation takes a global lock; all reads/writes of one run take that
run's lock, so two events can't grab the same seq, an intent can't observe a
half-registered value, and a trace read can't see a half-written event. An event
may carry `expected_seq` for optimistic concurrency — a mismatch is 409. The
trace is readable only after an explicit finalize, so it is never published mid-write.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lab_contracts import validate_artifact, world_digest
from lab_runner.kernel import Kernel, default_registry

from .gating import GatingError, gated_args, normalize_value_hash, provenance_fidelity
from .instrumented import PRODUCER_MODE

# malformed-event exceptions we translate to a clean 400; anything else is an
# unexpected server fault and becomes an opaque 500 (never leak a traceback).
_CLIENT_FAULTS = (KeyError, TypeError, ValueError, AttributeError)

_RUNS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/events$")
_TRACE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trace$")
_FINALIZE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/finalize$")
_ACK_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trace/ack$")
_MAX_BODY = 8 * 1024 * 1024


class _BodyError(Exception):
    """A malformed request body; carries the HTTP status to return."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Run:
    run_id: str
    condition: dict[str, object]
    scenario_id: str
    inputs: dict[str, object]
    kernel: Kernel
    trusted_runtime: bool = False
    fixtures: dict[str, object] = field(default_factory=dict)
    values: list[dict[str, object]] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    seq: int = 0
    labels_carried: bool = True
    finalized: bool = False
    # delivery lifecycle (review r15/r16): a finalized run is FINALIZED_UNDELIVERED
    # until the client explicitly ACKNOWLEDGES receipt (POST /trace/ack), then
    # DELIVERED. A GET of the trace is NOT proof of delivery — the socket write
    # can fail after the handler returns, or the client can crash before storing
    # the body — so `delivered` is never set on GET (review r16). Only a DELIVERED
    # (acknowledged) run is safe to evict for quota; a fetched-but-unacked trace
    # stays retrievable so the client can retry the fetch.
    delivered: bool = False
    # the frozen trace snapshot taken at finalize — served on every GET and by the
    # ack check, so the body a client acknowledges is exactly the one it read and
    # is stable regardless of later run mutation (there is none post-finalize, but
    # the snapshot makes delivery independent of re-assembly)
    frozen_trace: dict[str, object] | None = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def labels_of(self, value_id: str) -> tuple[str, ...]:
        for value in self.values:
            if value["value_id"] == value_id:
                return tuple(value["labels"])  # type: ignore[arg-type]
        return ()

    def trace(self) -> dict[str, object]:
        return {
            "schema_version": "trace/v1",
            "trace_id": f"t_{self.run_id}",
            "trial": {"run_id": self.run_id, "scenario_id": self.scenario_id,
                      "condition_id": str(self.condition["id"]), "seed": "s000", "repeat_index": 0},
            "producer": {
                "mode": PRODUCER_MODE,
                # fidelity is an operator-attested-runtime claim, not client-set
                "provenance_fidelity": provenance_fidelity(self.trusted_runtime, self.labels_carried),
                "kernel_version": str(self.condition["kernel"]), "runtime": "lab-gateway@0.1",
            },
            "inputs_digest": world_digest(self.inputs, self.fixtures),
            "events": list(self.events),
            "values": list(self.values),
        }


def make_gateway(
    condition: dict[str, object],
    manifests: dict[str, dict[str, object]],
    inputs: dict[str, object],
    scenario_id: str,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
    max_runs: int = 1000,
    max_events_per_run: int = 10000,
    trusted_runtime: bool = False,
    fixtures: dict[str, object] | None = None,
) -> ThreadingHTTPServer:
    """Build (do not start) a gateway for one condition/scenario.

    Opening a run requires the bearer `token` (when set); each run gets an
    unpredictable id AND a per-run secret its subsequent events must present.
    Quotas bound total runs and events per run.

    `trusted_runtime` (operator-set, default False) governs provenance honesty:
    only when the operator attests the caller is a first-party SDK that builds
    the ledger with closed constructors may a trace claim explicit_flow_tracked.
    For an ordinary untrusted agent it stays False, so labels are reported as
    heuristic_attribution — the gateway never lets a client self-certify tracked
    provenance (review r8). A cryptographic per-event envelope is the roadmap for
    attesting an untrusted multi-tenant caller."""
    import hmac
    import secrets

    kernel = default_registry((str(condition["kernel"]),)).get(str(condition["kernel"]))
    runs: dict[str, _Run] = {}
    run_secrets: dict[str, str] = {}
    global_lock = threading.Lock()  # guards run creation + the runs/secrets maps

    def gate_intent(run: _Run, tool: str, arg_bindings: dict[str, str],
                    args: dict[str, object]) -> dict[str, object]:
        """Gate an intent. `args` are the AUTHORITATIVE args assembled from the
        bound ledger values (resolve_args) — never the caller's concrete args,
        which are validated as an assertion by the handler before we get here.
        So the value the gate decides on is the value the labels describe."""
        call_id = f"call_root_{run.seq}"
        run.events.append({"seq": run.seq, "node": "root", "type": "tool_call_intent",
                           "tool": tool, "call_id": call_id, "arg_bindings": arg_bindings})
        run.seq += 1
        decision = kernel.decide(
            enforcement=str(condition["enforcement"]), manifest=manifests[tool], args=args,
            arg_labels={n: run.labels_of(v) for n, v in arg_bindings.items()},
            arg_bindings=arg_bindings, inputs=inputs, policy=condition.get("policy"),  # type: ignore[arg-type]
        )
        run.events.append({"seq": run.seq, "node": "root", "type": "gate_decision",
                           "call_id": call_id, "decision": decision})
        run.seq += 1
        return decision

    class Handler(BaseHTTPRequestHandler):
        _sent = False

        def log_message(self, *args: object) -> None:
            pass

        def _bearer(self) -> str:
            header = self.headers.get("Authorization", "")
            return header[7:] if header.startswith("Bearer ") else ""

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._route_post()
            except _BodyError as exc:
                self._safe_error(exc.status, exc.message)
            except _CLIENT_FAULTS as exc:
                # a malformed event (missing/wrong-typed field) is the client's
                # fault → a clean 400, never a stack trace or a 500
                self._safe_error(400, f"malformed event: {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 — last-resort boundary
                # an unexpected server fault: fail closed with an OPAQUE 500 so
                # internal details never leak to the caller
                self._safe_error(500, "internal error")

        def _safe_error(self, status: int, message: str) -> None:
            # only emit if the handler has not already written a response (an
            # error raised AFTER a partial success must not double-send)
            if not self._sent:
                self._json(status, {"error": message})

        def _route_post(self) -> None:
            if self.path == "/runs":
                if token is not None and not hmac.compare_digest(self._bearer(), token):
                    self._json(401, {"error": "missing or invalid bearer token"})
                    return
                with global_lock:
                    if len(runs) >= max_runs:
                        # evict the oldest run that is finalized AND DELIVERED — its
                        # trace was assembled and actually read, so discarding it is
                        # safe (LRU, dict is insertion-ordered). A finalized run
                        # whose trace was never fetched is NOT evicted: losing it
                        # before the client reads it is the round-15 bug. If nothing
                        # is delivered-and-finalized, refuse (429) rather than drop
                        # an unread trace.
                        evicted = next(
                            (rid for rid, r in runs.items() if r.finalized and r.delivered), None
                        )
                        if evicted is None:
                            self._json(429, {
                                "error": "run quota exceeded (no delivered finalized run to evict)"
                            })
                            return
                        del runs[evicted]
                        run_secrets.pop(evicted, None)
                    run_id = f"r_ep_{secrets.token_hex(16)}"  # unpredictable, not sequential
                    run_secret = secrets.token_hex(16)
                    runs[run_id] = _Run(run_id, condition, scenario_id, inputs, kernel,
                                        trusted_runtime=trusted_runtime, fixtures=dict(fixtures or {}))
                    run_secrets[run_id] = run_secret
                self._json(201, {"run_id": run_id, "run_secret": run_secret})
                return

            finalize = _FINALIZE_RE.match(self.path)
            if finalize:
                run = self._authorized_run(finalize.group(1))
                if run is None:
                    return
                with run.lock:
                    if run.finalized:
                        self._json(200, {"ok": True, "finalized": True})
                        return
                    # the assembled trace must be a CONFORMANT trace/v1 before we
                    # freeze and serve it — validate schema AND semantics at the
                    # finalize boundary so an out-of-spec accumulation can never be
                    # published as evidence (review r14). Fail closed: the run
                    # stays open so the caller can see exactly what is wrong.
                    trace = run.trace()
                    # validate_artifact("trace") already runs trace_semantics, so
                    # calling it again would surface every semantic error twice and
                    # crowd out other causes in details[:10] (review r15 P2)
                    errors = validate_artifact(trace, "trace")
                    if errors:
                        self._json(422, {
                            "error": "assembled trace is not a conformant trace/v1",
                            "details": errors[:10],
                        })
                        return
                    run.finalized = True
                    # freeze the conformant body once, so every GET and the ack
                    # serve the identical bytes independent of quota/re-assembly
                    run.frozen_trace = trace
                self._json(200, {"ok": True, "finalized": True})
                return

            ack = _ACK_RE.match(self.path)
            if ack:
                run = self._authorized_run(ack.group(1))
                if run is None:
                    return
                with run.lock:
                    if not run.finalized:
                        self._json(409, {"error": "run not finalized; nothing to acknowledge"})
                        return
                    # the client confirms it has STORED the trace body — only now is
                    # the run safe to evict for quota. Delivery is client-confirmed,
                    # never inferred from a GET that may not have arrived (review r16)
                    run.delivered = True
                self._json(200, {"ok": True, "delivered": True})
                return

            events = _RUNS_RE.match(self.path)
            if events:
                run = self._authorized_run(events.group(1))
                if run is None:
                    return
                event = self._read_body()
                with run.lock:  # serialize all mutation of THIS run
                    self._handle_event(run, event)
                return
            self._json(404, {"error": "no such run"})

        def _authorized_run(self, run_id: str) -> _Run | None:
            """Resolve a run and check its per-run secret, or emit the error."""
            with global_lock:
                run = runs.get(run_id)
                secret = run_secrets.get(run_id, "")
            if run is None:
                self._json(404, {"error": "no such run"})
                return None
            if not hmac.compare_digest(self._bearer(), secret):
                self._json(401, {"error": "missing or invalid run secret"})
                return None
            return run

        def _handle_event(self, run: _Run, event: dict[str, object]) -> None:
            # holds run.lock
            if run.finalized:
                self._json(409, {"error": "run is finalized; no further events"})
                return
            if run.seq >= max_events_per_run:
                self._json(429, {"error": "event quota exceeded"})
                return
            expected = event.get("expected_seq")
            if expected is not None and int(expected) != run.seq:  # type: ignore[arg-type]
                self._json(409, {"error": f"expected_seq {expected} != current {run.seq}"})
                return

            if event.get("type") == "tool_result":
                # every event names a tool that must exist in the manifest set —
                # a value minted "by" an unknown tool cannot be governed and is
                # rejected, never silently accepted (review r14)
                tool = event.get("tool")
                if not isinstance(tool, str) or tool not in manifests:
                    self._json(400, {"error": f"unknown or missing tool {tool!r}"})
                    return
                raw_values = event.get("values", [])
                if not isinstance(raw_values, list):
                    self._json(400, {"error": "tool_result.values must be a list"})
                    return
                known = {v["value_id"] for v in run.values}
                for value in raw_values:
                    if not isinstance(value, dict):
                        self._json(400, {"error": "each tool_result value must be an object"})
                        return
                    vid = value.get("value_id")
                    if not vid or vid in known:
                        self._json(400, {"error": f"duplicate or missing value_id {vid!r}"})
                        return
                    labels = value.get("labels")
                    if not isinstance(labels, list):
                        self._json(400, {"error": f"value {vid!r} labels must be a list"})
                        return
                    # a value must carry its authoritative decision_value so the
                    # gate can reconstruct the args from bindings alone (r8 P0) —
                    # unless it is sensitive (redacted). A redacted value still has
                    # to PIN its bytes with a client-supplied canonical_value_hash
                    # (the server can't derive one without the value), or the
                    # assembled trace fails trace_semantics on finalize (review r14).
                    if "decision_value" not in value:
                        if "sensitive" not in labels:
                            self._json(400, {"error": f"value {vid!r} has no decision_value (and is not sensitive)"})
                            return
                        if not value.get("canonical_value_hash"):
                            self._json(400, {"error": f"redacted sensitive value {vid!r} must carry a canonical_value_hash"})
                            return
                    known.add(vid)
                    # derive an authoritative canonical_value_hash from the
                    # decision_value (never trust a client-supplied hash) so every
                    # trace value is self-verifying (contracts trace_semantics, r13)
                    run.values.append(normalize_value_hash(value))
                run.events.append({"seq": run.seq, "node": "root", "type": "tool_result",
                                  "tool": tool,
                                  "produces_value_ids": [v["value_id"] for v in raw_values]})
                run.seq += 1
                if event.get("labels_carried") is False:
                    run.labels_carried = False
                self._json(200, {"ok": True, "seq": run.seq})
                return
            if event.get("type") == "tool_call_intent":
                tool_field = event.get("tool")
                if not isinstance(tool_field, str) or tool_field not in manifests:
                    # an intent for a tool with no manifest cannot be gated (no
                    # effect / driving args to reason over) — fail closed with a
                    # clean 400 rather than a KeyError→500 inside gate_intent (r14)
                    self._json(400, {"error": f"unknown or missing tool {tool_field!r}"})
                    return
                raw_bindings = event.get("arg_bindings", {})
                if not isinstance(raw_bindings, dict):
                    self._json(400, {"error": "tool_call_intent.arg_bindings must be an object"})
                    return
                asserted_raw = event.get("args")
                if "args" in event and not isinstance(asserted_raw, dict):
                    self._json(400, {"error": "tool_call_intent.args must be an object"})
                    return
                bindings = dict(raw_bindings)
                values_by_id = {str(v["value_id"]): v for v in run.values}
                unknown = [vid for vid in bindings.values() if vid not in values_by_id]
                if unknown:
                    # an intent binding an unknown value id fails closed; also a
                    # protocol violation (the value must be registered first)
                    self._json(400, {"error": f"arg_bindings reference unknown value ids {unknown}"})
                    return
                # authoritative args come SOLELY from the bindings; every
                # decision-relevant arg must be bound, and a conflicting concrete
                # `args` assertion fails closed — shared with the in-process path
                # so the two can't drift (review r8/r9)
                try:
                    authoritative = gated_args(
                        manifests[tool_field], bindings, values_by_id,
                        asserted=dict(asserted_raw) if "args" in event else None,
                    )
                except GatingError as exc:
                    self._json(409, {"error": str(exc)})
                    return
                decision = gate_intent(run, tool_field, bindings, authoritative)
                # return the AUTHORITATIVE args a cooperating proxy must execute,
                # so an honest client runs the bound value, not its own (review r9)
                self._json(200, {"decision": decision, "authoritative_args": authoritative})
                return
            self._json(400, {"error": "unknown event type"})

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._route_get()
            except _CLIENT_FAULTS as exc:
                self._safe_error(400, f"malformed request: {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 — last-resort boundary
                self._safe_error(500, "internal error")

        def _route_get(self) -> None:
            match = _TRACE_RE.match(self.path)
            if not match:
                self._json(404, {"error": "no such run"})
                return
            run = self._authorized_run(match.group(1))
            if run is None:
                return
            with run.lock:
                if not run.finalized:
                    # never publish a mid-write trace — require explicit finalize
                    self._json(409, {"error": "run not finalized; POST /finalize first"})
                    return
                # serve the FROZEN snapshot; a GET does NOT mark the run delivered.
                # Delivery is only confirmed by an explicit POST /trace/ack, so a
                # GET whose socket write fails (or a client that crashes before
                # storing the body) leaves the trace retrievable, never evicted
                # before the client actually has it (review r16)
                trace = run.frozen_trace if run.frozen_trace is not None else run.trace()
            self._json(200, trace)

        def _read_body(self) -> dict[str, object]:
            raw = self.headers.get("Content-Length")
            try:
                length = int(raw) if raw is not None else 0
            except ValueError as exc:
                raise _BodyError(400, f"invalid Content-Length {raw!r}") from exc
            if length < 0:
                raise _BodyError(400, "negative Content-Length")
            if length > _MAX_BODY:
                raise _BodyError(413, "request body too large")
            data = self.rfile.read(length) if length else b""
            try:
                obj = json.loads(data or b"{}", parse_constant=_reject_constant)
            except (ValueError, _BodyError) as exc:
                if isinstance(exc, _BodyError):
                    raise
                raise _BodyError(400, f"invalid JSON body: {exc}") from exc
            if not isinstance(obj, dict):
                raise _BodyError(400, "request body must be a JSON object")
            return obj

        def _json(self, status: int, obj: object) -> None:
            self._sent = True
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def _reject_constant(_name: str) -> object:
    # NaN/Infinity are not valid governance evidence; reject them at parse time
    raise _BodyError(400, "NaN/Infinity not allowed in request body")
