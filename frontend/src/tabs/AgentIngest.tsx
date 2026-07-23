// "Bring an agent" (lab-agent-ingest mockup): pick how your agent reaches Lab;
// it decides which reproducibility layer you get. Four modes:
//   upload code           — wrap engine (axor-wrap) is still building: honest "coming soon"
//   endpoint instrumented — REAL: POST /runtimes/connect → runtime_ref + ingest_key
//   endpoint black-box    — observe-only proxy harness: coming soon
//   upload traces         — parsed locally in the browser; upload endpoint TODO
import { ChangeEvent, useState } from "react";
import {
  ArrowRight, Check, Copy, FileStack, Globe, Lock, Radio, Terminal, TriangleAlert,
} from "lucide-react";
import { C, MONO, btn, cta, inp } from "../theme";
import { navigate } from "../router";
import { api, Trace } from "../api";
import { useApp } from "../store";
import EmptyState, { Cmd } from "../components/EmptyState";

type ModeId = "code" | "endpoint_instrumented" | "endpoint_blackbox" | "traces";

const MODES: {
  id: ModeId; icon: typeof Radio; label: string; sub: string;
  repro: string; reproColor: string; gives: string; setup: string; privacy: string;
}[] = [
  {
    id: "code", icon: Terminal, label: "Upload code",
    sub: "wrap your agent as an axor-core Invokable",
    repro: "live", reproColor: C.steel,
    gives: "full provenance fidelity — explicit flow tracking inside your agent",
    setup: "uvx axor lab wrap ./agent — your code is untouched + 2 files added",
    privacy: "code runs locally; only observations leave, never raw bodies",
  },
  {
    id: "endpoint_instrumented", icon: Radio, label: "Endpoint — instrumented",
    sub: "the same Axor runtime adapter that serves Control Plane — connect once",
    repro: "runtime", reproColor: C.steel,
    gives: "real governance · provenance · EvidenceCase — the runtime runs your agent and pushes traces",
    setup: "Connect runtime → a scoped ingest key for the adapter, or select an already-connected one",
    privacy: "the runtime executes locally and sends traces outward; Lab never connects to or proxies your agent",
  },
  {
    id: "endpoint_blackbox", icon: Globe, label: "Endpoint — black-box",
    sub: "an HTTP agent endpoint with no instrumentation",
    repro: "observed", reproColor: C.amber,
    gives: "observe-only runs — coarser provenance (boundary observations, no in-agent flow)",
    setup: "paste the endpoint URL; the Lab proxy drives the scenario against it",
    privacy: "requests/responses observed at the boundary only",
  },
  {
    id: "traces", icon: FileStack, label: "Upload traces / an incident",
    sub: "a production incident or someone's published run",
    repro: "replay", reproColor: C.green,
    gives: "reproduce governance verdicts bit-identical · turn an incident into a regression",
    setup: "drop an axor-core trace bundle — nothing to wrap",
    privacy: "observations only, never raw bodies",
  },
];

function Row({ k, v, icon: Icon }: { k: string; v: string; icon?: typeof Lock }) {
  return (
    <div className="flex items-start gap-2">
      {Icon
        ? <Icon size={11} color={C.dim} style={{ marginTop: 2, flexShrink: 0 }} />
        : <span style={{ width: 44, flexShrink: 0, fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{k}</span>}
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.5 }}>
        {Icon && <span style={{ color: C.dim }}>{k}: </span>}{v}
      </span>
    </div>
  );
}

function ComingSoon({ what }: { what: string }) {
  return (
    <div className="mt-4 p-3 flex items-start gap-2" style={{ background: C.panel, border: `1px dashed ${C.amber}`, borderRadius: 8 }}>
      <TriangleAlert size={13} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.6 }}>
        {what} — <b style={{ color: C.amber }}>coming soon</b>. Meanwhile, "endpoint — instrumented"
        connects a real runtime today, and "upload traces" replays a recorded run.
      </span>
    </div>
  );
}

// ── the real connect flow ───────────────────────────────────────────────────
function ConnectRuntime() {
  const { controlToken, setControlToken, setRuntimeRef } = useApp();
  const [model, setModel] = useState("");
  const [agentRef, setAgentRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ runtime_ref: string; ingest_key: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const connect = async () => {
    setBusy(true); setError(null);
    try {
      const res = await api.connectRuntime(model, agentRef || undefined);
      setResult(res);
      setRuntimeRef(res.runtime_ref);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <div className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.green}`, borderRadius: 10 }}>
        <div className="flex items-center gap-2 mb-3">
          <Check size={15} color={C.green} />
          <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>runtime connected</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 2 }}>
          <div>runtime_ref <span style={{ color: C.text }}>{result.runtime_ref}</span></div>
          <div className="wrapline" style={{ gap: 8 }}>
            <span>ingest_key <span style={{ color: C.steel }}>{result.ingest_key}</span></span>
            <button style={btn({ padding: "2px 8px", fontSize: 10 })}
              onClick={() => { navigator.clipboard.writeText(result.ingest_key); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>
              {copied ? <Check size={11} color={C.green} /> : <Copy size={11} />} {copied ? "copied" : "copy"}
            </button>
          </div>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.amber, marginTop: 6 }}>
          shown once — give the ingest_key to your runtime adapter; it authenticates the runtime-facing endpoints
        </div>
        <div className="wrapline mt-3">
          <button onClick={() => navigate("builder")} style={cta(true)}>
            Compose an experiment on it <ArrowRight size={13} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em", marginBottom: 10 }}>
        CONNECT A RUNTIME — POST /runtimes/connect
      </div>
      <div className="flex flex-col gap-2">
        <label className="wrapline" style={{ gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 90 }}>model</span>
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. o4-mini"
            style={{ ...inp, flex: "1 1 180px" }} />
        </label>
        <label className="wrapline" style={{ gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 90 }}>agent_ref</span>
          <input value={agentRef} onChange={(e) => setAgentRef(e.target.value)} placeholder="optional — your agent's name/ref"
            style={{ ...inp, flex: "1 1 180px" }} />
        </label>
        <label className="wrapline" style={{ gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 90 }}>control token</span>
          <input value={controlToken} onChange={(e) => setControlToken(e.target.value)} placeholder="only if the jobs server is token-gated"
            style={{ ...inp, flex: "1 1 180px" }} />
        </label>
      </div>
      <div className="wrapline mt-3" style={{ gap: 10 }}>
        <button onClick={connect} disabled={busy} style={cta(!busy)}>
          {busy ? "connecting…" : "Connect runtime"} <ArrowRight size={13} />
        </button>
        <span className="wrapline" style={{ fontFamily: MONO, fontSize: 10, color: C.dim, gap: 6 }}>
          <Terminal size={11} /> or local: <span style={{ color: C.mut }}>uvx axor lab wrap ./agent</span>
        </span>
      </div>
      {error && (
        <div className="mt-3">
          <EmptyState title="connect failed">
            {error}. Is the runtime-jobs server up?
            <Cmd>python -m lab_server --root ./lab-store --runtime-port 8010</Cmd>
          </EmptyState>
        </div>
      )}
    </div>
  );
}

// ── local trace import ──────────────────────────────────────────────────────
interface TraceSummary {
  traces: number; events: number; gateDecisions: number; denies: number;
  scenarios: string[]; conditions: string[];
}

function summarize(parsed: unknown): TraceSummary {
  // accept: one trace, an array of traces, {traces: [...]}, or a full
  // reproduction package — all shapes the ecosystem produces
  let traces: Trace[] = [];
  if (Array.isArray(parsed)) traces = parsed as Trace[];
  else if (parsed && typeof parsed === "object") {
    const obj = parsed as Record<string, unknown>;
    if (Array.isArray(obj.traces)) traces = obj.traces as Trace[];
    else if (Array.isArray(obj.events)) traces = [parsed as Trace];
  }
  if (!traces.length) throw new Error("no traces found — expected trace/v1 objects, a {traces:[…]} body, or a reproduction package");
  let events = 0, gates = 0, denies = 0;
  const scenarios = new Set<string>(), conditions = new Set<string>();
  for (const t of traces) {
    events += t.events?.length ?? 0;
    for (const e of t.events ?? []) {
      if (e.type === "gate_decision") {
        gates += 1;
        if (e.decision?.verdict === "DENY") denies += 1;
      }
    }
    if (t.trial?.scenario_id) scenarios.add(t.trial.scenario_id);
    if (t.trial?.condition_id) conditions.add(t.trial.condition_id);
  }
  return {
    traces: traces.length, events, gateDecisions: gates, denies,
    scenarios: [...scenarios], conditions: [...conditions],
  };
}

function TraceImport() {
  const [summary, setSummary] = useState<TraceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name); setSummary(null); setError(null);
    file.text()
      .then((text) => setSummary(summarize(JSON.parse(text))))
      .catch((err) => setError(String(err instanceof Error ? err.message : err)));
  };

  return (
    <div className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em", marginBottom: 10 }}>
        IMPORT TRACES — parsed locally in your browser
      </div>
      <input type="file" accept=".json,application/json" onChange={onFile}
        style={{ fontFamily: MONO, fontSize: 11, color: C.mut }} />
      {error && (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.red, marginTop: 10 }}>
          {fileName}: {error}
        </div>
      )}
      {summary && (
        <div className="mt-3 p-3" style={{ background: C.panel2, border: `1px solid ${C.green}`, borderRadius: 8 }}>
          <div className="flex items-center gap-2 mb-2">
            <Check size={13} color={C.green} />
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>{fileName} — traces loaded</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.9, paddingLeft: 21 }}>
            <div>traces <b style={{ color: C.text }}>{summary.traces}</b> · events <b style={{ color: C.text }}>{summary.events}</b></div>
            <div>gate decisions <b style={{ color: C.text }}>{summary.gateDecisions}</b> · DENY <b style={{ color: summary.denies ? C.green : C.mut }}>{summary.denies}</b></div>
            {summary.scenarios.length > 0 && <div>scenarios: {summary.scenarios.join(", ")}</div>}
            {summary.conditions.length > 0 && <div>conditions: {summary.conditions.join(", ")}</div>}
          </div>
        </div>
      )}
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 10, lineHeight: 1.6 }}>
        TODO: server-side trace ingestion (upload → replay → EvidenceCase) is not wired yet — this
        summary is computed locally and nothing leaves the browser. Use{" "}
        <span style={{ color: C.mut }}>axor-lab replay ./bundle</span> for exact replay today.
      </div>
    </div>
  );
}

export default function AgentIngest() {
  const [mode, setMode] = useState<ModeId | null>(null);

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <h1 style={{ fontSize: 23, fontWeight: 680, lineHeight: 1.25, margin: "0 0 6px" }}>
        Bring your agent. Run experiments on it under governance.
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 24 }}>
        No Axor deployment needed — this is standalone. Pick how your agent gets here; it decides which
        reproducibility layer you get.
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.05em", marginBottom: 8, lineHeight: 1.6 }}>
        HOW YOUR AGENT REACHES LAB — Lab reads the shared Axor trace fabric; it never connects to or
        proxies your agent. Connect a runtime once; Control Plane sees the same one.
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
                  <Row k="setup" v={x.setup} />
                  <Row k="privacy" v={x.privacy} icon={Lock} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {mode === "code" && <ComingSoon what="the wrap engine (axor-wrap) that turns your code into an axor-core Invokable" />}
      {mode === "endpoint_blackbox" && <ComingSoon what="the black-box endpoint proxy harness" />}
      {mode === "endpoint_instrumented" && <ConnectRuntime />}
      {mode === "traces" && <TraceImport />}

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 20, lineHeight: 1.7 }}>
        Lab is the experiment & evidence layer over Axor runtime traces — it reads the <b style={{ color: C.steel }}>shared trace fabric</b>,
        it does not connect to, execute, or proxy your agent. How you bring your agent sets the reproducibility you get:{" "}
        <b style={{ color: C.steel }}>code/endpoint → live</b> (stochastic, reported with CI),{" "}
        <b style={{ color: C.green }}>traces → replay</b> (governance verdicts, bit-identical). Code never has to leave
        your machine — the local path is first-class.
      </div>
    </div>
  );
}
