import { useState } from "react";
import { Plus, X, Play, GitBranch, ArrowRight, Beaker, Check, ChevronDown, ChevronRight, Dice5 } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";
const btn = (x = {}) => ({ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "6px 11px", cursor: "pointer", ...x });

// building blocks — all are existing primitives, the builder only composes them
const INTERACTIONS = [
  { id: "iterated", label: "iterated game", note: "repeated rounds, same players" },
  { id: "delegation", label: "delegation chain", note: "parent → children, one task" },
  { id: "auction", label: "auction / bidding", note: "k bidders, one auctioneer" },
  { id: "negotiation", label: "negotiation", note: "two agents, claims exchanged" },
];
const FAULTS = ["none", "tool deprivation", "compromised member", "permission revocation", "budget exhaustion"];
const METRICS = [
  { id: "coop", label: "cooperation rate" },
  { id: "containment", label: "damage containment" },
  { id: "integrity", label: "claim integrity (honesty)" },
  { id: "welfare", label: "total payoff / welfare" },
];
const trustLevels = ["L0", "L1", "L2"];
const SUITES = [
  { id: "banking", label: "banking", n: 7, note: "IBAN args mix prompt + untrusted read" },
  { id: "slack", label: "slack", n: 7, note: "shared-channel taint" },
  { id: "workspace", label: "workspace", n: 7, note: "mixed sources" },
  { id: "travel", label: "travel", n: 2, note: "recipient prompt-given · 0 denials" },
];
const MODELS = ["o4-mini", "claude-opus-4.8", "gpt-5", "claude-sonnet-5"];

export default function App() {
  const [expType, setExpType] = useState("benchmark"); // benchmark (1 agent) | game (multi)
  const [agent, setAgent] = useState("(no agent yet — bring one first)");
  const [suite, setSuite] = useState("banking");
  const [model, setModel] = useState("o4-mini");
  const [players, setPlayers] = useState([
    { id: 1, name: "player-A", kind: "single", size: 1, trust: "L2", governed: true },
    { id: 2, name: "player-B", kind: "federation", size: 3, trust: "L1", governed: true },
  ]);
  const [interaction, setInteraction] = useState("iterated");
  const [rounds, setRounds] = useState(100);
  const [fault, setFault] = useState("none");
  const [metric, setMetric] = useState("coop");
  const [conditions, setConditions] = useState(["ungoverned", "governed"]);
  const [repeats, setRepeats] = useState(30);
  const [showSpec, setShowSpec] = useState(false);
  const [built, setBuilt] = useState(false);

  const addPlayer = () => setPlayers([...players, { id: Date.now(), name: `player-${String.fromCharCode(65 + players.length)}`, kind: "single", size: 1, trust: "L1", governed: true }]);
  const rmPlayer = (id) => setPlayers(players.filter((p) => p.id !== id));
  const patchPlayer = (id, k, v) => setPlayers(players.map((p) => (p.id === id ? { ...p, [k]: v } : p)));
  const toggleCond = (c) => setConditions(conditions.includes(c) ? conditions.filter((x) => x !== c) : [...conditions, c]);

  const spec = expType === "benchmark"
    ? { type: "benchmark", suite, model, conditions, repeats,
        reports: ["ASR", "benign_utility_cost_paired", "denials"] }
    : { type: "game", interaction, rounds: interaction === "iterated" ? rounds : undefined,
        players: players.map((p) => ({ name: p.name, kind: p.kind, size: p.kind === "federation" ? p.size : 1, trust: p.trust, governed: p.governed })),
        fault: fault === "none" ? undefined : fault, metric, conditions, repeats };
  const ready = expType === "benchmark"
    ? conditions.length >= 2
    : players.length >= 2 && conditions.length >= 2;

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "clamp(14px, 4vw, 28px) clamp(10px, 3vw, 20px)" }}>
      <style>{`
        .autogrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px; }
        .autogrid-wide { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
        .wrapline { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; row-gap: 6px; }
        @media (max-width: 480px) { .hide-narrow { display: none; } }
      `}</style>
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <div className="wrapline mb-6" style={{ gap: 10, justifyContent: "space-between" }}>
          <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700 }}>AXOR<span style={{ color: C.violet }}> LAB</span></span>
          <span style={{ display: "flex", alignItems: "center", gap: 5, fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            <Beaker size={12} /> experiment builder · lab.useaxor.net
          </span>
        </div>

        <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 2px" }}>Compose an experiment.</h1>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
          a single-agent benchmark (AgentDojo-style) or a multi-agent game — the builder wires existing primitives, it doesn't invent mechanics
        </div>

        {/* experiment type */}
        <div className="wrapline mb-4" style={{ gap: 10 }}>
          {[["benchmark", "Benchmark", "1 agent × suite — reproduce AgentDojo-style results"], ["game", "Game", "multi-agent interaction"]].map(([id, lab, note]) => (
            <div key={id} onClick={() => setExpType(id)}
              style={{ flex: "1 1 200px", cursor: "pointer", padding: 12, borderRadius: 8, background: expType === id ? "rgba(155,140,204,0.08)" : C.panel, border: `1px solid ${expType === id ? C.violet : C.line}` }}>
              <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, fontWeight: 600 }}>{lab}</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 2 }}>{note}</div>
            </div>
          ))}
        </div>

        {/* reproducibility mode — the honest two-layer split */}
        <div className="mb-5 p-3" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <div className="wrapline" style={{ gap: 8 }}>
            {(() => {
              const suiteObj = (typeof SUITES !== "undefined") ? SUITES.find((x) => x.id === suite) : null;
              const scen = expType === "benchmark" ? (suiteObj ? suiteObj.n : 1) : 1;
              const trials = conditions.length * repeats * scen;
              return (
                <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginLeft: 6 }}>
                  {conditions.length} × {repeats} × {scen} scenarios = <b style={{ color: C.mut }}>{trials}</b> live model runs
                </span>
              );
            })()}
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em" }}>REPRODUCIBILITY</span>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.steel }}>● live</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>runs the model — stochastic, reported with CI over n</span>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.green, marginLeft: 6 }}>● replay</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>runs governance over recorded traces — bit-identical</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 6, lineHeight: 1.5 }}>
            You run <b style={{ color: C.steel }}>live</b> to get a result; publishing freezes the agent traces so anyone re-runs the governance verdicts in <b style={{ color: C.green }}>replay</b>, exactly. The model layer is never bit-reproducible — that's what n and CI are for.
          </div>
        </div>

        {expType === "benchmark" ? (
          <>
            <Section n="1" title="Agent & suite" hint="one governed agent against a benchmark suite">
              <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 12 }}>
                <div className="wrapline mb-3" style={{ gap: 8 }}>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 44 }}>model</span>
                  {MODELS.map((m) => (
                    <button key={m} onClick={() => setModel(m)}
                      style={{ background: model === m ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${model === m ? C.steel : C.line}`, borderRadius: 4, color: model === m ? C.steel : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "3px 9px", cursor: "pointer" }}>{m}</button>
                  ))}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>suite</div>
                <div className="autogrid">
                  {SUITES.map((su) => (
                    <div key={su.id} onClick={() => setSuite(su.id)}
                      style={{ cursor: "pointer", padding: 10, borderRadius: 7, background: suite === su.id ? "rgba(155,140,204,0.08)" : C.panel2, border: `1px solid ${suite === su.id ? C.violet : C.line}` }}>
                      <div className="wrapline" style={{ justifyContent: "space-between" }}>
                        <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>{su.label}</span>
                        <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>n={su.n}</span>
                      </div>
                      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.mut, marginTop: 2 }}>{su.note}</div>
                    </div>
                  ))}
                </div>
              </div>
            </Section>
            <Section n="2" title="Conditions" hint="compare governed vs the ungoverned (undefended) baseline — the AgentDojo shape">
              <div className="wrapline">
                {["ungoverned", "governed", "governed + allowlist"].map((c) => (
                  <button key={c} onClick={() => toggleCond(c)}
                    style={{ background: conditions.includes(c) ? "rgba(70,167,88,0.1)" : "none", border: `1px solid ${conditions.includes(c) ? C.green : C.line}`, borderRadius: 4, color: conditions.includes(c) ? C.green : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "4px 10px", cursor: "pointer" }}>
                    {conditions.includes(c) ? "✓ " : ""}{c}
                  </button>
                ))}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 6 }}>
                reports: ASR (attack success) · benign-utility cost (paired Δ vs undefended) · denials — the Table 1 shape
              </div>
            </Section>
          </>
        ) : (
          <>
        {/* 1. players */}
        <Section n="1" title="Players" hint="single agent or a federation (tree) — composition is itself an experimental variable">
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            {players.map((p, i) => (
              <div key={p.id} className="wrapline px-3 py-2.5" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                <GitBranch size={12} color={C.mut} />
                <input value={p.name} onChange={(e) => patchPlayer(p.id, "name", e.target.value)}
                  style={{ flex: "1 1 110px", minWidth: 100, background: "none", border: "none", color: C.text, fontFamily: MONO, fontSize: 12, outline: "none" }} />
                <div className="flex gap-1">
                  {["single", "federation"].map((k) => (
                    <button key={k} onClick={() => patchPlayer(p.id, "kind", k)}
                      style={{ background: p.kind === k ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${p.kind === k ? C.steel : C.line}`, borderRadius: 3, color: p.kind === k ? C.steel : C.dim, fontFamily: MONO, fontSize: 9.5, padding: "2px 7px", cursor: "pointer" }}>{k}</button>
                  ))}
                </div>
                {p.kind === "federation" && (
                  <input value={p.size} onChange={(e) => patchPlayer(p.id, "size", Math.max(2, +e.target.value.replace(/\D/g, "") || 2))}
                    title="members"
                    style={{ width: 34, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 3, color: C.text, fontFamily: MONO, fontSize: 10.5, padding: "2px 5px", outline: "none", textAlign: "center" }} />
                )}
                <div className="flex gap-1">
                  {trustLevels.map((l) => (
                    <button key={l} onClick={() => patchPlayer(p.id, "trust", l)}
                      style={{ background: p.trust === l ? "rgba(155,140,204,0.12)" : "none", border: `1px solid ${p.trust === l ? C.violet : C.line}`, borderRadius: 3, color: p.trust === l ? C.violet : C.dim, fontFamily: MONO, fontSize: 9.5, padding: "2px 7px", cursor: "pointer" }}>{l}</button>
                  ))}
                </div>
                <button onClick={() => patchPlayer(p.id, "governed", !p.governed)}
                  style={{ border: `1px solid ${p.governed ? C.green : C.line}`, borderRadius: 3, background: "none", color: p.governed ? C.green : C.dim, fontFamily: MONO, fontSize: 9.5, padding: "2px 8px", cursor: "pointer" }}>
                  {p.governed ? "governed" : "ungoverned"}
                </button>
                <X size={13} color={C.dim} style={{ cursor: "pointer" }} onClick={() => rmPlayer(p.id)} />
              </div>
            ))}
            <button onClick={addPlayer} className="flex items-center gap-2 px-3 py-2.5 w-full" style={{ background: "none", border: "none", borderTop: `1px solid ${C.line}`, color: C.mut, fontFamily: MONO, fontSize: 11, cursor: "pointer" }}>
              <Plus size={12} /> add player
            </button>
          </div>
        </Section>

        {/* 2. interaction */}
        <Section n="2" title="Interaction" hint="how players act on each other">
          <div className="autogrid">
            {INTERACTIONS.map((x) => (
              <div key={x.id} onClick={() => setInteraction(x.id)}
                style={{ cursor: "pointer", padding: 10, borderRadius: 7, background: interaction === x.id ? "rgba(155,140,204,0.08)" : C.panel, border: `1px solid ${interaction === x.id ? C.violet : C.line}` }}>
                <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>{x.label}</div>
                <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.mut, marginTop: 2 }}>{x.note}</div>
              </div>
            ))}
          </div>
          {interaction === "iterated" && (
            <div className="flex items-center gap-2 mt-2">
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>rounds</span>
              <input value={rounds} onChange={(e) => setRounds(+e.target.value.replace(/\D/g, "") || 0)}
                style={{ width: 64, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 12, padding: "4px 8px", outline: "none" }} />
            </div>
          )}
        </Section>

        {/* 3. fault + metric side by side */}
        <div className="autogrid-wide">
          <Section n="3" title="Fault" hint="perturbation injected during the run">
            <div className="flex flex-col gap-1.5">
              {FAULTS.map((f) => (
                <button key={f} onClick={() => setFault(f)}
                  style={{ textAlign: "left", background: fault === f ? "rgba(155,140,204,0.08)" : C.panel, border: `1px solid ${fault === f ? C.violet : C.line}`, borderRadius: 5, color: fault === f ? C.text : C.mut, fontFamily: MONO, fontSize: 11, padding: "6px 10px", cursor: "pointer" }}>{f}</button>
              ))}
            </div>
          </Section>
          <Section n="4" title="Metric" hint="what the experiment measures">
            <div className="flex flex-col gap-1.5">
              {METRICS.map((m) => (
                <button key={m.id} onClick={() => setMetric(m.id)}
                  style={{ textAlign: "left", background: metric === m.id ? "rgba(155,140,204,0.08)" : C.panel, border: `1px solid ${metric === m.id ? C.violet : C.line}`, borderRadius: 5, color: metric === m.id ? C.text : C.mut, fontFamily: MONO, fontSize: 11, padding: "6px 10px", cursor: "pointer" }}>{m.label}</button>
              ))}
            </div>
          </Section>
        </div>

        {/* 5. conditions + repeats */}
        <Section n="5" title="Conditions & repeats" hint="what to compare, and n per condition for significance">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            {["ungoverned", "governed", "governed (L2)", "adversarial"].map((c) => (
              <button key={c} onClick={() => toggleCond(c)}
                style={{ background: conditions.includes(c) ? "rgba(70,167,88,0.1)" : "none", border: `1px solid ${conditions.includes(c) ? C.green : C.line}`, borderRadius: 4, color: conditions.includes(c) ? C.green : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "4px 10px", cursor: "pointer" }}>
                {conditions.includes(c) ? "✓ " : ""}{c}
              </button>
            ))}
          </div>
          <div className="wrapline">
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>repeats (n)</span>
            {[10, 30, 100].map((n) => (
              <button key={n} onClick={() => setRepeats(n)} style={{ background: repeats === n ? "rgba(155,140,204,0.12)" : "none", border: `1px solid ${repeats === n ? C.violet : C.line}`, borderRadius: 4, color: repeats === n ? C.violet : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "3px 9px", cursor: "pointer" }}>n={n}</button>
            ))}
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: repeats < 10 ? C.amber : C.dim }}>
              {repeats < 10 ? "underpowered" : `${conditions.length}×${repeats} = ${conditions.length * repeats} trials, CI + the test per statistics.md`}
            </span>
          </div>
        </Section>
          </>
        )}

        {/* spec preview + run */}
        <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <button onClick={() => setShowSpec(!showSpec)} className="flex items-center gap-2 px-4 py-3 w-full" style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer" }}>
            {showSpec ? <ChevronDown size={13} /> : <ChevronRight size={13} />} experiment spec (experiment.axl)
          </button>
          {showSpec && (
            <pre style={{ margin: "0 14px 12px", background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 12, fontFamily: MONO, fontSize: 10.5, color: C.mut, overflow: "auto" }}>{JSON.stringify(spec, null, 2)}</pre>
          )}
        </div>

        <div className="wrapline mt-4" style={{ gap: 10 }}>
          <button onClick={() => ready && setBuilt(true)} disabled={!ready}
            style={{ display: "flex", alignItems: "center", gap: 7, background: ready ? C.violet : C.panel2, border: "none", borderRadius: 5, color: ready ? C.bg : C.dim, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "9px 18px", cursor: ready ? "pointer" : "default" }}>
            <Play size={14} /> Build & run
          </button>
          <button style={btn()}><Dice5 size={12} /> randomize seeds</button>
          {!ready && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber }}>{expType === "benchmark" ? "need ≥2 conditions to compare" : "need ≥2 players and ≥2 conditions"}</span>}
          {built && <span style={{ fontFamily: MONO, fontSize: 11, color: C.green, display: "flex", alignItems: "center", gap: 5 }}><Check size={12} /> queued · {conditions.length * repeats} trials → results tab</span>}
        </div>

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
          A benchmark is the simplest experiment — one agent, one suite, defended vs undefended, exactly the
          AgentDojo shape from the paper (ASR + paired utility cost + denials). Games add players. Either way the
          builder only wires primitives the platform already has — agents, federations, trust levels, faults,
          the replay engine. It parameterizes; it never generates new game logic. A single agent is a federation of
          size 1; the difference that matters in a game is the boundary: a federation-player's members coordinate
          on carried labels internally, while everything between players is inter — re-derived, claims not data.
          "One federation of 5 vs five singles" is therefore a real experiment, not two names for the same setup. The emitted <span style={{ color: C.mut }}>experiment.axl</span> is
          a plain file: version it, hand-edit it, share it — the run it produces is reproducible bit-for-bit.
        </div>
      </div>
    </div>
  );
}

function Section({ n, title, hint, children }) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline gap-2 mb-2">
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.violet, fontWeight: 700 }}>{n}</span>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>{title}</span>
        <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>· {hint}</span>
      </div>
      {children}
    </div>
  );
}
