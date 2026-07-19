"""The instrumented-endpoint gateway (endpoint-protocol.md).

A live HTTP surface an instrumented agent talks to:

  POST /runs                          → { run_id }
  POST /runs/{run_id}/events          ← tool_result (values+labels) | tool_call_intent
       (a tool_call_intent is GATED synchronously — this is the tool proxy
        dispatch point: the gateway returns ALLOW/DENY before the tool runs)
  GET  /runs/{run_id}/trace           → the assembled trace/v1 so far

The synchronous gate on each intent is what makes an instrumented endpoint
governance-capable: Lab sees value lineage (carried on the events) and can stop
a sink before it fires. Streaming (SSE) is a transport nicety on top of this
request/response contract; the governance semantics live here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lab_runner.kernel import Kernel, default_registry

from .instrumented import PRODUCER_MODE

_RUNS_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/events$")
_TRACE_RE = re.compile(r"^/runs/([A-Za-z0-9_]+)/trace$")
_MAX_BODY = 8 * 1024 * 1024


@dataclass
class _Run:
    run_id: str
    condition: dict[str, object]
    scenario_id: str
    inputs: dict[str, object]
    kernel: Kernel
    values: list[dict[str, object]] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    seq: int = 0
    labels_carried: bool = True

    def labels_of(self, value_id: str) -> tuple[str, ...]:
        for value in self.values:
            if value["value_id"] == value_id:
                return tuple(value["labels"])  # type: ignore[arg-type]
        return ()

    def trace(self) -> dict[str, object]:
        from lab_contracts import content_hash

        return {
            "schema_version": "trace/v1",
            "trace_id": f"t_{self.run_id}",
            "trial": {"run_id": self.run_id, "scenario_id": self.scenario_id,
                      "condition_id": str(self.condition["id"]), "seed": "s000", "repeat_index": 0},
            "producer": {
                "mode": PRODUCER_MODE,
                "provenance_fidelity": "explicit_flow_tracked" if self.labels_carried else "heuristic_attribution",
                "kernel_version": str(self.condition["kernel"]), "runtime": "lab-gateway@0.1",
            },
            "inputs_digest": content_hash({"inputs": self.inputs}),
            "events": self.events,
            "values": self.values,
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
) -> ThreadingHTTPServer:
    """Build (do not start) a gateway for one condition/scenario.

    Review §8: opening a run requires the bearer `token` (when set); each run
    gets an unpredictable id AND a per-run secret that its subsequent events
    must present, so one client cannot post events into another's run. Quotas
    bound total runs and events per run."""
    import hmac
    import secrets

    kernel = default_registry((str(condition["kernel"]),)).get(str(condition["kernel"]))
    runs: dict[str, _Run] = {}
    run_secrets: dict[str, str] = {}

    def gate_intent(run: _Run, tool: str, arg_bindings: dict[str, str],
                    args: dict[str, object]) -> dict[str, object]:
        run.events.append({"seq": run.seq, "node": "root", "type": "tool_call_intent",
                           "tool": tool, "arg_bindings": arg_bindings})
        run.seq += 1
        decision = kernel.decide(
            enforcement=str(condition["enforcement"]), manifest=manifests[tool], args=args,
            arg_labels={n: run.labels_of(v) for n, v in arg_bindings.items()},
            arg_bindings=arg_bindings, inputs=inputs, policy=condition.get("policy"),  # type: ignore[arg-type]
        )
        run.events.append({"seq": run.seq, "node": "root", "type": "gate_decision", "decision": decision})
        run.seq += 1
        return decision

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _bearer(self) -> str:
            header = self.headers.get("Authorization", "")
            return header[7:] if header.startswith("Bearer ") else ""

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/runs":
                if token is not None and not hmac.compare_digest(self._bearer(), token):
                    self._json(401, {"error": "missing or invalid bearer token"})
                    return
                if len(runs) >= max_runs:
                    self._json(429, {"error": "run quota exceeded"})
                    return
                run_id = f"r_ep_{secrets.token_hex(16)}"  # unpredictable, not sequential
                run_secret = secrets.token_hex(16)
                runs[run_id] = _Run(run_id, condition, scenario_id, inputs, kernel)
                run_secrets[run_id] = run_secret
                self._json(201, {"run_id": run_id, "run_secret": run_secret})
                return
            match = _RUNS_RE.match(self.path)
            if match and match.group(1) in runs:
                run = runs[match.group(1)]
                # the per-run secret authorizes posting events into THIS run
                if not hmac.compare_digest(self._bearer(), run_secrets.get(match.group(1), "")):
                    self._json(401, {"error": "missing or invalid run secret"})
                    return
                if run.seq >= max_events_per_run:
                    self._json(429, {"error": "event quota exceeded"})
                    return
                event = self._body()
                if event.get("type") == "tool_result":
                    known = {v["value_id"] for v in run.values}
                    for value in event.get("values", []):
                        # reject duplicate/missing value ids — a client must not
                        # be able to redefine a value's lineage (review P0.6)
                        vid = value.get("value_id")
                        if not vid or vid in known:
                            self._json(400, {"error": f"duplicate or missing value_id {vid!r}"})
                            return
                        if "labels" not in value:
                            self._json(400, {"error": f"value {vid!r} has no labels"})
                            return
                        known.add(vid)
                        run.values.append(value)
                    run.events.append({"seq": run.seq, "node": "root", "type": "tool_result",
                                      "tool": event.get("tool"),
                                      "produces_value_ids": [v["value_id"] for v in event.get("values", [])]})
                    run.seq += 1
                    if event.get("labels_carried") is False:
                        run.labels_carried = False
                    self._json(200, {"ok": True})
                    return
                if event.get("type") == "tool_call_intent":
                    bindings = dict(event.get("arg_bindings", {}))
                    known = {v["value_id"] for v in run.values}
                    unknown = [vid for vid in bindings.values() if vid not in known]
                    if unknown:
                        # an intent binding an unknown value_id: the gate fails
                        # closed on it, but flag the protocol violation too
                        self._json(400, {"error": f"arg_bindings reference unknown value ids {unknown}"})
                        return
                    decision = gate_intent(run, str(event["tool"]), bindings,
                                          dict(event.get("args", {})))
                    self._json(200, {"decision": decision})  # the tool proxy verdict
                    return
                self._json(400, {"error": "unknown event type"})
                return
            self._json(404, {"error": "no such run"})

        def do_GET(self) -> None:  # noqa: N802
            match = _TRACE_RE.match(self.path)
            if match and match.group(1) in runs:
                if not hmac.compare_digest(self._bearer(), run_secrets.get(match.group(1), "")):
                    self._json(401, {"error": "missing or invalid run secret"})
                    return
                self._json(200, runs[match.group(1)].trace())
                return
            self._json(404, {"error": "no such run"})

        def _body(self) -> dict[str, object]:
            length = min(int(self.headers.get("Content-Length", "0")), _MAX_BODY)
            return json.loads(self.rfile.read(length) or b"{}")

        def _json(self, status: int, obj: object) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)
