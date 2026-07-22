import { useState } from "react";
import { FileStack, Check, ArrowRight, Terminal, Lock, ChevronRight, AlertTriangle, Sparkles, Radio } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const MODES = [
  {
    id: "demo", icon: Sparkles, label: "Try a preset — no agent",
    sub: "a curated scenario on a Lab-hosted model",
    repro: "hosted", reproColor: C.steel,
    gives: "open → Run → Results → EvidenceCase in 30s, before connecting anything",
    detects: "nothing to bring; pick a preset and run",
    privacy: "runs entirely on Lab; you upload nothing",
  },
  {
    id: "connected_runtime", icon: Radio, label: "Connect a runtime",
    sub: "the same Axor adapter that serves Control Plane — connect once",
    repro: "runtime", reproColor: C.steel,
    gives: "real governance · provenance · EvidenceCase — the runtime runs your agent and pushes traces",
    detects: "Connect runtime → scoped key for the shared adapter, or select an already-connected one",
    privacy: "the runtime executes locally and sends traces outward; Lab never connects to or proxies your agent",
  },
  {
    id: "trace_import", icon: FileStack, label: "Import traces / an incident",
    sub: "a production incident or someone's published run",
    repro: "replay", reproColor: C.green,
    gives: "reproduce governance verdicts bit-identical · turn an incident into a regression",
    detects: "drop an axor-core trace bundle — nothing to wrap",
    privacy: "observations only, never raw bodies",
  },
  {
    id: "offline_runner", icon: Terminal, label: "Offline runner (CI / air-gapped)",
    sub: "private code or CI, on your infra",
    repro: "offline", reproColor: C.violet,
    gives: "run beside the agent in CI; upload only the trace bundle",
    detects: "uvx axor lab run experiment.axl — nothing leaves but the bundle",
    privacy: "code and execution stay on your machine; only the signed bundle uploads",
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
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.05em", marginBottom: 8, lineHeight: 1.6 }}>
              HOW YOUR AGENT REACHES LAB — Lab reads the shared Axor trace fabric; it never connects to or proxies your agent. Connect a runtime once; Control Plane sees the same one.
            </div>
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
                  {mode === "demo" ? "Run preset" : mode === "connected_runtime" ? "Connect runtime" : mode === "trace_import" ? "Import traces" : "Set up runner"} <ArrowRight size={13} />
                </button>
                {mode === "connected_runtime" && (
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
              {mode === "connected_runtime" && (
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 8 }}>
                  4 tools detected · classify sinks in the next step, then your code is untouched + 2 files added
                </div>
              )}
            </div>

            {/* run mode */}
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em", margin: "18px 0 8px" }}>HOW TO RUN</div>

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
          Lab is the experiment & evidence layer over Axor runtime traces — it reads the <b style={{ color: C.steel }}>shared trace fabric</b>,
          it does not connect to, execute, or proxy your agent. Connect a runtime once (the same adapter Control Plane uses);
          both modules see it. Wiring in <span style={{ color: C.mut }}>ui-backend-contract.md</span>. How you bring your agent sets the reproducibility you get: <b style={{ color: C.steel }}>code/endpoint → live</b> (stochastic,
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
