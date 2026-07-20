# Axor Lab — Endpoint Protocol (v1, post-MVP)

Endpoint ingest splits into two modes that the UI must present separately, because provenance is only possible in one.

## Instrumented endpoint (advisory decision API)

The agent participates: it emits value-carrying events, and each tool-call intent is **gated synchronously** — the gateway returns a verdict and the authoritative args **before** the tool runs. This is a *decision point*, **not** a tool executor: the gateway does not invoke the caller's tool. Enforcement therefore depends on the caller ROUTING execution through the verdict — a cooperating (ideally attested) SDK/proxy. An untrusted client can ignore the verdict, and because it supplies the value labels it can mislabel a value; that is why an untrusted client's trace is `heuristic_attribution` (see fidelity below). What the gate guarantees unconditionally is that it decides on the value the *binding* names, so the recorded evidence and replay never diverge from the decision.

Actual routes (the code — no server-side tool dispatch route exists):

```
POST /runs                          → { run_id, run_secret }        # run_secret authenticates every later event
POST /runs/{run_id}/events          ← tool_result { values:[{value_id, decision_value, labels, sources}], labels_carried? }
                                    ← tool_call_intent { tool, arg_bindings:{arg→value_id}, args? }
     → for a tool_call_intent: { decision:{verdict, gate, driving_value_id, reason}, authoritative_args }
POST /runs/{run_id}/finalize        → freeze the run; no further events
GET  /runs/{run_id}/trace           → the assembled trace/v1 (only after finalize)
```

- The gate decides on `authoritative_args`, assembled SOLELY from `arg_bindings → decision_value` (never the client's concrete `args`, which are accepted only as an assertion and canonical-hash-checked against the bound values). A binding to an unknown value id, an unbound decision-relevant/required/asserted arg, or a mismatched assertion is refused (`4xx`) — never a silent ALLOW.
- `authoritative_args` is the COMPLETE, executable call: every schema-required arg (and every arg the caller will pass) must be bound to a ledger value, so a cooperating proxy runs exactly it, not a bound subset topped up with unrecorded values.
- Produces `trace/v1` with `producer.mode = instrumented_endpoint`. `inputs_digest = world_digest(inputs, fixtures)` is REQUIRED.
- `provenance_fidelity`: **`heuristic_attribution` by default** — the labels are self-reported by the caller. `explicit_flow_tracked` is granted ONLY when the operator constructs the gateway as an attested `trusted_runtime` (and labels are carried); a client's `labels_carried` flag can only *downgrade*, never upgrade. It is NOT granted merely because the SDK carried labels.
- Roadmap for a real enforcement boundary against an untrusted caller: a trusted runtime that mints labels from the tool manifest / observed execution graph, and/or a signed per-event envelope; optionally a genuine server-side dispatch route so the gateway executes the tool itself.

## Black-box endpoint (evaluation-only)

Plain request/response: task in, final answer out. No tool visibility, no provenance, no mid-run gating.

```
POST /runs   { task }  →  { output }
```

- Produces NO conformant trace (cannot emit lineage). Governance is impossible.
- The UI offers ONLY output scoring, and "compare" here means **behavioral configurations**, never Axor gate on/off. EvidenceCase is unavailable (or degraded to input/output only).
- Labeled explicitly "evaluation-only — not governance" everywhere it appears.

## Endpoint safety (both modes)
SSRF protection; private-network endpoints blocked or run via an isolated egress runner; DNS-rebinding guard; endpoint auth (bearer token) + per-run secret; run/event quotas; bounded request bodies; idempotency keys on tool replay.
