import { useState } from "react";
import { Upload, Globe, FileStack, Check, ArrowRight, Terminal, Beaker, Lock, ChevronRight, AlertTriangle } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const MODES = [
  {
    id: "code", icon: Upload, label: "Upload agent code",
    sub: "Python · LangChain · MCP project",
    repro: "live", reproColor: C.steel,
    gives: "full wrap · real runs · every experiment type",
    detects: "tools auto-detected → you classify sinks → wrapped",
    privacy: "runs in the lab sandbox — or locally: uvx axor lab wrap ./agent (code never leaves your machine)",
  },
  {
    id: "endpoint_instrumented", icon: Globe, label: "Endpoint — instrumented",
    sub: "agent emits tool events + routes tools via the Lab gateway / MCP proxy",
    repro: "live (proxy)", reproColor: C.steel,
    gives: "real governance · provenance · EvidenceCase — because Lab sees the tool calls",
    detects: "connect via Axor SDK or MCP proxy; declare tool manifests",
    privacy: "Lab sees events + tool I/O, never your source or weights",
  },
  {
    id: "endpoint_blackbox", icon: Globe, label: "Endpoint — black-box",
    sub: "plain HTTP task-in / result-out, no instrumentation",
    repro: "evaluation-only", reproColor: C.amber,
    gives: "output scoring only — NOT governance (Lab can't see tool calls or provenance)",
    detects: "just a URL; nothing to instrument",
    privacy: "Lab sees task in / final answer out",
  },
  {
    id: "traces", icon: FileStack, label: "Upload recorded traces",
    sub: "no agent at all · governance over frozen behavior",
    repro: "replay", reproColor: C.green,
    gives: "bit-identical · reproduce a published run · no model calls",
    detects: "traces already carry the tool-call intents — nothing to wrap",
    privacy: "traces are observations only, never raw bodies (§8.3)",
  },
];

export default function App() {
  const [mode, setMode] = useState(null);
  const [stage, setStage] = useState("pick"); // pick | ingesting | ready
  const [runMode, setRunMode] = useState("ungoverned"); // ungoverned | governed | compare
  const m = MODES.find((x) => x.id === mode);

  const proceed = () => { setStage("ingesting"); setTimeout(() => setStage("ready"), 1100); };

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "clamp(14px,4vw,32px) clamp(10px,3vw,20px)" }}>
      <style>{`.wrapline{display:flex;align-items:center;flex-wrap:wrap;gap:8px;row-gap:6px;}`}</style>
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        {/* standalone brand — own domain, not a CP tab */}
        <div className="wrapline mb-6" style={{ gap: 10, justifyContent: "space-between" }}>
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700 }}>AXOR<span style={{ color: C.violet }}> LAB</span></span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>lab.useaxor.net · free for research</span>
        </div>

        <h1 style={{ fontSize: 23, fontWeight: 680, lineHeight: 1.25, margin: "0 0 6px" }}>
          Bring your agent. Run experiments on it under governance.
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 24 }}>
          No Axor deployment needed — this is standalone. Pick how your agent gets here; it decides which
          reproducibility layer you get.
        </div>

        {stage === "pick" && (
          <>
            <div className="flex flex-col gap-3">
              {MODES.map((x) => {
                const Icon = x.icon;
                const active = mode === x.id;
                return (
                  <div key={x.id} onClick={() => setMode(x.id)}
                    style={{ cursor: "pointer", padding: 16, borderRadius: 10, background: active ? "rgba(155,140,204,0.06)" : C.panel, border: `1px solid ${active ? C.violet : C.line}` }}>
                    <div className="wrapline" style={{ justifyContent: "space-between" }}>
                      <div className="flex items-center gap-2.5">
                        <Icon size={16} color={active ? C.violet : C.mut} />
                        <div>
                          <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, fontWeight: 600 }}>{x.label}</div>
                          <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 1 }}>{x.sub}</div>
                        </div>
                      </div>
                      <span style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 700, color: x.reproColor, border: `1px solid ${x.reproColor}`, borderRadius: 3, padding: "2px 7px" }}>{x.repro}</span>
                    </div>
                    {active && (
                      <div className="mt-3 pt-3 flex flex-col gap-1.5" style={{ borderTop: `1px solid ${C.line}` }}>
                        <Row k="gives you" v={x.gives} />
                        <Row k="setup" v={x.detects} />
                        <Row k="privacy" v={x.privacy} icon={Lock} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {mode && (
              <div className="wrapline mt-5" style={{ gap: 10 }}>
                <button onClick={proceed}
                  style={{ display: "flex", alignItems: "center", gap: 7, background: C.violet, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "9px 18px", cursor: "pointer" }}>
                  {mode === "code" ? "Drop code" : mode === "endpoint_instrumented" ? "Add endpoint" : "Upload traces"} <ArrowRight size={13} />
                </button>
                {mode === "code" && (
                  <span className="wrapline" style={{ fontFamily: MONO, fontSize: 10, color: C.dim, gap: 6 }}>
                    <Terminal size={11} /> or local: <span style={{ color: C.mut }}>uvx axor lab wrap ./agent</span>
                  </span>
                )}
              </div>
            )}
          </>
        )}

        {stage === "ingesting" && (
          <div className="p-8 flex flex-col items-center gap-3" style={{ background: C.panel, border: `1px dashed ${C.violet}`, borderRadius: 10 }}>
            <Beaker size={22} color={C.violet} />
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.violet }}>
              {mode === "code" ? "wrapping agent · extracting tool signatures…" : mode === "endpoint_instrumented" ? "handshaking endpoint · declaring tools…" : "indexing traces · validating schema…"}
            </span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>reproducibility: {m.repro}</span>
          </div>
        )}

        {stage === "ready" && (
          <>
            <div className="p-4" style={{ background: C.panel, border: `1px solid ${C.green}`, borderRadius: 10 }}>
              <div className="wrapline" style={{ justifyContent: "space-between" }}>
                <div className="flex items-center gap-2">
                  <Check size={15} color={C.green} />
                  <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>
                    {mode === "code" ? "agent wrapped" : mode === "endpoint_instrumented" ? "endpoint connected" : "traces loaded"}
                  </span>
                </div>
                <span style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 700, color: m.reproColor, border: `1px solid ${m.reproColor}`, borderRadius: 3, padding: "2px 7px" }}>{m.repro}</span>
              </div>
              {mode === "code" && (
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 8 }}>
                  4 tools detected · classify sinks in the next step, then your code is untouched + 2 files added
                </div>
              )}
              {mode === "endpoint_blackbox" && (
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.amber, marginTop: 8 }}>
                  evaluation-only — no tool visibility, so no governance and no EvidenceCase
                </div>
              )}
            </div>

            {/* run mode — black-box can't be governed; guard it */}
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em", margin: "18px 0 8px" }}>HOW TO RUN</div>
            {mode === "endpoint_blackbox" && (
              <div className="mb-3 p-3 flex items-start gap-2" style={{ background: "rgba(242,163,60,0.06)", border: `1px solid ${C.amber}`, borderRadius: 8 }}>
                <AlertTriangle size={12} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
                <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.5 }}>
                  Black-box endpoints support <b style={{ color: C.text }}>output scoring only</b>. Governance and EvidenceCase are unavailable — Lab can't see tool calls or provenance from outside. "Compare" here means behavioral configurations, never Axor gate on/off.
                </span>
              </div>
            )}
            <div className="flex flex-col gap-2 mb-4">
              {[
                ["ungoverned", "just run my agent — observe & record, enforce nothing", C.steel, "see what your agent actually does on your scenarios. The proxy watches and logs (that's how EvidenceCase works) but never blocks."],
                ["governed", "run under axor-core — gates enforce", C.green, "the same run with enforcement on."],
                ["compare", "both, side by side — the governance delta", C.violet, "ungoverned vs governed on identical scenarios; the paired Δ, with CI over repeats."],
              ].map(([id, label, col, note]) => {
                const sel = runMode === id;
                return (
                  <div key={id} onClick={() => setRunMode(id)} className="px-4 py-3" style={{ background: sel ? "rgba(155,140,204,0.06)" : C.panel, border: `1px solid ${sel ? col : C.line}`, borderRadius: 8, cursor: "pointer" }}>
                    <div className="wrapline" style={{ justifyContent: "space-between" }}>
                      <div className="flex items-center gap-2">
                        <span style={{ width: 12, height: 12, borderRadius: 6, border: `1px solid ${sel ? col : C.dim}`, background: sel ? col : "transparent", flexShrink: 0 }} />
                        <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>{label}</span>
                      </div>
                      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 700, color: col, border: `1px solid ${col}`, borderRadius: 3, padding: "2px 7px" }}>{id}</span>
                    </div>
                    <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 3, lineHeight: 1.5, paddingLeft: 20 }}>{note}</div>
                  </div>
                );
              })}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginBottom: 4, lineHeight: 1.6 }}>
              ungoverned ≠ unobserved — the proxy is observe-only there: it records everything, enforces nothing.
              It's your agent, run your way; governance is a comparison you opt into, not a tax on every run.
            </div>

            {/* next: author a scenario / import a bench */}
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em", margin: "10px 0 8px" }}>NOW RUN IT ON</div>
            <div className="flex flex-col gap-2">
              {[
                ["Write a scenario", "author the world + breach criterion — data, not code"],
                ["Import a benchmark", "AgentDojo & others as ready scenario sets"],
                ["Reproduce a published run", "someone's bundle: config + frozen traces", mode !== "traces"],
              ].map(([t, d, dim]) => (
                <div key={t} className="wrapline px-4 py-3" style={{ justifyContent: "space-between", background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, cursor: "pointer", opacity: dim ? 0.5 : 1 }}>
                  <div>
                    <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.text }}>{t}</div>
                    <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 1 }}>{d}</div>
                  </div>
                  <ChevronRight size={14} color={C.dim} />
                </div>
              ))}
            </div>
          </>
        )}

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 20, lineHeight: 1.7 }}>
          Lab is its own thing at <span style={{ color: C.mut }}>lab.useaxor.net</span> — you don't need the Control Plane
          product to use it. How you bring your agent sets the reproducibility you get: <b style={{ color: C.steel }}>code/endpoint → live</b> (stochastic,
          reported with CI), <b style={{ color: C.green }}>traces → replay</b> (governance verdicts, bit-identical). Code never
          has to leave your machine — the local path is first-class.
        </div>
      </div>
    </div>
  );
}

function Row({ k, v, icon: Icon }) {
  return (
    <div className="flex items-start gap-2">
      {Icon ? <Icon size={11} color={C.dim} style={{ marginTop: 2, flexShrink: 0 }} /> : <span style={{ width: 44, flexShrink: 0, fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{k}</span>}
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.5 }}>
        {Icon && <span style={{ color: C.dim }}>{k}: </span>}{v}
      </span>
    </div>
  );
}
