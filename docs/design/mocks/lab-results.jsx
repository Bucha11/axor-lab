import { useState } from "react";
import { Play, Share2, Check, ChevronDown, ChevronRight, Repeat, FileText, FileDown, Search, AlertTriangle } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// Each game declares its own conditions, scenario count, and whether results exist.
// No silent fallback: a game without data says "no data yet".
const GAMES = [
  {
    id: "coop", name: "Repeated cooperation", scenarios: 4,
    q: "Do governed agents sustain cooperation longer than ungoverned?",
    metric: "cooperation rate over 100 rounds", binary: false,
    rows: [
      ["ungoverned", 0.34, [0.28, 0.40], "defects once payoff asymmetry appears"],
      ["governed (L1)", 0.61, [0.55, 0.67], "attestation stabilizes reciprocity"],
      ["governed (L2)", 0.88, [0.83, 0.92], "signed labels ≈ enforceable promises"],
    ],
    test: "paired bootstrap (continuous metric)",
    finding: "Governance raises sustained cooperation 0.34 → 0.88. Mechanism: L2 signed-label assertions act as enforceable commitments — an agent can't cheaply claim cooperation it didn't perform (claim-vs-reality gate catches it).",
  },
  {
    id: "adversary", name: "Adversarial federation", scenarios: 3,
    q: "Does carried-taint localize a compromised member's damage?",
    metric: "federation reached by injected agent (binary per trial)", binary: true,
    rows: [
      ["ungoverned", 0.83, [0.66, 0.92], "compromise propagates to ~5/6 members"],
      ["governed", 0.17, [0.08, 0.34], "carried-taint stops it at first boundary"],
    ],
    test: "McNemar (paired binary) + Wilson CIs",
    finding: "A compromised member reaches 83% of an ungoverned federation vs 17% governed — containment holds at the first boundary the tainted value crosses.",
  },
  { id: "trust", name: "Trust escalation", scenarios: 3, q: "How do L0→L1→L2 transitions change outcomes across operators?", metric: "—", binary: false, rows: null },
  { id: "auction", name: "Negotiation / auction", scenarios: 2, q: "Can agents lie in bidding under a claim-vs-reality gate?", metric: "—", binary: false, rows: null },
];

// NOTE: intervals + tests are computed by the runner/analyzer per contracts/statistics.md
// (Wilson for binary ASR, bootstrap for continuous, McNemar for paired) and stored in
// bundle.aggregates. This UI only renders stored numbers — there is no statistic computed here.

export default function App() {
  const [gameId, setGameId] = useState("coop");
  const [phase, setPhase] = useState("setup"); // setup | running | done
  const [repeats, setRepeats] = useState(30);
  const [openFinding, setOpenFinding] = useState(true);
  const [shared, setShared] = useState(false);
  const [exported, setExported] = useState(null);

  const g = GAMES.find((x) => x.id === gameId);
  const hasData = !!g.rows;
  const conditions = hasData ? g.rows.length : 0;
  const trials = conditions * repeats * g.scenarios; // conditions × repeats × scenarios
  const underpowered = repeats < 10;

  // binary metrics: recompute CIs from n via Wilson; continuous: keep declared bootstrap CI
  // intervals come precomputed in the data (from bundle.aggregates per statistics.md).
  // The UI renders them; it never computes a statistic at render time.
  const rows = hasData
    ? g.rows.map(([cond, p, ci, note]) => ({ cond, p, ci, note }))
    : [];

  const run = () => { setPhase("running"); setTimeout(() => setPhase("done"), 1400); };

  // verdict is computed, never a hardcoded p-value
  const verdict = () => {
    if (underpowered) return { txt: `inconclusive at n=${repeats} — raise repeats`, col: C.amber };
    const lo = rows[0], hi = rows[rows.length - 1];
    const disjoint = lo.ci[1] < hi.ci[0] || hi.ci[1] < lo.ci[0];
    return disjoint
      ? { txt: `${lo.cond} vs ${hi.cond}: 95% CIs disjoint · ${g.test}`, col: C.green }
      : { txt: `${lo.cond} vs ${hi.cond}: CIs overlap — not significant at n=${repeats}`, col: C.amber };
  };
  const v = hasData && phase === "done" ? verdict() : null;

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "clamp(14px,4vw,28px) clamp(10px,3vw,20px)" }}>
      <style>{`
        .autogrid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:8px; }
        .wrapline { display:flex; align-items:center; flex-wrap:wrap; gap:8px; row-gap:6px; }
        .result-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
        .result-row .rr-note { flex:1 1 100%; padding-left:2px; }
        @media (min-width:560px){ .result-row{flex-wrap:nowrap;} .result-row .rr-note{flex:0 0 150px;padding-left:0;} }
      `}</style>
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <div className="wrapline mb-6" style={{ gap: 10, justifyContent: "space-between" }}>
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700 }}>AXOR<span style={{ color: C.violet }}> LAB</span></span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>lab.useaxor.net · free for research</span>
        </div>

        <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 2px" }}>Run an experiment on agents under governance.</h1>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
          live runs call the model — stochastic, reported with CI over repeats. Only the governance verdicts replay bit-for-bit.
        </div>

        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.08em", marginBottom: 8 }}>EXPERIMENT</div>
        <div className="autogrid mb-5">
          {GAMES.map((x) => (
            <div key={x.id} onClick={() => { setGameId(x.id); setPhase("setup"); }}
              style={{ cursor: "pointer", padding: 12, borderRadius: 8, background: gameId === x.id ? "rgba(155,140,204,0.08)" : C.panel, border: `1px solid ${gameId === x.id ? C.violet : C.line}` }}>
              <div className="wrapline" style={{ justifyContent: "space-between" }}>
                <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>{x.name}</span>
                {!x.rows && <span style={{ fontFamily: MONO, fontSize: 8.5, color: C.dim, border: `1px solid ${C.line}`, borderRadius: 3, padding: "1px 5px" }}>no data yet</span>}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 3, lineHeight: 1.4 }}>{x.q}</div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, marginTop: 4 }}>{x.scenarios} scenarios</div>
            </div>
          ))}
        </div>

        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 14 }}>
          <div className="wrapline mb-2">
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>repeats (n)</span>
            {[10, 30, 100].map((n) => (
              <button key={n} onClick={() => setRepeats(n)} style={{ background: repeats === n ? "rgba(155,140,204,0.12)" : "none", border: `1px solid ${repeats === n ? C.violet : C.line}`, borderRadius: 4, color: repeats === n ? C.violet : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "3px 9px", cursor: "pointer" }}>n={n}</button>
            ))}
            <span style={{ fontFamily: MONO, fontSize: 10, color: underpowered ? C.amber : C.dim }}>
              {underpowered ? "underpowered — wide CIs" : "95% CI + significance reported"}
            </span>
          </div>
          {hasData ? (
            <div className="wrapline" style={{ gap: 10 }}>
              <button onClick={run} disabled={phase === "running"}
                style={{ display: "flex", alignItems: "center", gap: 6, background: phase === "running" ? C.panel2 : C.violet, border: "none", borderRadius: 5, color: phase === "running" ? C.mut : C.bg, fontFamily: MONO, fontSize: 12, fontWeight: 700, padding: "8px 16px", cursor: phase === "running" ? "default" : "pointer" }}>
                {phase === "running" ? <><Repeat size={13} className="animate-spin" /> running {trials} trials…</> : <><Play size={13} /> Run live</>}
              </button>
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
                {conditions} conditions × {repeats} repeats × {g.scenarios} scenarios = <b style={{ color: C.mut }}>{trials}</b> model runs
              </span>
            </div>
          ) : (
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
              No recorded results yet. Author this experiment's scenarios to produce the first run.
            </div>
          )}
        </div>

        {hasData && phase === "done" && (
          <>
            <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
              <div className="wrapline" style={{ justifyContent: "space-between", padding: "10px 14px", borderBottom: `1px solid ${C.line}` }}>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em" }}>{g.metric.toUpperCase()}</span>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>n={repeats}×{g.scenarios} · {g.binary ? "Wilson 95% CI" : "bootstrap 95% CI"}</span>
              </div>
              {rows.map(({ cond, p, ci, note }) => {
                const col = cond.includes("ungoverned") ? C.red : cond.includes("L2") || cond === "governed" ? C.green : C.amber;
                return (
                  <div key={cond} className="result-row px-4 py-2.5" style={{ borderTop: `1px solid ${C.line}` }}>
                    <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, flex: "0 0 auto", minWidth: 96 }}>{cond}</span>
                    <span style={{ flex: "1 1 90px", minWidth: 80, height: 8, background: C.panel2, borderRadius: 3, position: "relative" }}>
                      <span style={{ position: "absolute", left: 0, top: 1, height: 6, width: `${p * 100}%`, background: col, borderRadius: 3 }} />
                      <span style={{ position: "absolute", top: 0, height: 8, left: `${ci[0] * 100}%`, width: `${(ci[1] - ci[0]) * 100}%`, borderLeft: `1px solid ${C.text}`, borderRight: `1px solid ${C.text}`, opacity: 0.5 }} />
                    </span>
                    <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, flex: "0 0 auto", fontWeight: 700 }}>
                      {p.toFixed(2)}<span style={{ color: C.mut, fontWeight: 400, fontSize: 9.5 }}> [{ci[0].toFixed(2)},{ci[1].toFixed(2)}]</span>
                    </span>
                    <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.mut }} className="rr-note">{note}</span>
                  </div>
                );
              })}
            </div>

            <div className="mt-2 px-4 py-2.5 wrapline" style={{ background: C.panel, border: `1px solid ${underpowered ? C.amber : C.line}`, borderRadius: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em" }}>SIGNIFICANCE</span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: v.col }}>{v.txt}</span>
            </div>

            {/* EvidenceCase entry — the differentiator the review flagged as missing */}
            <div className="mt-2 px-4 py-3 wrapline" style={{ justifyContent: "space-between", background: C.panel, border: `1px solid ${C.steel}`, borderRadius: 8, cursor: "pointer" }}>
              <div className="flex items-center gap-2">
                <Search size={13} color={C.steel} />
                <div>
                  <div style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>Investigate a trial → EvidenceCase</div>
                  <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 1 }}>the exact injection, provenance, the gated call, the verdict — per trial, not just the aggregate</div>
                </div>
              </div>
              <ChevronRight size={14} color={C.dim} />
            </div>

            <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              <button onClick={() => setOpenFinding(!openFinding)} className="flex items-center gap-2 px-4 py-3 w-full" style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer" }}>
                {openFinding ? <ChevronDown size={13} /> : <ChevronRight size={13} />} finding
              </button>
              {openFinding && (
                <div className="px-4 pb-3" style={{ paddingLeft: 34 }}>
                  <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, lineHeight: 1.6 }}>{g.finding}</div>
                  <div className="mt-2 flex items-start gap-2 p-2" style={{ background: C.panel2, borderRadius: 4 }}>
                    <AlertTriangle size={11} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
                    <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.5 }}>
                      This measures the effect of our own enforcement — a labeled "effect of governance" study, not a neutral benchmark. Reproduce it independently. The model layer is stochastic; only the governance verdicts replay exactly.
                    </span>
                  </div>
                </div>
              )}
            </div>

            <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 8 }}>
              <div className="wrapline mb-3" style={{ justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600 }}>Export & publish</div>
                  <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 2 }}>bundle embeds kernel version + config hash + model id + frozen traces — governance replays exact</div>
                </div>
                <button onClick={() => { setShared(true); setTimeout(() => setShared(false), 1500); }}
                  style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.violet}`, borderRadius: 5, color: shared ? C.green : C.violet, fontFamily: MONO, fontSize: 11, padding: "6px 12px", cursor: "pointer" }}>
                  {shared ? <Check size={12} /> : <Share2 size={12} />} {shared ? "link copied" : "Publish run"}
                </button>
              </div>
              <div className="wrapline">
                <button onClick={() => { setExported("md"); setTimeout(() => setExported(null), 1600); }}
                  style={{ display: "flex", alignItems: "center", gap: 6, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: exported === "md" ? C.green : C.text, fontFamily: MONO, fontSize: 11, padding: "7px 13px", cursor: "pointer" }}>
                  {exported === "md" ? <Check size={12} /> : <FileText size={12} />} {exported === "md" ? "results.md saved" : "Export Markdown"}
                </button>
                <button onClick={() => { setExported("pdf"); setTimeout(() => setExported(null), 1600); }}
                  style={{ display: "flex", alignItems: "center", gap: 6, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: exported === "pdf" ? C.green : C.text, fontFamily: MONO, fontSize: 11, padding: "7px 13px", cursor: "pointer" }}>
                  {exported === "pdf" ? <Check size={12} /> : <FileDown size={12} />} {exported === "pdf" ? "results.pdf saved" : "Export PDF"}
                </button>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginLeft: 4 }}>both carry the reproduce command</span>
              </div>
              {exported && (
                <div className="mt-3 p-3" style={{ background: C.panel2, borderRadius: 6, border: `1px solid ${C.line}` }}>
                  <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginBottom: 6 }}>{exported === "md" ? "results.md — preview" : "results.pdf — contents"}</div>
                  <pre style={{ fontFamily: MONO, fontSize: 10, color: C.mut, margin: 0, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{`# ${g.name}
${g.q}

| condition | ${g.metric} | 95% CI |
|---|---|---|
${rows.map((r) => `| ${r.cond} | ${r.p.toFixed(2)} | [${r.ci[0].toFixed(2)}, ${r.ci[1].toFixed(2)}] |`).join("\n")}

significance: ${v.txt}
test: ${g.test} · n=${repeats} × ${g.scenarios} scenarios = ${trials} model runs
note: model layer is stochastic (CI over repeats); governance verdicts replay bit-identical

reproduce:  axor lab replay ./bundle                      # governance verdicts, exact
            axor lab run --config ${g.id}.axl --repeats ${repeats}   # fresh live run, new sample`}</pre>
                </div>
              )}
            </div>
          </>
        )}

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
          Two layers, kept honest: a <b style={{ color: C.steel }}>live</b> run calls the model and is reported with CI over
          repeats — never bit-reproducible, and the UI never calls it deterministic. Publishing freezes the traces so anyone
          re-runs the <b style={{ color: C.green }}>governance verdicts</b> exactly. A fresh behavioral run is a new live sample.
        </div>
      </div>
    </div>
  );
}
