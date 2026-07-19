# Axor Lab — Endpoint Protocol (v1, post-MVP)

Endpoint ingest splits into two modes that the UI must present separately, because governance is only possible in one.

## Instrumented endpoint (governance-capable)

The agent participates: it emits tool-call events and routes tool execution through the Lab gateway (or an MCP proxy), so Lab sees value lineage and can gate.

```
POST /runs                      → { run_id }
SSE  /runs/{run_id}/events      ← tool_call_intent, tool_result, message_send/recv (carry value ids + labels)
POST /runs/{run_id}/tools/{call_id}/result   (gateway dispatches the tool, returns result; provenance minted here)
```

- Produces `trace/v1` with `producer.mode = instrumented_endpoint`, `provenance_fidelity = explicit_flow_tracked` (if the SDK carries labels) or `heuristic_attribution` (if only events, no labels — flagged).
- Governance, EvidenceCase, compare-mode: all available.

## Black-box endpoint (evaluation-only)

Plain request/response: task in, final answer out. No tool visibility, no provenance, no mid-run gating.

```
POST /runs   { task }  →  { output }
```

- Produces NO conformant trace (cannot emit lineage). Governance is impossible.
- The UI offers ONLY output scoring, and "compare" here means **behavioral configurations**, never Axor gate on/off. EvidenceCase is unavailable (or degraded to input/output only).
- Labeled explicitly "evaluation-only — not governance" everywhere it appears.

## Endpoint safety (both modes)
SSRF protection; private-network endpoints blocked or run via an isolated egress runner; DNS-rebinding guard; endpoint auth + secret storage; outbound allowlist; idempotency keys on tool replay.
