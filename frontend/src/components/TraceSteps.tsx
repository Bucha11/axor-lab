// One trial's trace, step by step — what the agent read, the intent it formed,
// the gated call and the verdict (the lab-evidencecase mockup's TRACE panel).
// Rendered straight from recorded trace events; nothing is invented.
import { AlertTriangle, CornerDownRight, GitCommit, Shield } from "lucide-react";
import { C, MONO } from "../theme";
import { Trace, TraceEvent } from "../api";

function describe(e: TraceEvent): { title: string; detail: string; color: string; Icon: typeof Shield } {
  if (e.type === "tool_result") {
    const produced = e.produces_value_ids?.length ?? 0;
    return {
      title: `${e.tool ?? "tool"}() → result`,
      detail: produced
        ? `produced ${produced} value${produced === 1 ? "" : "s"} — external, untrusted fields minted into the ledger`
        : "tool result recorded",
      color: produced ? C.amber : C.mut,
      Icon: GitCommit,
    };
  }
  if (e.type === "tool_call_intent") {
    const args = e.arg_bindings ? Object.keys(e.arg_bindings).join(", ") : "";
    return {
      title: `agent forms intent — ${e.tool ?? "tool"}(${args})`,
      detail: e.call_id ? `call_id ${e.call_id}` : "tool call intent",
      color: C.amber,
      Icon: CornerDownRight,
    };
  }
  if (e.type === "gate_decision") {
    const verdict = e.decision?.verdict ?? "?";
    return {
      title: `gate: ${verdict}`,
      detail: [
        e.decision?.gate ? `gate ${e.decision.gate}` : "",
        e.decision?.reason ? String(e.decision.reason) : "",
      ].filter(Boolean).join(" · ") || "gate decision",
      color: verdict === "DENY" ? C.green : C.red,
      Icon: Shield,
    };
  }
  return {
    title: e.type,
    detail: e.tool ? `tool ${e.tool}` : "",
    color: C.mut,
    Icon: AlertTriangle,
  };
}

export function verdictOf(trace: Trace): { verdict: string; decision?: TraceEvent["decision"] } {
  const gate = trace.events.find((e) => e.type === "gate_decision");
  return { verdict: gate?.decision?.verdict ?? "no gate decision recorded", decision: gate?.decision };
}

export default function TraceSteps({ trace }: { trace: Trace }) {
  const { verdict, decision } = verdictOf(trace);
  const denied = verdict === "DENY";
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", padding: "10px 14px", borderBottom: `1px solid ${C.line}` }}>
        TRACE — what actually happened · {trace.trace_id}
      </div>
      {trace.events.map((e, i) => {
        const { title, detail, color, Icon } = describe(e);
        return (
          <div key={`${e.seq}-${i}`} className="px-4 py-2.5" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
            <div className="wrapline" style={{ gap: 8 }}>
              <Icon size={13} color={color} style={{ flexShrink: 0 }} />
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>{title}</span>
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: C.dim }}>seq {e.seq}</span>
            </div>
            {detail && (
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 3, paddingLeft: 21, lineHeight: 1.5 }}>
                {detail}
              </div>
            )}
          </div>
        );
      })}
      <div className="px-4 py-3" style={{ borderTop: `1px solid ${C.line}`, background: denied ? "rgba(70,167,88,0.05)" : "rgba(229,72,77,0.05)" }}>
        <div className="wrapline" style={{ justifyContent: "space-between" }}>
          <div className="flex items-center gap-2">
            {denied ? <Shield size={14} color={C.green} /> : <AlertTriangle size={14} color={C.red} />}
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>
              {denied ? "DENY — the call was gated" : verdict === "ALLOW" ? "ALLOW — the emitted call executed (simulated)" : verdict}
            </span>
          </div>
          <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 700, color: denied ? C.green : C.red, border: `1px solid ${denied ? C.green : C.red}`, borderRadius: 3, padding: "2px 7px" }}>
            observed
          </span>
        </div>
        {decision?.reason != null && (
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 4, paddingLeft: 22 }}>
            {String(decision.reason)}
          </div>
        )}
      </div>
    </div>
  );
}
