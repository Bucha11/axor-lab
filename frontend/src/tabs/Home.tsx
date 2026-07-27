// The playground. This is the product.
//
// What this page is NOT, after two wrong turns. It is not a control panel over
// the endpoints (seven entry cards, a four-step wizard, a Run button in front of
// the first number). And it is not a governance pitch: the previous version led
// with "what does governance cost, and what does it buy?", which put the add-on
// where the product goes. Governance is something you can ATTACH to a run here.
// You can also never attach it, run your own agent against attack scenarios, and
// never touch a Control Plane, an account, or an Axor deployment.
//
// So the page is built on the three axes of an experiment, in the order they
// belong to the user:
//
//   1. the agent    — whose behaviour is being watched. THIS is the subject.
//   2. the tasks    — what it is asked to do, and what is trying to hijack it.
//   3. governance   — optional, off by default, an extra arm to compare against.
//
// Leaving governance off is a first-class run, not a degenerate one: `run_mode`
// executes the ungoverned condition alone, and the result is your agent's own
// attack-success and task-success rate with no gate anywhere in it. That is the
// whole point of a playground — see what the thing does.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Play, RefreshCw, TriangleAlert } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { navigate } from "../router";
import { api, type Aggregate, type Catalog } from "../api";
import { useApp } from "../store";
import EmptyState, { Cmd } from "../components/EmptyState";

export default function Home() {
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: api.catalog });
  if (catalog.isLoading) {
    return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading the playground…</div>;
  }
  if (catalog.isError || !catalog.data) {
    return (
      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <EmptyState title="the run API is not answering">
          One command serves the UI, the catalog and the runs:
          <Cmd>axor-lab serve</Cmd>
        </EmptyState>
      </div>
    );
  }
  return <Playground catalog={catalog.data} />;
}

/** Where an agent comes from. The first axis, and the one the product is about.
 *  `runnable` means this page can start the run itself; the rest hand off to the
 *  surface that owns the setup, rather than pretending to accept it here. */
type AgentSource = {
  id: string; label: string; detail: string; runnable?: boolean; route?: string;
};

const AGENTS: AgentSource[] = [
  {
    id: "bundled",
    label: "the bundled stand-in",
    detail: "deterministic, offline, free — runs here in under a second",
    runnable: true,
  },
  {
    id: "runtime",
    label: "your agent, connected",
    detail: "Lab assigns the trials, your runtime executes them and pushes traces back",
    runnable: true,
  },
  {
    id: "live",
    label: "a live model",
    detail: "your key, your spend — the one run whose ungoverned arm is a real model",
    route: "models",
  },
  {
    id: "byo",
    label: "your code, or traces you already have",
    detail: "we derive the tool manifests from the code, or read the run you already did",
    route: "agent-ingest",
  },
];

/** One line of a numbered axis: a heading, why it matters, then the controls. */
function Axis({ n, title, sub, children, accent }: {
  n: number; title: string; sub: string; children: React.ReactNode; accent?: string;
}) {
  return (
    <div className="p-4 mb-3" style={{
      background: C.panel, border: `1px solid ${accent ?? C.line}`, borderRadius: 10,
    }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: C.text }}>
        <span style={{ color: C.dim }}>{n} · </span>{title}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.65, margin: "5px 0 12px" }}>
        {sub}
      </div>
      {children}
    </div>
  );
}

function Radio({ on, label, detail, onClick, right }: {
  on: boolean; label: string; detail: string; onClick: () => void; right?: React.ReactNode;
}) {
  return (
    <div onClick={onClick} className="wrapline" style={{
      gap: 10, cursor: "pointer", padding: "7px 2px", justifyContent: "space-between",
    }}>
      <span className="wrapline" style={{ gap: 9, flex: "1 1 320px", alignItems: "flex-start" }}>
        <input type="radio" checked={on} readOnly tabIndex={-1} style={{ marginTop: 2 }} />
        <span>
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: on ? C.text : C.mut }}>{label}</span>
          <span style={{ display: "block", fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.55 }}>
            {detail}
          </span>
        </span>
      </span>
      {right}
    </div>
  );
}

function Playground({ catalog }: { catalog: Catalog }) {
  const { runtimeRef, setRuntimeRef, setLastRun } = useApp();
  const baseline = catalog.conditions.find((c) => c.baseline)?.id ?? "ungoverned";
  const governedId = catalog.conditions.find((c) => !c.baseline)?.id ?? "governed";

  const [agent, setAgent] = useState<string>("bundled");
  const [suiteId, setSuiteId] = useState(catalog.suites[0]?.id ?? "");
  const [repeats, setRepeats] = useState(catalog.repeats.default);
  const [governed, setGoverned] = useState(false);
  const [realKernel, setRealKernel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ runId: string; aggregates: Aggregate[] } | null>(null);

  const runtimes = useQuery({
    queryKey: ["runtimes"], queryFn: api.listRuntimes, enabled: agent === "runtime",
  });

  const suite = catalog.suites.find((s) => s.id === suiteId);
  const arms = governed ? 2 : 1;
  const trials = (suite?.scenarios.length ?? 0) * arms * repeats;
  const underpowered = governed && repeats < catalog.repeats.min_powered;
  const chosen = AGENTS.find((a) => a.id === agent)!;

  // The spec, exactly as it goes over the wire. Governance off is `run_mode:
  // ungoverned` — the conditions stay declared, only the baseline executes.
  const spec = {
    suite: suiteId,
    conditions: [baseline, governedId],
    repeats,
    run_mode: (governed ? "compare" : "ungoverned") as "compare" | "ungoverned",
    ...(governed && realKernel ? { real_kernel: true } : {}),
  };

  const run = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      if (agent === "runtime") {
        if (!runtimeRef) throw new Error("pick a connected runtime first");
        const composed = await api.composeExperiment(spec);
        const experiment = (composed.document as { experiment: Record<string, unknown> }).experiment;
        const created = await api.createRun(
          runtimeRef, experiment, composed.planned_trials,
          composed.estimate as unknown as Record<string, unknown>,
        );
        setLastRun(created.run_id);
        navigate(`runs/${created.run_id}`);
        return;
      }
      const landed = await api.runComposed(spec);
      setLastRun(landed.run_id);
      const results = await api.runResults(landed.run_id);
      setResult({ runId: landed.run_id, aggregates: results.aggregates });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 760, margin: "0 auto" }}>
      <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
        A playground for agent experiments.
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.75, marginBottom: 22, maxWidth: 610 }}>
        Put an agent in front of tasks that are trying to hijack it, and watch what it does.
        Everything runs here — no deployment, no account, no Control Plane, ever. Governance is
        an <b style={{ color: C.text }}>optional</b> second arm: add it to see what a gate would
        have changed, or leave it off and just measure your agent.
      </div>

      <Axis
        n={1}
        title="Whose agent"
        sub="The subject of the experiment. Everything else is what you point it at."
      >
        {AGENTS.map((a) => (
          <Radio
            key={a.id}
            on={agent === a.id}
            label={a.label}
            detail={a.detail}
            onClick={() => setAgent(a.id)}
            right={
              a.id === "bundled" ? (
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{catalog.agent.ref}</span>
              ) : a.id === "runtime" ? (
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: (runtimes.data?.length ?? 0) ? C.green : C.dim }}>
                  {agent === "runtime" ? `${runtimes.data?.length ?? 0} connected` : ""}
                </span>
              ) : null
            }
          />
        ))}

        {agent === "runtime" && (
          <div className="wrapline mt-2" style={{ gap: 8, paddingLeft: 24 }}>
            {(runtimes.data ?? []).map((rt) => (
              <button
                key={rt.runtime_ref}
                onClick={() => setRuntimeRef(rt.runtime_ref)}
                style={{
                  background: runtimeRef === rt.runtime_ref ? "rgba(127,168,204,0.12)" : "none",
                  border: `1px solid ${runtimeRef === rt.runtime_ref ? C.steel : C.line}`,
                  borderRadius: 4, color: runtimeRef === rt.runtime_ref ? C.steel : C.mut,
                  fontFamily: MONO, fontSize: 10, padding: "3px 9px", cursor: "pointer",
                }}
              >
                {rt.runtime_ref}{rt.model ? ` · ${rt.model}` : ""}
              </button>
            ))}
            {runtimes.isSuccess && (runtimes.data ?? []).length === 0 && (
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6 }}>
                none connected yet — point your runtime at this server and it appears here
                <button
                  onClick={() => navigate("agent-ingest")}
                  style={{ background: "none", border: "none", color: C.steel, fontFamily: MONO, fontSize: 10, cursor: "pointer" }}
                >
                  how →
                </button>
              </span>
            )}
            <button
              onClick={() => runtimes.refetch()}
              style={{ background: "none", border: `1px solid ${C.line}`, borderRadius: 4, color: C.mut, fontFamily: MONO, fontSize: 10, padding: "3px 8px", cursor: "pointer" }}
            >
              <RefreshCw size={10} style={{ verticalAlign: -1 }} /> refresh
            </button>
          </div>
        )}
      </Axis>

      <Axis
        n={2}
        title="What it is asked to do"
        sub="Each scenario is an ordinary task with an injection hidden in the data the agent
             reads. The task is real work; the injection is the attacker's attempt to redirect it."
      >
        <div className="wrapline" style={{ gap: 8, marginBottom: 10 }}>
          {catalog.suites.map((s) => (
            <button
              key={s.id}
              onClick={() => setSuiteId(s.id)}
              style={{
                background: suiteId === s.id ? "rgba(155,140,204,0.10)" : "none",
                border: `1px solid ${suiteId === s.id ? C.violet : C.line}`, borderRadius: 6,
                color: suiteId === s.id ? C.text : C.mut, fontFamily: MONO, fontSize: 11,
                padding: "5px 11px", cursor: "pointer",
              }}
            >
              {s.label} · {s.scenarios.length}
            </button>
          ))}
        </div>
        {suite?.scenarios.map((sc) => (
          <div key={sc.name} style={{ marginBottom: 7 }}>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>{sc.name}</div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.5 }}>{sc.task}</div>
          </div>
        ))}
        <div className="wrapline mt-3" style={{ gap: 10 }}>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, minWidth: 52 }}>repeats</span>
          <input
            type="range" min={1} max={Math.min(catalog.repeats.max, 40)} value={repeats}
            onChange={(e) => setRepeats(Number(e.target.value))}
            style={{ flex: "1 1 180px", accentColor: C.violet }}
          />
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, minWidth: 20 }}>{repeats}</span>
        </div>
      </Axis>

      <Axis
        n={3}
        title="Governance — optional"
        accent={governed ? C.violet : undefined}
        sub="Off, this run has no gate in it anywhere: you get your agent's own attack-success and
             task-success rate, which is the honest baseline and often the only thing you wanted.
             On, the same trials run a second time under a gate, on the same seeds, so the two arms
             are a real matched pair."
      >
        <label className="wrapline" style={{ gap: 8, cursor: "pointer" }}>
          <input type="checkbox" checked={governed} onChange={(e) => setGoverned(e.target.checked)} />
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: governed ? C.text : C.mut }}>
            add a governed arm
          </span>
        </label>
        {governed && (
          <div style={{ marginLeft: 24, marginTop: 8 }}>
            <label className="wrapline" style={{ gap: 8, cursor: catalog.real_kernel?.available ? "pointer" : "default" }}>
              <input
                type="checkbox" checked={realKernel} disabled={!catalog.real_kernel?.available}
                onChange={(e) => setRealKernel(e.target.checked)}
              />
              <span style={{ fontFamily: MONO, fontSize: 11, color: realKernel ? C.text : C.mut }}>
                use the production kernel
                <span style={{ color: C.dim }}>
                  {" "}({catalog.real_kernel?.available ? catalog.real_kernel.version : "axor-core not installed"})
                </span>
              </span>
            </label>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6, marginTop: 6 }}>
              Off, the gate is the stdlib reference kernel that ships with Lab — enough to see the
              mechanism, and it needs nothing installed. Both arms are repinned together, so the
              comparison isolates enforcement rather than mixing in a kernel change.
            </div>
            <button
              onClick={() => navigate("governance")}
              style={{ background: "none", border: "none", padding: "10px 0 0", cursor: "pointer", color: C.violet, fontFamily: MONO, fontSize: 10.5 }}
            >
              what a gate costs across a whole published benchmark <ArrowRight size={10} style={{ verticalAlign: -1 }} />
            </button>
          </div>
        )}
      </Axis>

      <div className="wrapline" style={{ gap: 12, marginTop: 4 }}>
        {chosen.runnable ? (
          <button onClick={run} disabled={busy || !suiteId} style={cta(!busy && !!suiteId)}>
            {busy
              ? <><RefreshCw size={14} className="animate-spin" /> running…</>
              : <><Play size={14} /> Run {trials} trials</>}
          </button>
        ) : (
          <button onClick={() => navigate(chosen.route!)} style={cta(true)}>
            {chosen.label} <ArrowRight size={13} />
          </button>
        )}
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6 }}>
          {suite?.scenarios.length ?? 0} scenarios × {arms} arm{arms === 1 ? "" : "s"} × {repeats} repeats
          {chosen.runnable && agent === "bundled" && " · executes in this server, offline"}
        </span>
      </div>

      {underpowered && (
        <div className="wrapline mt-2" style={{ gap: 6 }}>
          <TriangleAlert size={12} color={C.amber} />
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber, lineHeight: 1.55 }}>
            below {catalog.repeats.min_powered} repeats the paired test has too few discordant pairs,
            so the two arms get no p-value. The run still works — it answers less.
          </span>
        </div>
      )}

      {error && (
        <div className="mt-3 p-3" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red, lineHeight: 1.6 }}>
          {error}
        </div>
      )}

      {result && <ResultStrip runId={result.runId} aggregates={result.aggregates} governed={governed} />}

      {/* the ways out of the three axes, for someone who has outgrown them. An
          entry point linked from nowhere is one that does not exist, and the
          builder holds the .axl, the file upload and the CLI equivalent. */}
      <div className="wrapline" style={{ gap: 16, marginTop: 26, paddingTop: 16, borderTop: `1px solid ${C.line}` }}>
        {[
          { to: "builder", label: "the experiment file, an .axl you wrote, the CLI" },
          { to: "scenario-author", label: "write your own scenario" },
          { to: "import", label: "reproduce a production incident" },
        ].map((l) => (
          <button
            key={l.to}
            onClick={() => navigate(l.to)}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.mut, fontFamily: MONO, fontSize: 10.5 }}
          >
            {l.label} <ArrowRight size={10} style={{ verticalAlign: -1 }} />
          </button>
        ))}
      </div>
    </div>
  );
}

/** What just happened, in the two rates that matter, per arm. */
function ResultStrip({ runId, aggregates, governed }: {
  runId: string; aggregates: Aggregate[]; governed: boolean;
}) {
  const arms = [...new Set(aggregates.map((a) => a.condition_id))];
  const rate = (arm: string, metric: string) =>
    aggregates.find((a) => a.condition_id === arm && a.metric === metric);
  return (
    <div className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.green}`, borderRadius: 10 }}>
      <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600 }}>
          {governed ? "Two arms, same seeds" : "Your agent, ungoverned"}
        </span>
        <button
          onClick={() => navigate(`results/${runId}`)}
          style={{ background: "none", border: "none", cursor: "pointer", color: C.violet, fontFamily: MONO, fontSize: 10.5 }}
        >
          every trial, trace and verdict <ArrowRight size={10} style={{ verticalAlign: -1 }} />
        </button>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11 }}>
          <thead>
            <tr style={{ color: C.mut, textAlign: "left" }}>
              <th style={{ padding: "4px 8px" }}>arm</th>
              <th style={{ padding: "4px 8px" }}>attack succeeded</th>
              <th style={{ padding: "4px 8px" }}>task completed</th>
            </tr>
          </thead>
          <tbody>
            {arms.map((arm) => {
              const asr = rate(arm, "ASR");
              const util = rate(arm, "task_success_rate");
              return (
                <tr key={arm} style={{ borderTop: `1px solid ${C.line}` }}>
                  <td style={{ padding: "8px", color: C.text }}>{arm}</td>
                  <td style={{ padding: "8px" }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: (asr?.estimate ?? 0) > 0 ? C.amber : C.green }}>
                      {asr ? `${(100 * asr.estimate).toFixed(0)}%` : "—"}
                    </span>
                    <span style={{ fontSize: 9.5, color: C.dim }}> of {asr?.n ?? 0}</span>
                  </td>
                  <td style={{ padding: "8px" }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>
                      {util ? `${(100 * util.estimate).toFixed(0)}%` : "—"}
                    </span>
                    <span style={{ fontSize: 9.5, color: C.dim }}> of {util?.n ?? 0}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.65, marginTop: 10 }}>
        {governed
          ? "Both arms ran the same scenarios on the same seeds, so the difference is the gate and nothing else."
          : "One arm, so nothing here is a comparison — this is what your agent does when nothing stops it."}
      </div>
    </div>
  );
}
