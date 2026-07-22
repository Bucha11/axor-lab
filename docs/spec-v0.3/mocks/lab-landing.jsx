import { useState } from "react";
import { Compass, RefreshCw, Upload, Search, GitFork, BadgeCheck, CircleDot, ChevronRight, ShieldAlert } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// provenance status — the review's point: a verified run must not look like an uploaded JSON
const STATUS = {
  lab:       { label: "Lab-executed", color: C.green, icon: BadgeCheck, note: "ran on Lab infra" },
  reproduced:{ label: "independently reproduced", color: C.steel, icon: BadgeCheck, note: "re-run by a third party" },
  self:      { label: "self-reported", color: C.amber, icon: CircleDot, note: "local run, bundle uploaded — integrity-hashed, not independently run" },
};

const CATALOG = [
  { id: "i18", title: "Support agent issued an unauthorized refund from a customer email", author: "acme-support", status: "lab", metric: "refund blocked · policy pinned", repro: 1, kind: "incident" },
  { id: "i22", title: "Coding agent deleted files after reading a poisoned README", author: "devtools-co", status: "lab", metric: "rm gated · regression added", repro: 0, kind: "incident" },
  { id: "i27", title: "Sales agent followed an injected note in a CRM record", author: "pipeline-io", status: "self", metric: "exfil→0 under governance", repro: 0, kind: "incident" },
  { id: "e042", title: "Carried-taint contains a compromised federation member", author: "smu-axor", status: "lab", metric: "containment 0.83→0.17", repro: 4, kind: "game" },
  { id: "e051", title: "AgentDojo banking under governance (o4-mini)", author: "smu-axor", status: "lab", metric: "cost −17±7pp · ASR→0", repro: 7, kind: "benchmark" },
  { id: "e063", title: "Prompt-infection propagation, governed vs not", author: "ext-lab-mit", status: "reproduced", metric: "spread 0.71→0.12", repro: 2, kind: "game" },
];

export default function App() {
  const [q, setQ] = useState("");
  const list = CATALOG.filter((c) => (c.title + c.author).toLowerCase().includes(q.toLowerCase()));

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "clamp(14px,4vw,32px) clamp(10px,3vw,20px)" }}>
      <style>{`
        .wrapline{display:flex;align-items:center;flex-wrap:wrap;gap:8px;row-gap:6px;}
        .entrygrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;}
      `}</style>
      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <div className="wrapline mb-8" style={{ gap: 10, justifyContent: "space-between" }}>
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700 }}>AXOR<span style={{ color: C.violet }}> LAB</span></span>
          <div className="wrapline" style={{ fontFamily: MONO, fontSize: 11, gap: 16, color: C.dim }}>
            <span style={{ color: C.text }}>explore</span><span style={{ cursor: "pointer" }}>reproduce</span><span style={{ cursor: "pointer" }}>create</span><span style={{ cursor: "pointer" }}>docs</span>
            <span style={{ color: C.mut, cursor: "pointer" }}>Control Plane ↗</span>
          </div>
        </div>

        <h1 style={{ fontSize: 27, fontWeight: 700, lineHeight: 1.2, margin: "0 0 8px", maxWidth: 540 }}>
          A reproducible lab for agent governance.
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.mut, marginBottom: 28, maxWidth: 560, lineHeight: 1.6 }}>
          Run experiments on LLM agents under Axor governance — or reproduce someone else's, bit-for-bit on the
          governance layer. Free for research. Standalone; no Axor deployment needed.
        </div>

        {/* three entry points — the review wanted a lower first step than 'bring your agent' */}
        <div className="entrygrid mb-10">
          {[
            [Compass, "Explore experiments", "browse published runs, see the mechanism, fork one", C.violet, "lowest barrier"],
            [RefreshCw, "Reproduce a run", "drop a bundle → re-run its governance verdicts exactly", C.green, "no agent needed"],
            [Upload, "Bring your agent", "code · endpoint · traces → run your own", C.steel, "your setup"],
            [ShieldAlert, "Investigate a production incident", "import the trace → reproduce it → test a fix → pin a regression", C.red, "for teams"],
          ].map(([Icon, t, d, col, tag]) => (
            <div key={t} style={{ cursor: "pointer", padding: 16, borderRadius: 10, background: C.panel, border: `1px solid ${C.line}` }}>
              <Icon size={18} color={col} />
              <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, fontWeight: 600, marginTop: 10 }}>{t}</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 3, lineHeight: 1.5 }}>{d}</div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: col, marginTop: 8 }}>{tag}</div>
            </div>
          ))}
        </div>

        {/* catalog */}
        <div className="wrapline mb-3" style={{ justifyContent: "space-between" }}>
          <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>Published experiments</span>
          <div className="flex items-center gap-2" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6, padding: "5px 10px" }}>
            <Search size={12} color={C.dim} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search"
              style={{ background: "none", border: "none", color: C.text, fontFamily: MONO, fontSize: 11, outline: "none", width: 120 }} />
          </div>
        </div>

        <div className="flex flex-col gap-2">
          {list.map((c) => {
            const st = STATUS[c.status]; const StIcon = st.icon;
            return (
              <div key={c.id} className="wrapline" style={{ justifyContent: "space-between", background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: "12px 14px", cursor: "pointer" }}>
                <div style={{ flex: "1 1 260px", minWidth: 220 }}>
                  <div className="wrapline" style={{ gap: 8 }}>
                    <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>{c.title}</span>
                    <span style={{ fontFamily: MONO, fontSize: 8.5, color: c.kind === "incident" ? C.red : C.dim, border: `1px solid ${c.kind === "incident" ? C.red : C.line}`, borderRadius: 3, padding: "1px 5px" }}>{c.kind}</span>
                  </div>
                  <div className="wrapline" style={{ gap: 10, marginTop: 4 }}>
                    <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>{c.metric}</span>
                    <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>@{c.author}</span>
                  </div>
                </div>
                <div className="wrapline" style={{ gap: 12 }}>
                  <span className="flex items-center gap-1.5" title={st.note} style={{ fontFamily: MONO, fontSize: 9.5, color: st.color }}>
                    <StIcon size={11} /> {st.label}
                  </span>
                  {c.repro > 0 && (
                    <span className="flex items-center gap-1" style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                      <GitFork size={10} /> {c.repro}
                    </span>
                  )}
                  <ChevronRight size={14} color={C.dim} />
                </div>
              </div>
            );
          })}
        </div>

        {/* status legend — make the distinction explicit, per review */}
        <div className="wrapline mt-4" style={{ gap: 14 }}>
          {Object.values(STATUS).map((st) => {
            const StIcon = st.icon;
            return (
              <span key={st.label} className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 9, color: st.color }}>
                <StIcon size={10} /> {st.label} <span style={{ color: C.dim }}>— {st.note}</span>
              </span>
            );
          })}
        </div>

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 20, lineHeight: 1.7 }}>
          The catalog never equates a verified run with an uploaded JSON: <b style={{ color: C.green }}>Lab-executed</b> ran here,
          <b style={{ color: C.steel }}> independently reproduced</b> was re-run by others, <b style={{ color: C.amber }}>self-reported</b> is
          integrity-hashed but not independently verified. Reproductions are counted, not assumed.
        </div>
      </div>
    </div>
  );
}
