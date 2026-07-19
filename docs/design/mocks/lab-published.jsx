import { useState } from "react";
import { BadgeCheck, GitFork, Copy, Check, ChevronDown, ChevronRight, Search, Quote, Lock, AlertTriangle } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export default function App() {
  const [copied, setCopied] = useState(null); // 'cmd' | 'cite'
  const [openMethod, setOpenMethod] = useState(false);
  const [openLimits, setOpenLimits] = useState(true);
  const copy = (k) => { setCopied(k); setTimeout(() => setCopied(null), 1400); };

  const rows = [
    ["ungoverned", 0.83, [0.66, 0.92], C.red],
    ["governed", 0.17, [0.08, 0.34], C.green],
  ];

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "clamp(14px,4vw,28px) clamp(10px,3vw,20px)" }}>
      <style>{`.wrapline{display:flex;align-items:center;flex-wrap:wrap;gap:8px;row-gap:6px;} .result-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}`}</style>
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <div className="wrapline mb-5" style={{ gap: 10, justifyContent: "space-between" }}>
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700 }}>AXOR<span style={{ color: C.violet }}> LAB</span></span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>lab.useaxor.net/e/e042</span>
        </div>

        {/* header: status + immutability */}
        <div className="wrapline mb-3" style={{ gap: 10 }}>
          <span className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 10, color: C.green, border: `1px solid ${C.green}`, borderRadius: 4, padding: "3px 9px" }}>
            <BadgeCheck size={12} /> Lab-executed
          </span>
          <span className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 10, color: C.steel, border: `1px solid ${C.steel}`, borderRadius: 4, padding: "3px 9px" }}>
            <Lock size={11} /> signed bundle
          </span>
          <span className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 10, color: C.violet, border: `1px solid ${C.violet}`, borderRadius: 4, padding: "3px 9px" }}>
            reproduced ×4
          </span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>immutable · 2026-07-04</span>
        </div>

        <h1 style={{ fontSize: 21, fontWeight: 680, lineHeight: 1.25, margin: "0 0 6px" }}>
          Does carried-taint localize a compromised federation member's damage?
        </h1>
        <div className="wrapline" style={{ gap: 10, marginBottom: 20 }}>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>@smu-axor</span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>adversarial-federation · game · 3 scenarios</span>
        </div>

        {/* the result */}
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
          <div className="wrapline" style={{ justifyContent: "space-between", padding: "10px 14px", borderBottom: `1px solid ${C.line}` }}>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em" }}>FEDERATION REACHED BY INJECTED AGENT</span>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>n=30×3 · Wilson 95% CI · McNemar</span>
          </div>
          {rows.map(([cond, p, ci, col]) => (
            <div key={cond} className="result-row px-4 py-2.5" style={{ borderTop: `1px solid ${C.line}` }}>
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, flex: "0 0 auto", minWidth: 96 }}>{cond}</span>
              <span style={{ flex: "1 1 80px", minWidth: 70, height: 8, background: C.panel2, borderRadius: 3, position: "relative" }}>
                <span style={{ position: "absolute", left: 0, top: 1, height: 6, width: `${p * 100}%`, background: col, borderRadius: 3 }} />
                <span style={{ position: "absolute", top: 0, height: 8, left: `${ci[0] * 100}%`, width: `${(ci[1] - ci[0]) * 100}%`, borderLeft: `1px solid ${C.text}`, borderRight: `1px solid ${C.text}`, opacity: 0.5 }} />
              </span>
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, flex: "0 0 auto", fontWeight: 700 }}>{p.toFixed(2)}<span style={{ color: C.mut, fontWeight: 400, fontSize: 9.5 }}> [{ci[0].toFixed(2)},{ci[1].toFixed(2)}]</span></span>
            </div>
          ))}
          <div className="px-4 py-2.5" style={{ borderTop: `1px solid ${C.line}` }}>
            <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: "0.06em", marginBottom: 4 }}>CLAIMS — typed by what supports them</div>
            <div className="flex items-start gap-2" style={{ marginBottom: 3 }}>
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: C.steel, border: `1px solid ${C.steel}`, borderRadius: 3, padding: "1px 5px", flexShrink: 0 }}>exact</span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>On trace t_7c31_07, axor-core 0.4.2 returns DENY — recipient is untrusted-derived. Replays bit-identical.</span>
            </div>
            <div className="flex items-start gap-2">
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: C.amber, border: `1px solid ${C.amber}`, borderRadius: 3, padding: "1px 5px", flexShrink: 0 }}>stat</span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>Over 30 live trials, reach 0.83→0.17; McNemar p&lt;0.01 (paired). A behavioral delta — statistical, not exact.</span>
            </div>
          </div>
        </div>

        {/* investigate + reproduce actions */}
        <div className="wrapline mt-3" style={{ gap: 8 }}>
          <button className="flex items-center gap-2" style={{ background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11, padding: "7px 13px", cursor: "pointer" }}><Search size={12} /> Investigate a trial</button>
          <button className="flex items-center gap-2" style={{ background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "7px 13px", cursor: "pointer" }}><GitFork size={12} /> Fork this experiment</button>
        </div>

        {/* reproduce command */}
        <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 8, padding: 14 }}>
          <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>Reproduce</span>
            <button onClick={() => copy("cmd")} style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: `1px solid ${C.line}`, borderRadius: 4, color: copied === "cmd" ? C.green : C.mut, fontFamily: MONO, fontSize: 10, padding: "3px 9px", cursor: "pointer" }}>{copied === "cmd" ? <Check size={11} /> : <Copy size={11} />} {copied === "cmd" ? "copied" : "copy"}</button>
          </div>
          <pre style={{ margin: 0, background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 10, color: C.mut, overflow: "auto", lineHeight: 1.6 }}>{`axor lab replay ./e042-bundle       # governance verdicts, bit-identical
axor lab run --config e042.axl --repeats 30   # fresh live sample (stochastic)`}</pre>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 6 }}>replay reproduces the governance conclusion exactly; a fresh run is a new stochastic sample (CI over repeats)</div>
        </div>

        {/* artifacts + versions — the provenance block */}
        <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <button onClick={() => setOpenMethod(!openMethod)} className="flex items-center gap-2 px-4 py-3 w-full" style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer" }}>
            {openMethod ? <ChevronDown size={13} /> : <ChevronRight size={13} />} methodology & artifacts — the bundle that makes this reproducible
          </button>
          {openMethod && (
            <div className="px-4 pb-3" style={{ paddingLeft: 34, fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.9 }}>
              <Kv k="kernel version" v="axor-core 0.4.2" hash />
              <Kv k="config hash" v="sha256:9f2a…c701" hash />
              <Kv k="model" v="o4-mini · temp 1.0 · 2026-06-30" />
              <Kv k="scenarios" v="adversarial-fed ×3 (e042.axl)" />
              <Kv k="seeds" v="run_7c31 · 30 pinned" />
              <Kv k="artifacts" v="config · traces · verdicts · aggregates · redaction manifest" />
              <div style={{ color: C.dim, marginTop: 6, lineHeight: 1.5 }}>
                the kernel version is load-bearing: the same traces under a different <span style={{ color: C.mut }}>decide</span> can yield a different verdict, so it is pinned in the bundle.
              </div>
            </div>
          )}
        </div>

        {/* limitations — stated, not hidden */}
        <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.amber}`, borderRadius: 8 }}>
          <button onClick={() => setOpenLimits(!openLimits)} className="flex items-center gap-2 px-4 py-3 w-full" style={{ background: "none", border: "none", color: C.amber, fontFamily: MONO, fontSize: 11.5, cursor: "pointer" }}>
            {openLimits ? <ChevronDown size={13} /> : <ChevronRight size={13} />} limitations
          </button>
          {openLimits && (
            <div className="px-4 pb-3" style={{ paddingLeft: 34, fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
              <div className="flex items-start gap-2">
                <AlertTriangle size={11} color={C.amber} style={{ marginTop: 2, flexShrink: 0 }} />
                <span>Measures the effect of Axor's own enforcement — a labeled "effect of governance" study, not a neutral third-party benchmark. Reproduce independently before citing as neutral.</span>
              </div>
              <div className="mt-1.5" style={{ paddingLeft: 19 }}>Model layer is stochastic; only governance verdicts replay bit-identical. Downstream behavior after a DENY is not recoverable from frozen traces.</div>
              <div className="mt-1.5" style={{ paddingLeft: 19 }}>n=30 per condition — sign is stable; small effects would need more repeats.</div>
            </div>
          )}
        </div>

        {/* independent reproductions */}
        <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>Independent reproductions</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.steel }}>4 reproductions</span>
          </div>
          {[
            ["@ext-lab-mit", "fresh_live", "0.17 [0.08,0.31]", C.green],
            ["@cmu-secagents", "fresh_live", "0.19 [0.10,0.34]", C.green],
            ["@indie-sec", "exact_replay", "DENY reproduced", C.steel],
            ["@robustness-grp", "changed_model", "0.21 (gpt-5)", C.amber],
          ].map(([who, kind, r, col]) => (
            <div key={who} className="wrapline" style={{ justifyContent: "space-between", padding: "3px 0", gap: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>{who}</span>
              <span className="wrapline" style={{ gap: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: 8.5, color: col, border: `1px solid ${col}`, borderRadius: 3, padding: "1px 5px" }}>{kind}</span>
                <span style={{ fontFamily: MONO, fontSize: 10, color: C.green }}>{r}</span>
              </span>
            </div>
          ))}
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 6 }}>counted, not assumed — and typed: exact_replay (verdict, bit-identical) vs fresh_live (new sample, within CI) vs changed_model/kernel (robustness, a different claim)</div>
        </div>

        {/* citation */}
        <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <span className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}><Quote size={12} color={C.mut} /> Cite</span>
            <button onClick={() => copy("cite")} style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: `1px solid ${C.line}`, borderRadius: 4, color: copied === "cite" ? C.green : C.mut, fontFamily: MONO, fontSize: 10, padding: "3px 9px", cursor: "pointer" }}>{copied === "cite" ? <Check size={11} /> : <Copy size={11} />} {copied === "cite" ? "copied" : "copy"}</button>
          </div>
          <pre style={{ margin: 0, background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 9.5, color: C.mut, overflow: "auto", whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{`@misc{axor-e042,
  title  = {Carried-taint containment of a compromised
            federation member},
  author = {smu-axor},
  year   = {2026},
  note   = {Axor Lab, lab.useaxor.net/e/e042,
            axor-core 0.4.2, sha256:9f2a…c701}
}`}</pre>
        </div>

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
          An immutable public record: question, result with honest CI, the full bundle that reproduces it, stated
          limitations, counted independent reproductions, and a citation. This is what a published Lab run is — not a
          screenshot of a number, but an artifact another researcher can re-run, fork, and cite.
        </div>
      </div>
    </div>
  );
}

function Kv({ k, v, hash }) {
  return (
    <div className="wrapline" style={{ gap: 8 }}>
      <span style={{ color: C.dim, minWidth: 96 }}>{k}</span>
      <span style={{ color: hash ? C.steel : C.text }}>{v}</span>
    </div>
  );
}
