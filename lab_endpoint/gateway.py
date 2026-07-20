"""The instrumented-endpoint gateway (endpoint-protocol.md).

A live HTTP surface an instrumented agent talks to:

  POST /runs                          → { run_id, run_secret }
  POST /runs/{run_id}/events          ← tool_result (values+labels) | tool_call_intent
       (a tool_call_intent is GATED synchronously — this is the tool proxy
        dispatch point: the gateway returns ALLOW/DENY before the tool runs)
  POST /runs/{run_id}/finalize        → freeze the run; no further events
  GET  /runs/{run_id}/trace           → the assembled trace/v1 (only after finalize)

The synchronous gate on each intent is what makes an instrumented endpoint
governance-capable: Lab sees value lineage (carried on the events) and can stop
a sink before it fires.

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

from lab_runner.kernel import Kernel, default_registry
from lab_runner.replay import resolve_args

from .instrumented import PRODUCER_MODE

_RUNS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/events$")
_TRACE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trace$")
_FINALIZE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/finalize$")
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
    values: list[dict[str, object]] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    seq: int = 0
    labels_carried: bool = True
    finalized: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def labels_of(self, value_id: str) -> tuple[str, ...]:
        for value in self.values:
            if value["value_id"] == value_id:
                return tuple(value["labels"])  # type: ignore[arg-type]
        return ()

    def provenance_fidelity(self) -> str:
        """explicit_flow_tracked is a claim that a trusted runtime built the
        value lineage with closed constructors — NOT something an untrusted
        client can self-assert (review r8). It is granted ONLY when the operator
        constructed this gateway as an attested `trusted_runtime`; the client's
        `labels_carried` flag can only DOWNGRADE (to heuristic_attribution), never
        upgrade. An ordinary agent talking to the gateway is heuristic_attribution:
        the labels are self-reported, so we do not dress them up as tracked flow."""
        if self.trusted_runtime and self.labels_carried:
            return "explicit_flow_tracked"
        return "heuristic_attribution"

    def trace(self) -> dict[str, object]:
        from lab_contracts import content_hash

        return {
            "schema_version": "trace/v1",
            "trace_id": f"t_{self.run_id}",
            "trial": {"run_id": self.run_id, "scenario_id": self.scenario_id,
                      "condition_id": str(self.condition["id"]), "seed": "s000", "repeat_index": 0},
            "producer": {
                "mode": PRODUCER_MODE,
                "provenance_fidelity": self.provenance_fidelity(),
                "kernel_version": str(self.condition["kernel"]), "runtime": "lab-gateway@0.1",
            },
            "inputs_digest": content_hash({"inputs": self.inputs}),
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
        def log_message(self, *args: object) -> None:
            pass

        def _bearer(self) -> str:
            header = self.headers.get("Authorization", "")
            return header[7:] if header.startswith("Bearer ") else ""

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._route_post()
            except _BodyError as exc:
                self._json(exc.status, {"error": exc.message})

        def _route_post(self) -> None:
            if self.path == "/runs":
                if token is not None and not hmac.compare_digest(self._bearer(), token):
                    self._json(401, {"error": "missing or invalid bearer token"})
                    return
                with global_lock:
                    if len(runs) >= max_runs:
                        self._json(429, {"error": "run quota exceeded"})
                        return
                    run_id = f"r_ep_{secrets.token_hex(16)}"  # unpredictable, not sequential
                    run_secret = secrets.token_hex(16)
                    runs[run_id] = _Run(run_id, condition, scenario_id, inputs, kernel,
                                        trusted_runtime=trusted_runtime)
                    run_secrets[run_id] = run_secret
                self._json(201, {"run_id": run_id, "run_secret": run_secret})
                return

            finalize = _FINALIZE_RE.match(self.path)
            if finalize:
                run = self._authorized_run(finalize.group(1))
                if run is None:
                    return
                with run.lock:
                    run.finalized = True
                self._json(200, {"ok": True, "finalized": True})
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
                known = {v["value_id"] for v in run.values}
                for value in event.get("values", []):
                    vid = value.get("value_id")
                    if not vid or vid in known:
                        self._json(400, {"error": f"duplicate or missing value_id {vid!r}"})
                        return
                    if "labels" not in value:
                        self._json(400, {"error": f"value {vid!r} has no labels"})
                        return
                    # a value must carry its authoritative decision_value so the
                    # gate can reconstruct the args from bindings alone (r8 P0) —
                    # unless it is sensitive (redacted), where the sentinel makes
                    # the gate fail closed on that value
                    if "decision_value" not in value and "sensitive" not in value.get("labels", []):
                        self._json(400, {"error": f"value {vid!r} has no decision_value (and is not sensitive)"})
                        return
                    known.add(vid)
                    run.values.append(value)
                run.events.append({"seq": run.seq, "node": "root", "type": "tool_result",
                                  "tool": event.get("tool"),
                                  "produces_value_ids": [v["value_id"] for v in event.get("values", [])]})
                run.seq += 1
                if event.get("labels_carried") is False:
                    run.labels_carried = False
                self._json(200, {"ok": True, "seq": run.seq})
                return
            if event.get("type") == "tool_call_intent":
                bindings = dict(event.get("arg_bindings", {}))
                values_by_id = {str(v["value_id"]): v for v in run.values}
                unknown = [vid for vid in bindings.values() if vid not in values_by_id]
                if unknown:
                    # an intent binding an unknown value id fails closed; also a
                    # protocol violation (the value must be registered first)
                    self._json(400, {"error": f"arg_bindings reference unknown value ids {unknown}"})
                    return
                # EVERY decision-relevant arg (driving args + args named in a
                # resolve rule) must be bound — otherwise a client could dodge an
                # escalation rule by leaving the arg unbound, so the gate decides
                # over one effect class while the tool runs another (r8 P0).
                tool = str(event["tool"])
                relevant = _decision_relevant_args(manifests.get(tool, {}))
                unbound_relevant = sorted(relevant - set(bindings))
                if unbound_relevant:
                    self._json(409, {"error": f"decision-relevant args must be bound to values: {unbound_relevant}"})
                    return
                # the gate decides on args assembled from the bound ledger values
                # ONLY — assembled the same way exact replay does (r8 P0). A
                # client cannot make the gate see a clean value while the tool
                # would receive a tainted one.
                authoritative = resolve_args(bindings, values_by_id)
                # a client-supplied `args` is accepted ONLY as an assertion of
                # what it will execute; any BOUND arg must canonical-hash-match
                # its provenance value, else the intent is refused (no ALLOW, no
                # trace mutation). Unbound asserted args can't change the verdict
                # (all decision-relevant args are bound, checked above).
                if "args" in event:
                    mismatch = _assertion_mismatch(dict(event.get("args", {})), authoritative)
                    if mismatch is not None:
                        self._json(409, {"error": f"args assertion conflicts with bound provenance: {mismatch}"})
                        return
                decision = gate_intent(run, tool, bindings, authoritative)
                self._json(200, {"decision": decision})  # the tool proxy verdict
                return
            self._json(400, {"error": "unknown event type"})

        def do_GET(self) -> None:  # noqa: N802
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
                trace = run.trace()  # consistent snapshot under the lock
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
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def _decision_relevant_args(manifest: dict[str, object]) -> set[str]:
    """The arg names that can change the gate's verdict for this tool: its
    declared driving args plus any arg named in an effect-resolve rule's
    `when`. These MUST be bound to ledger values so the gate decides over the
    same concrete values the tool will run — an unbound one could dodge an
    escalation rule (r8 P0)."""
    effect: dict[str, object] = manifest.get("effect", {})  # type: ignore[assignment]
    names: set[str] = set(effect.get("driving_args", []))  # type: ignore[arg-type]
    for rule in effect.get("resolve", []):  # type: ignore[union-attr]
        names |= set(rule.get("when", {}).keys())
    return names


def _assertion_mismatch(
    asserted: dict[str, object], authoritative: dict[str, object]
) -> str | None:
    """Compare the caller's asserted concrete args against the authoritative
    args derived from the ledger bindings. Every arg that is BOTH asserted and
    bound must be equal by JCS content hash (so typing/ordering can't smuggle a
    difference past a shallow ==); returns a reason string on the first
    mismatch, else None. Unbound asserted args are ignored here — the handler
    has already required every decision-relevant arg to be bound, so an unbound
    asserted arg cannot influence the verdict."""
    from lab_contracts import content_hash

    for name, value in authoritative.items():
        if name in asserted and content_hash(asserted[name]) != content_hash(value):
            return f"arg {name!r} concrete value does not match its bound provenance value"
    return None


def _reject_constant(_name: str) -> object:
    # NaN/Infinity are not valid governance evidence; reject them at parse time
    raise _BodyError(400, "NaN/Infinity not allowed in request body")
