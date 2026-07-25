// Experiment builder, disclosed in three levels.
//
// It used to open with the whole research knob panel — experiment type, four
// suites, four models, conditions, repeats, and in game mode players,
// federations, trust levels, rounds, faults and metrics — and it could not
// actually build anything runnable: the suite list was invented here, with
// fabricated scenario ids (`banking-01`…`banking-07`, `slack-*`, `travel-*`) that
// no scenario matched.
//
// Now the menu comes from GET /catalog — the code that owns the scenarios — and
// the levels are:
//   simple    pick a suite, run it. Executes in the server: no agent, no cost.
//   adjust    conditions and repeats, with the statistical floor made visible.
//   advanced  a connected runtime instead of local, the composed .axl, the CLI.
//
// The UI is the primary path. The CLI appears at the advanced level as the
// equivalent command, for versioning an experiment and for what only it can do:
// a live model with its hard cost ceiling, and --real-kernel.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Check, ChevronDown, ChevronRight, Play, RefreshCw, TriangleAlert,
} from "lucide-react";
import { C, MONO, btn, cta, inp } from "../theme";
import { navigate } from "../router";
import { api, type Catalog } from "../api";
import { useApp } from "../store";
import EmptyState, { Cmd } from "../components/EmptyState";

type Level = "simple" | "adjust" | "advanced";

export default function Builder() {
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: api.catalog });

  if (catalog.isLoading) {
    return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading catalog…</div>;
  }
  if (catalog.isError || !catalog.data) {
    return (
      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <EmptyState title="run API unreachable">
          The builder needs the run API. One command serves it alongside the catalog:
          <Cmd>python -m lab_server --root ./lab-store</Cmd>
          <ControlTokenField />
        </EmptyState>
      </div>
    );
  }
  return <BuilderBody catalog={catalog.data} />;
}

function ControlTokenField() {
  const { controlToken, setControlToken } = useApp();
  return (
    <div className="mt-2">
      If the control surface is token-gated, set the control token here:
      <div className="mt-1">
        <input value={controlToken} onChange={(e) => setControlToken(e.target.value)}
          placeholder="control token" style={{ ...inp, width: 220 }} />
      </div>
    </div>
  );
}

function Disclosure({ open, onToggle, label, children }: {
  open: boolean; onToggle: () => void; label: string; children: React.ReactNode;
}) {
  return (
    <div className="mt-4" style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12 }}>
      <button onClick={onToggle}
        style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", padding: 0, cursor: "pointer", color: open ? C.text : C.mut, fontFamily: MONO, fontSize: 11.5 }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {label}
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  );
}

function BuilderBody({ catalog }: { catalog: Catalog }) {
  const { runtimeRef, setRuntimeRef, setLastRun } = useApp();
  const baseline = catalog.conditions.find((c) => c.baseline)?.id ?? "ungoverned";

  const [level, setLevel] = useState<Level>("simple");
  const [suiteId, setSuiteId] = useState(catalog.suites[0]?.id ?? "");
  const [conditions, setConditions] = useState<string[]>(
    catalog.conditions.map((c) => c.id),
  );
  const [repeats, setRepeats] = useState(catalog.repeats.default);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [spec, setSpec] = useState<string | null>(null);

  const suite = catalog.suites.find((s) => s.id === suiteId);
  const trials = (suite?.scenarios.length ?? 0) * conditions.length * repeats;
  const underpowered = repeats < catalog.repeats.min_powered;
  const missingBaseline = !conditions.includes(baseline);

  const runtimes = useQuery({
    queryKey: ["runtimes"], queryFn: api.listRuntimes, enabled: level === "advanced",
  });

  const composeSpec = { suite: suiteId, conditions, repeats };

  const runHere = async () => {
    setBusy(true); setError(null);
    try {
      const run = await api.runComposed(composeSpec);
      setLastRun(run.run_id);
      navigate(`results/${run.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const assignToRuntime = async () => {
    if (!runtimeRef) return;
    setBusy(true); setError(null);
    try {
      const composed = await api.composeExperiment(composeSpec);
      const experiment = (composed.document as { experiment: Record<string, unknown> }).experiment;
      const run = await api.createRun(
        runtimeRef, experiment, composed.planned_trials,
        composed.estimate as unknown as Record<string, unknown>,
      );
      setLastRun(run.run_id);
      navigate(`runs/${run.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const showSpec = async () => {
    setError(null);
    try {
      const composed = await api.composeExperiment(composeSpec);
      setSpec(JSON.stringify(composed.document, null, 2));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Keep the catalogue's own order rather than append-on-click, so the condition
  // row reads the same however you got there.
  const toggleCondition = (id: string) =>
    setConditions(
      catalog.conditions
        .map((c) => c.id)
        .filter((c) => (c === id ? !conditions.includes(id) : conditions.includes(c))),
    );

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 2px" }}>Compose an experiment.</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 18, lineHeight: 1.6 }}>
        One agent against a suite, defended vs undefended — the AgentDojo shape. It runs here:
        the agent is a deterministic stand-in, so there is no key, no provider and no cost.
      </div>

      {/* ── level 1: pick a suite, run it ───────────────────────────────────── */}
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 8 }}>suite</div>
      <div className="autogrid">
        {catalog.suites.map((s) => (
          <div key={s.id} onClick={() => setSuiteId(s.id)}
            style={{ cursor: "pointer", padding: 12, borderRadius: 8, background: suiteId === s.id ? "rgba(155,140,204,0.08)" : C.panel, border: `1px solid ${suiteId === s.id ? C.violet : C.line}` }}>
            <div className="wrapline" style={{ justifyContent: "space-between" }}>
              <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>{s.label}</span>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                {s.scenarios.length} scenario{s.scenarios.length === 1 ? "" : "s"}
              </span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 3 }}>{s.source}</div>
          </div>
        ))}
      </div>

      {suite && (
        <div className="mt-3" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 8, padding: 12 }}>
          {suite.scenarios.map((sc) => (
            <div key={sc.name} style={{ marginBottom: 6 }}>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>{sc.name}</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.5 }}>{sc.task}</div>
            </div>
          ))}
        </div>
      )}

      <div className="wrapline mt-4" style={{ gap: 10, justifyContent: "space-between" }}>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>
          {suite?.scenarios.length ?? 0} scenarios × {conditions.length} conditions × {repeats} repeats
          {" = "}<b style={{ color: C.text }}>{trials}</b> trials · kernel {catalog.kernel}
        </span>
        <button onClick={runHere} disabled={busy || missingBaseline || !suiteId}
          style={cta(!busy && !missingBaseline && !!suiteId)}>
          {busy ? <><RefreshCw size={14} className="animate-spin" /> running…</> : <><Play size={14} /> Run it</>}
        </button>
      </div>

      {missingBaseline && (
        <div className="wrapline mt-2" style={{ gap: 6 }}>
          <TriangleAlert size={12} color={C.amber} />
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, lineHeight: 1.5 }}>
            the {baseline} baseline is required — an attack-success delta has nothing to be a delta against without it
          </span>
        </div>
      )}

      {error && (
        <div className="mt-3">
          <EmptyState title="the run was refused">{error}</EmptyState>
        </div>
      )}

      {/* ── level 2: adjust ─────────────────────────────────────────────────── */}
      <Disclosure open={level !== "simple"} label="adjust conditions & repeats"
        onToggle={() => setLevel(level === "simple" ? "adjust" : "simple")}>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>conditions</div>
        <div className="wrapline">
          {catalog.conditions.map((c) => {
            const on = conditions.includes(c.id);
            return (
              <button key={c.id} onClick={() => toggleCondition(c.id)}
                style={{ background: on ? "rgba(70,167,88,0.1)" : "none", border: `1px solid ${on ? C.green : C.line}`, borderRadius: 4, color: on ? C.green : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "4px 10px", cursor: "pointer" }}>
                {on ? "✓ " : ""}{c.id}{c.baseline ? " (baseline)" : ""}
              </button>
            );
          })}
        </div>

        <div className="wrapline mt-4" style={{ gap: 10 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 56 }}>repeats</span>
          <input type="range" min={1} max={Math.min(catalog.repeats.max, 40)} value={repeats}
            onChange={(e) => setRepeats(Number(e.target.value))}
            style={{ flex: "1 1 200px", accentColor: C.violet }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, minWidth: 24 }}>{repeats}</span>
        </div>
        {underpowered ? (
          <div className="wrapline mt-2" style={{ gap: 6 }}>
            <TriangleAlert size={12} color={C.amber} />
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, lineHeight: 1.5 }}>
              below {catalog.repeats.min_powered} repeats the paired test has too few discordant pairs to
              conclude, so the result publishes no p-value. The run still works — it just answers less.
            </span>
          </div>
        ) : (
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 8, lineHeight: 1.6 }}>
            The agent is deterministic, so ungoverned and governed on the same seed are a real matched
            pair — that is what makes McNemar's paired test valid here rather than nominal.
          </div>
        )}
      </Disclosure>

      {/* ── level 3: advanced ───────────────────────────────────────────────── */}
      <Disclosure open={level === "advanced"} label="advanced — your own agent, the .axl, the CLI"
        onToggle={() => setLevel(level === "advanced" ? "adjust" : "advanced")}>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, marginBottom: 6 }}>
          Run on your own agent instead
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginBottom: 8, lineHeight: 1.6 }}>
          Lab assigns, the connected runtime executes and pushes traces back. Use this when the point
          is YOUR agent's behaviour — the local run above measures the scripted stand-in.
        </div>
        <div className="wrapline" style={{ gap: 8 }}>
          {(runtimes.data ?? []).map((rt) => (
            <button key={rt.runtime_ref} onClick={() => setRuntimeRef(rt.runtime_ref)}
              style={{ background: runtimeRef === rt.runtime_ref ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${runtimeRef === rt.runtime_ref ? C.steel : C.line}`, borderRadius: 4, color: runtimeRef === rt.runtime_ref ? C.steel : C.mut, fontFamily: MONO, fontSize: 10, padding: "3px 9px", cursor: "pointer" }}>
              {rt.runtime_ref}{rt.model ? ` · ${rt.model}` : ""}
            </button>
          ))}
          <button style={btn({ padding: "3px 9px", fontSize: 10 })} onClick={() => runtimes.refetch()}>
            <RefreshCw size={11} /> refresh
          </button>
          <button onClick={assignToRuntime} disabled={!runtimeRef || busy}
            style={btn({ padding: "4px 10px", fontSize: 10.5 })}>
            <Play size={11} /> assign to runtime
          </button>
        </div>
        {runtimes.isSuccess && (runtimes.data ?? []).length === 0 && (
          <div className="mt-2" style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
            none connected — "bring an agent" → endpoint instrumented
          </div>
        )}

        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, margin: "16px 0 6px" }}>
          The experiment file
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginBottom: 8, lineHeight: 1.6 }}>
          Composed server-side, because the document carries per-condition config hashes that must
          agree with what the kernel computes. It is a plain file: version it, hand-edit it, share it.
        </div>
        <button onClick={showSpec} style={btn({ padding: "4px 10px", fontSize: 10.5 })}>
          <Check size={11} /> show the .axl
        </button>
        {spec && (
          <pre style={{ marginTop: 8, maxHeight: 260, overflow: "auto", background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 9.5, color: C.mut, lineHeight: 1.5 }}>
            {spec}
          </pre>
        )}

        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, margin: "16px 0 6px" }}>
          The same thing from the CLI
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6 }}>
          The CLI is the supplementary path, not the main one. Reach for it when you need what the
          browser must not do — a live model and its hard cost ceiling — or{" "}
          <span style={{ color: C.mut }}>--real-kernel</span>, which repins every condition to the
          installed axor-core so the comparison isolates enforcement rather than a mixed kernel.
          <Cmd>{`axor-lab import-agentdojo ${suiteId || "banking"} --out suite.axl --repeats ${repeats}
axor-lab run suite.axl --out ./bundle --yes --real-kernel`}</Cmd>
        </div>

        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 14, lineHeight: 1.7 }}>
          Multi-agent games (players, federations, trust levels, rounds, faults) are a separate
          experimental track in <span style={{ color: C.mut }}>lab_games</span> — a deterministic toy
          model whose containment is demonstrated, not proven. It is deliberately not offered here:
          the builder parameterizes primitives that exist, it never invents mechanics.
        </div>
      </Disclosure>
    </div>
  );
}
