// Experiment builder (lab-builder mockup): a single-agent benchmark or a
// multi-agent game — the builder wires existing primitives, it doesn't invent
// mechanics. The run flow is real: POST /experiments/plan → show the estimate,
// pick a connected runtime (GET /runtimes), POST /runs → #/runs/{run_id}.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Beaker, Check, ChevronDown, ChevronRight, GitBranch, Play, Plus, RefreshCw, X,
} from "lucide-react";
import { C, MONO, btn, cta, inp } from "../theme";
import { navigate } from "../router";
import { api, PlanResult } from "../api";
import { useApp } from "../store";
import EmptyState, { Cmd } from "../components/EmptyState";

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
const TRUST_LEVELS = ["L0", "L1", "L2"];
const SUITES = [
  { id: "banking", label: "banking", n: 7, note: "IBAN args mix prompt + untrusted read" },
  { id: "slack", label: "slack", n: 7, note: "shared-channel taint" },
  { id: "workspace", label: "workspace", n: 7, note: "mixed sources" },
  { id: "travel", label: "travel", n: 2, note: "recipient prompt-given · 0 denials" },
];
const MODELS = ["o4-mini", "claude-opus-4.8", "gpt-5", "claude-sonnet-5"];

interface Player {
  id: number; name: string; kind: "single" | "federation";
  size: number; trust: string; governed: boolean;
}

function Section({ n, title, hint, children }: {
  n: string; title: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 mb-2" style={{ alignItems: "baseline" }}>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.violet, fontWeight: 700 }}>{n}</span>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>{title}</span>
        {hint && <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>· {hint}</span>}
      </div>
      {children}
    </div>
  );
}

const suiteScenarios = (suiteId: string): string[] => {
  const suite = SUITES.find((s) => s.id === suiteId);
  const n = suite ? suite.n : 1;
  return Array.from({ length: n }, (_, i) => `${suiteId}-${String(i + 1).padStart(2, "0")}`);
};

export default function Builder() {
  const { runtimeRef, setRuntimeRef, setLastRun, controlToken, setControlToken } = useApp();
  const [expType, setExpType] = useState<"benchmark" | "game">("benchmark");
  const [suite, setSuite] = useState("banking");
  const [model, setModel] = useState("o4-mini");
  const [players, setPlayers] = useState<Player[]>([
    { id: 1, name: "player-A", kind: "single", size: 1, trust: "L2", governed: true },
    { id: 2, name: "player-B", kind: "federation", size: 3, trust: "L1", governed: true },
  ]);
  const [interaction, setInteraction] = useState("iterated");
  const [rounds, setRounds] = useState(100);
  const [fault, setFault] = useState("none");
  const [metric, setMetric] = useState("coop");
  const [conditions, setConditions] = useState<string[]>(["ungoverned", "governed"]);
  const [repeats, setRepeats] = useState(30);
  const [showSpec, setShowSpec] = useState(false);

  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [busy, setBusy] = useState<"plan" | "run" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: api.listRuntimes });

  const addPlayer = () =>
    setPlayers([...players, {
      id: Date.now(), name: `player-${String.fromCharCode(65 + players.length)}`,
      kind: "single", size: 1, trust: "L1", governed: true,
    }]);
  const rmPlayer = (id: number) => setPlayers(players.filter((p) => p.id !== id));
  const patchPlayer = (id: number, patch: Partial<Player>) =>
    setPlayers(players.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  const toggleCond = (c: string) =>
    setConditions(conditions.includes(c) ? conditions.filter((x) => x !== c) : [...conditions, c]);

  const scenarioIds = expType === "benchmark" ? suiteScenarios(suite) : [interaction];
  const experiment: Record<string, unknown> = expType === "benchmark"
    ? {
        id: `bench-${suite}`, type: "benchmark", suite, model,
        scenario_ids: scenarioIds, conditions, repeats,
        reports: ["ASR", "benign_utility_cost_paired", "denials"],
      }
    : {
        id: `game-${interaction}`, type: "game", interaction,
        ...(interaction === "iterated" ? { rounds } : {}),
        players: players.map((p) => ({
          name: p.name, kind: p.kind, size: p.kind === "federation" ? p.size : 1,
          trust: p.trust, governed: p.governed,
        })),
        ...(fault === "none" ? {} : { fault }),
        metric, scenario_ids: scenarioIds, conditions, repeats,
      };

  const ready = expType === "benchmark"
    ? conditions.length >= 2
    : players.length >= 2 && conditions.length >= 2;
  const localTrials = conditions.length * repeats * scenarioIds.length;

  const doPlan = async () => {
    setBusy("plan"); setError(null); setPlan(null);
    try {
      setPlan(await api.planExperiment(experiment));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  };

  const doRun = async () => {
    if (!plan || !runtimeRef) return;
    setBusy("run"); setError(null);
    try {
      const run = await api.createRun(runtimeRef, experiment, plan.trials, plan.estimate as unknown as Record<string, unknown>);
      setLastRun(run.run_id);
      navigate(`runs/${run.run_id}`);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <div className="wrapline mb-4" style={{ gap: 10, justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 2px" }}>Compose an experiment.</h1>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
            a single-agent benchmark (AgentDojo-style) or a multi-agent game — the builder wires existing primitives, it doesn't invent mechanics
          </div>
        </div>
        <span style={{ display: "flex", alignItems: "center", gap: 5, fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
          <Beaker size={12} /> experiment builder
        </span>
      </div>

      {/* experiment type */}
      <div className="wrapline mb-4" style={{ gap: 10 }}>
        {([
          ["benchmark", "Benchmark", "1 agent × suite — reproduce AgentDojo-style results"],
          ["game", "Game", "multi-agent interaction"],
        ] as const).map(([id, label, note]) => (
          <div key={id} onClick={() => setExpType(id)}
            style={{ flex: "1 1 200px", cursor: "pointer", padding: 12, borderRadius: 8, background: expType === id ? "rgba(155,140,204,0.08)" : C.panel, border: `1px solid ${expType === id ? C.violet : C.line}` }}>
            <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, fontWeight: 600 }}>{label}</div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 2 }}>{note}</div>
          </div>
        ))}
      </div>

      {/* reproducibility — the honest two-layer split */}
      <div className="mb-5 p-3" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <div className="wrapline" style={{ gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em" }}>REPRODUCIBILITY</span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.steel }}>● live</span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>runs the model — stochastic, reported with CI over n</span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.green, marginLeft: 6 }}>● replay</span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>runs governance over recorded traces — bit-identical</span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginLeft: 6 }}>
            {conditions.length} × {repeats} × {scenarioIds.length} scenarios = <b style={{ color: C.mut }}>{localTrials}</b> live model runs
          </span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 6, lineHeight: 1.5 }}>
          You run <b style={{ color: C.steel }}>live</b> to get a result; publishing freezes the agent traces so anyone re-runs the
          governance verdicts in <b style={{ color: C.green }}>replay</b>, exactly. The model layer is never bit-reproducible — that's what n and CI are for.
        </div>
      </div>

      {expType === "benchmark" ? (
        <>
          <Section n="1" title="Agent & suite" hint="one governed agent against a benchmark suite">
            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 12 }}>
              <div className="wrapline mb-3" style={{ gap: 8, justifyContent: "space-between" }}>
                <span className="flex items-center gap-2">
                  <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 44 }}>agent</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, border: `1px solid ${C.violet}`, borderRadius: 4, padding: "3px 9px" }}>
                    {runtimeRef ?? "(no runtime yet — bring an agent first)"}
                  </span>
                </span>
                <span style={{ fontFamily: MONO, fontSize: 9, color: C.dim }}>bound runtime — a connected Axor runtime, not a raw model</span>
              </div>
              <div className="wrapline mb-3" style={{ gap: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 44 }}>model</span>
                {MODELS.map((m) => (
                  <button key={m} onClick={() => setModel(m)}
                    style={{ background: model === m ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${model === m ? C.steel : C.line}`, borderRadius: 4, color: model === m ? C.steel : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "3px 9px", cursor: "pointer" }}>{m}</button>
                ))}
                <span style={{ fontFamily: MONO, fontSize: 9, color: C.dim }}>model override — inherits the runtime's default</span>
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
          <Section n="1" title="Players" hint="single agent or a federation (tree) — composition is itself an experimental variable">
            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
              {players.map((p, i) => (
                <div key={p.id} className="wrapline px-3 py-2.5" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                  <GitBranch size={12} color={C.mut} />
                  <input value={p.name} onChange={(e) => patchPlayer(p.id, { name: e.target.value })}
                    style={{ flex: "1 1 110px", minWidth: 100, background: "none", border: "none", color: C.text, fontFamily: MONO, fontSize: 12, outline: "none" }} />
                  <div className="flex gap-1">
                    {(["single", "federation"] as const).map((k) => (
                      <button key={k} onClick={() => patchPlayer(p.id, { kind: k })}
                        style={{ background: p.kind === k ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${p.kind === k ? C.steel : C.line}`, borderRadius: 3, color: p.kind === k ? C.steel : C.dim, fontFamily: MONO, fontSize: 9.5, padding: "2px 7px", cursor: "pointer" }}>{k}</button>
                    ))}
                  </div>
                  {p.kind === "federation" && (
                    <input value={p.size} title="members"
                      onChange={(e) => patchPlayer(p.id, { size: Math.max(2, +e.target.value.replace(/\D/g, "") || 2) })}
                      style={{ width: 34, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 3, color: C.text, fontFamily: MONO, fontSize: 10.5, padding: "2px 5px", outline: "none", textAlign: "center" }} />
                  )}
                  <div className="flex gap-1">
                    {TRUST_LEVELS.map((l) => (
                      <button key={l} onClick={() => patchPlayer(p.id, { trust: l })}
                        style={{ background: p.trust === l ? "rgba(155,140,204,0.12)" : "none", border: `1px solid ${p.trust === l ? C.violet : C.line}`, borderRadius: 3, color: p.trust === l ? C.violet : C.dim, fontFamily: MONO, fontSize: 9.5, padding: "2px 7px", cursor: "pointer" }}>{l}</button>
                    ))}
                  </div>
                  <button onClick={() => patchPlayer(p.id, { governed: !p.governed })}
                    style={{ border: `1px solid ${p.governed ? C.green : C.line}`, borderRadius: 3, background: "none", color: p.governed ? C.green : C.dim, fontFamily: MONO, fontSize: 9.5, padding: "2px 8px", cursor: "pointer" }}>
                    {p.governed ? "governed" : "ungoverned"}
                  </button>
                  <X size={13} color={C.dim} style={{ cursor: "pointer" }} onClick={() => rmPlayer(p.id)} />
                </div>
              ))}
              <button onClick={addPlayer} className="flex items-center gap-2 px-3 py-2.5 w-full"
                style={{ background: "none", border: "none", borderTop: `1px solid ${C.line}`, color: C.mut, fontFamily: MONO, fontSize: 11, cursor: "pointer" }}>
                <Plus size={12} /> add player
              </button>
            </div>
          </Section>

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
                  style={{ ...inp, width: 64, padding: "4px 8px" }} />
              </div>
            )}
          </Section>

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

          <Section n="5" title="Conditions & repeats" hint="what to compare, and n per condition for significance">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              {["ungoverned", "governed", "governed (L2)", "adversarial"].map((c) => (
                <button key={c} onClick={() => toggleCond(c)}
                  style={{ background: conditions.includes(c) ? "rgba(70,167,88,0.1)" : "none", border: `1px solid ${conditions.includes(c) ? C.green : C.line}`, borderRadius: 4, color: conditions.includes(c) ? C.green : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "4px 10px", cursor: "pointer" }}>
                  {conditions.includes(c) ? "✓ " : ""}{c}
                </button>
              ))}
            </div>
          </Section>
        </>
      )}

      {/* repeats — shared */}
      <div className="wrapline mb-4">
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>repeats (n)</span>
        {[10, 30, 100].map((n) => (
          <button key={n} onClick={() => setRepeats(n)}
            style={{ background: repeats === n ? "rgba(155,140,204,0.12)" : "none", border: `1px solid ${repeats === n ? C.violet : C.line}`, borderRadius: 4, color: repeats === n ? C.violet : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "3px 9px", cursor: "pointer" }}>n={n}</button>
        ))}
        <span style={{ fontFamily: MONO, fontSize: 9.5, color: repeats < 10 ? C.amber : C.dim }}>
          {repeats < 10 ? "underpowered" : `${conditions.length}×${repeats}×${scenarioIds.length} = ${localTrials} trials, CI + the test per statistics.md`}
        </span>
      </div>

      {/* spec preview */}
      <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <button onClick={() => setShowSpec(!showSpec)} className="flex items-center gap-2 px-4 py-3 w-full"
          style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer" }}>
          {showSpec ? <ChevronDown size={13} /> : <ChevronRight size={13} />} experiment spec (experiment.axl)
        </button>
        {showSpec && (
          <pre style={{ margin: "0 14px 12px", background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 12, fontFamily: MONO, fontSize: 10.5, color: C.mut, overflow: "auto" }}>
            {JSON.stringify(experiment, null, 2)}
          </pre>
        )}
      </div>

      {/* plan → estimate → assign to a runtime */}
      <div className="wrapline mt-4" style={{ gap: 10 }}>
        <button onClick={doPlan} disabled={!ready || busy === "plan"} style={cta(ready && busy !== "plan")}>
          <Play size={14} /> {busy === "plan" ? "planning…" : "Plan & estimate"}
        </button>
        {!ready && (
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber }}>
            {expType === "benchmark" ? "need ≥2 conditions to compare" : "need ≥2 players and ≥2 conditions"}
          </span>
        )}
      </div>

      {plan && (
        <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 8 }}>
          <div className="flex items-center gap-2 mb-2">
            <Check size={13} color={C.green} />
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>
              planned · {plan.estimate.trials} trials
            </span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
              {plan.estimate.scenarios} scenarios × {plan.estimate.conditions} conditions × {plan.estimate.repeats} repeats
            </span>
          </div>

          <div className="wrapline mt-2" style={{ gap: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em" }}>RUNTIME</span>
            {(runtimes.data ?? []).map((rt) => (
              <button key={rt.runtime_ref} onClick={() => setRuntimeRef(rt.runtime_ref)}
                style={{ background: runtimeRef === rt.runtime_ref ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${runtimeRef === rt.runtime_ref ? C.steel : C.line}`, borderRadius: 4, color: runtimeRef === rt.runtime_ref ? C.steel : C.mut, fontFamily: MONO, fontSize: 10, padding: "3px 9px", cursor: "pointer" }}>
                {rt.runtime_ref}{rt.model ? ` · ${rt.model}` : ""}
              </button>
            ))}
            <button style={btn({ padding: "3px 9px", fontSize: 10 })} onClick={() => runtimes.refetch()}>
              <RefreshCw size={11} /> refresh
            </button>
          </div>
          {runtimes.isSuccess && (runtimes.data ?? []).length === 0 && (
            <div className="mt-2">
              <EmptyState title="no connected runtimes">
                Connect one first ("bring an agent" → endpoint instrumented), or start the jobs server:
                <Cmd>python -m lab_server --root ./lab-store --runtime-port 8010</Cmd>
              </EmptyState>
            </div>
          )}
          {runtimes.isError && (
            <div className="mt-2">
              <EmptyState title="runtime-jobs server unreachable">
                <Cmd>python -m lab_server --root ./lab-store --runtime-port 8010</Cmd>
                If the control surface is token-gated, set the control token here:
                <div className="mt-2">
                  <input value={controlToken} onChange={(e) => setControlToken(e.target.value)}
                    placeholder="control token" style={{ ...inp, width: 220 }} />
                </div>
              </EmptyState>
            </div>
          )}

          <div className="wrapline mt-3" style={{ gap: 10 }}>
            <button onClick={doRun} disabled={!runtimeRef || busy === "run"} style={cta(!!runtimeRef && busy !== "run")}>
              <Play size={14} /> {busy === "run" ? "assigning…" : "Assign run to runtime"}
            </button>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
              Lab assigns, the runtime executes — the run appears at #/runs/{"{run_id}"} and the runtime pulls it
            </span>
          </div>
        </div>
      )}

      {error && (
        <div className="mt-3">
          <EmptyState title="request failed">
            {error}. The runtime-jobs server must be running:
            <Cmd>python -m lab_server --root ./lab-store --runtime-port 8010</Cmd>
          </EmptyState>
        </div>
      )}

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
        A benchmark is the simplest experiment — one agent, one suite, defended vs undefended, exactly the
        AgentDojo shape from the paper (ASR + paired utility cost + denials). Games add players. Either way the
        builder only wires primitives the platform already has — agents, federations, trust levels, faults,
        the replay engine. It parameterizes; it never generates new game logic. The emitted{" "}
        <span style={{ color: C.mut }}>experiment.axl</span> is a plain file: version it, hand-edit it, share it.
      </div>
    </div>
  );
}
