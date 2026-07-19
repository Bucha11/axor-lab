import { useState } from "react";
import { Check, X, Loader, AlertTriangle, RotateCcw, Ban, ChevronDown, ChevronRight } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// pipelines branch by backend (lifecycle.md)
const PIPELINES = {
  local: [["validating","schema, bindings, predicates, $inputs"],["waiting_for_runner","local runner picks it up"],["running_local","agent on simulated tools, on your machine"],["uploading_artifacts","traces + verdicts uploaded"],["analyzing","aggregate per statistics.md"],["completed","results + EvidenceCases ready"]],
  lab_template: [["validating","schema, bindings, predicates"],["queued","waiting for a runner"],["provisioning","sandbox up, deps installed, key checked"],["running","model calls across trials"],["analyzing","verdicts replayed, CIs computed"],["completed","results ready"]],
  trace_replay: [["validating","schema + trace integrity"],["replaying","governance verdicts over frozen traces"],["analyzing","aggregate"],["completed","results ready"]],
};
const PIPELINE_LEGACY = [
  ["validating", "schema, tool bindings, predicates, $inputs resolve"],
  ["ready", "estimate shown, awaiting go"],
  ["queued", "waiting for a runner"],
  ["provisioning", "sandbox up, deps installed, key checked"],
  ["running", "model calls across conditions × repeats × scenarios"],
  ["analyzing", "verdicts replayed, CIs computed"],
  ["completed", "results + EvidenceCases ready"],
];

// realistic failure modes, each at a specific stage
const FAILURES = [
  { at: "validating", label: "predicate references unknown tool", detail: "violation.tool 'send_wire' is not a declared tool", fix: "fix scenario", retry: false },
  { at: "provisioning", label: "missing model key", detail: "BYOK: no key for provider openai", fix: "add key", retry: true },
  { at: "running", label: "model timeout (rate limit)", detail: "provider 429 on 12/240 trials — partial results kept", fix: "retry failed trials", retry: true, partial: true },
  { at: "running", label: "agent crashed", detail: "uncaught exception in wrapped agent on scenario 3", fix: "view logs", retry: true },
  { at: "analyzing", label: "malformed trace", detail: "3 traces missing tool-call intents — excluded, flagged", fix: "inspect", retry: false, partial: true },
];

const stColor = (s, cur, done, failed) => failed === s ? C.red : done ? C.green : cur === s ? C.steel : C.dim;

export default function App() {
  const [backend, setBackend] = useState("local");
  const PIPELINE = PIPELINES[backend];
  const [idx, setIdx] = useState(2);
  const [failure, setFailure] = useState(null); // null | index into FAILURES
  const [openEst, setOpenEst] = useState(true);

  const cur = PIPELINE[idx][0];
  const fail = failure != null ? FAILURES[failure] : null;
  const failedStage = fail ? fail.at : null;

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "clamp(14px,4vw,28px) clamp(10px,3vw,20px)" }}>
      <style>{`.wrapline{display:flex;align-items:center;flex-wrap:wrap;gap:8px;row-gap:6px;} @keyframes spin{to{transform:rotate(360deg)}} .spin{animation:spin 1.2s linear infinite;}`}</style>
      <div style={{ maxWidth: 620, margin: "0 auto" }}>
        <div className="wrapline mb-6" style={{ gap: 10, justifyContent: "space-between" }}>
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700 }}>AXOR<span style={{ color: C.violet }}> LAB</span></span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>run r_7c31 · banking-suite</span>
        </div>

        <h1 style={{ fontSize: 19, fontWeight: 650, margin: "0 0 4px" }}>
          {fail ? "Run interrupted." : cur === "completed" ? "Run complete." : "Running your experiment."}
        </h1>
        <div className="wrapline" style={{ marginBottom: 14, gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>240 trials · 2×30×4 · BYOK</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>backend</span>
          {["local","lab_template","trace_replay"].map((b) => (
            <button key={b} onClick={() => { setBackend(b); setIdx(1); setFailure(null); }} style={{ background: backend === b ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${backend === b ? C.steel : C.line}`, borderRadius: 3, color: backend === b ? C.steel : C.dim, fontFamily: MONO, fontSize: 9, padding: "2px 7px", cursor: "pointer" }}>{b}</button>
          ))}
        </div>

        {/* pre-run estimate — validation screen content, per review */}
        {idx <= 1 && !fail && (
          <div className="mb-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            <button onClick={() => setOpenEst(!openEst)} className="flex items-center gap-2 px-4 py-3 w-full" style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer" }}>
              {openEst ? <ChevronDown size={13} /> : <ChevronRight size={13} />} before you run — estimate & checks
            </button>
            {openEst && (
              <div className="px-4 pb-3" style={{ paddingLeft: 34, fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.8 }}>
                <div>trials: <span style={{ color: C.text }}>240</span> · est. tokens ~1.2M · est. cost <span style={{ color: C.text }}>~$3.80</span> (your key)</div>
                <div>tool bindings: <span style={{ color: C.green }}>4/4 resolved</span> · predicates: <span style={{ color: C.green }}>type-check ok</span></div>
                <div>privacy: code runs <span style={{ color: C.text }}>locally</span> · only observations leave, never raw bodies</div>
              </div>
            )}
          </div>
        )}

        {/* the pipeline */}
        <div style={{ background: C.panel, border: `1px solid ${fail ? C.red : C.line}`, borderRadius: 10, padding: 16 }}>
          {PIPELINE.map(([stage, note], i) => {
            const done = i < idx || (cur === "completed" && i <= idx && !fail);
            const isCur = i === idx && !fail;
            const isFail = failedStage === stage;
            const col = isFail ? C.red : done ? C.green : isCur ? C.steel : C.dim;
            return (
              <div key={stage} className="wrapline" style={{ gap: 10, padding: "5px 0", opacity: i > idx && !isFail ? 0.4 : 1 }}>
                <span style={{ width: 18, flexShrink: 0, display: "flex", justifyContent: "center" }}>
                  {isFail ? <X size={13} color={C.red} /> : done ? <Check size={13} color={C.green} /> : isCur ? <Loader size={13} color={C.steel} className="spin" /> : <span style={{ width: 6, height: 6, borderRadius: 3, background: C.dim }} />}
                </span>
                <span style={{ fontFamily: MONO, fontSize: 11.5, color: isFail ? C.red : done ? C.text : isCur ? C.text : C.dim, fontWeight: isCur || isFail ? 600 : 400, minWidth: 96 }}>{stage}</span>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{isFail ? fail.detail : note}</span>
              </div>
            );
          })}
          {/* running progress bar */}
          {cur === "running" && !fail && (
            <div className="mt-2" style={{ paddingLeft: 28 }}>
              <div style={{ height: 5, background: C.panel2, borderRadius: 3, overflow: "hidden", maxWidth: 320 }}>
                <div style={{ width: "62%", height: "100%", background: C.steel }} />
              </div>
              <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 4 }}>149 / 240 trials · ~2m left</div>
            </div>
          )}
        </div>

        {/* failure panel */}
        {fail && (
          <div className="mt-3 p-4" style={{ background: "rgba(229,72,77,0.05)", border: `1px solid ${C.red}`, borderRadius: 10 }}>
            <div className="flex items-center gap-2 mb-1">
              <AlertTriangle size={14} color={C.red} />
              <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>{fail.label}</span>
              <span style={{ fontFamily: MONO, fontSize: 9, color: C.red, border: `1px solid ${C.red}`, borderRadius: 3, padding: "1px 6px" }}>at {fail.at}</span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 12, paddingLeft: 22 }}>{fail.detail}</div>
            {fail.partial && (
              <div className="mb-3 p-2" style={{ background: C.panel2, borderRadius: 5, fontFamily: MONO, fontSize: 10, color: C.amber, marginLeft: 22 }}>
                partial results kept — but validity depends on randomness: if failures cluster (e.g. on the hardest scenario) the estimate is biased. The result shows denominator + missing count and flags non-random missingness, never silently computes over survivors.
              </div>
            )}
            <div className="wrapline" style={{ gap: 8, paddingLeft: 22 }}>
              <button style={{ display: "flex", alignItems: "center", gap: 5, background: C.violet, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: "6px 12px", cursor: "pointer" }}>{fail.fix}</button>
              {fail.retry && <button style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "6px 12px", cursor: "pointer" }}><RotateCcw size={11} /> retry {fail.partial ? "failed trials" : ""}</button>}
              <button onClick={() => setFailure(null)} style={{ background: "none", border: "none", color: C.dim, fontFamily: MONO, fontSize: 11, cursor: "pointer" }}>dismiss</button>
            </div>
          </div>
        )}

        {/* controls */}
        {!fail && cur !== "completed" && (
          <div className="wrapline mt-3" style={{ gap: 8 }}>
            <button style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "6px 12px", cursor: "pointer" }}><Ban size={11} /> cancel run</button>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>cancel keeps completed trials — nothing already run is lost</span>
          </div>
        )}
        {cur === "completed" && !fail && (
          <div className="wrapline mt-3" style={{ gap: 8 }}>
            <button style={{ display: "flex", alignItems: "center", gap: 6, background: C.violet, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12, fontWeight: 700, padding: "8px 15px", cursor: "pointer" }}>View results <ChevronRight size={13} /></button>
          </div>
        )}

        {/* demo: jump to states */}
        <div className="wrapline mt-6" style={{ gap: 6, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
          <span style={{ fontFamily: MONO, fontSize: 9, color: C.dim }}>preview state:</span>
          {PIPELINE.map(([s], i) => (
            <button key={s} onClick={() => { setIdx(i); setFailure(null); }} style={{ background: idx === i && !fail ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${idx === i && !fail ? C.steel : C.line}`, borderRadius: 3, color: idx === i && !fail ? C.steel : C.dim, fontFamily: MONO, fontSize: 8.5, padding: "2px 6px", cursor: "pointer" }}>{s}</button>
          ))}
          {FAILURES.map((f, i) => (
            <button key={i} onClick={() => { setFailure(i); const j = PIPELINE.findIndex((p) => p[0] === f.at); setIdx(j >= 0 ? j : 0); }} style={{ background: failure === i ? "rgba(229,72,77,0.12)" : "none", border: `1px solid ${failure === i ? C.red : C.line}`, borderRadius: 3, color: failure === i ? C.red : C.dim, fontFamily: MONO, fontSize: 8.5, padding: "2px 6px", cursor: "pointer" }}>✕ {f.label.split(" ").slice(0, 2).join(" ")}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
