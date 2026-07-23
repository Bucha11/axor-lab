// "Bring an agent" (lab-agent-ingest mockup): pick how your agent reaches Lab;
// it decides which reproducibility layer you get. Four modes:
//   upload code           — REAL: POST /wrap/scan → review guesses → /wrap/manifests
//                           (axor-wrap engine behind the jobs server)
//   endpoint instrumented — REAL: POST /runtimes/connect → runtime_ref + ingest_key
//   endpoint black-box    — observe-only proxy harness: coming soon
//   upload traces         — parsed locally in the browser; upload endpoint TODO
import { ChangeEvent, useState } from "react";
import {
  ArrowRight, Check, Copy, Download, FileStack, Globe, Lock, Radio, Terminal,
  TriangleAlert, Upload,
} from "lucide-react";
import { C, MONO, btn, cta, inp } from "../theme";
import { navigate } from "../router";
import {
  api, EffectClass, Trace, WrapDetectedTool, WrapManifestsResult,
} from "../api";
import { useApp } from "../store";
import EmptyState, { Cmd } from "../components/EmptyState";

type ModeId = "code" | "endpoint_instrumented" | "endpoint_blackbox" | "traces";

const MODES: {
  id: ModeId; icon: typeof Radio; label: string; sub: string;
  repro: string; reproColor: string; gives: string; setup: string; privacy: string;
}[] = [
  {
    id: "code", icon: Terminal, label: "Upload code",
    sub: "scan your agent's tools, review effect classes, get manifests + governance",
    repro: "live", reproColor: C.steel,
    gives: "tool-manifest/v1 per detected tool + a GovernanceConfig YAML — the wrap engine (axor-wrap) does the static analysis, you make the final classification",
    setup: "pick your agent's .py files → review each tool's guessed class → Build manifests",
    privacy: "sources are scanned statically (AST only) in a temp dir server-side — never imported or executed",
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

// ── the real wrap flow (upload code → scan → classify → manifests) ──────────

// class → color, same coding as the mockups: READ green, WRITE amber,
// EXPORT/EXEC red (egress), UNKNOWN amber "?" until a human classifies it.
const CLASS_COLOR: Record<string, string> = {
  READ: C.green, WRITE: C.amber, EXPORT: C.red, EXEC: C.red, UNKNOWN: C.amber,
};
const EFFECT_CLASSES: EffectClass[] = ["READ", "WRITE", "EXPORT", "EXEC"];

// one scanned tool + the human's (pending) review decision
type ToolRow = WrapDetectedTool & {
  chosen: EffectClass | ""; // "" ⇔ still UNKNOWN — must be classified by hand
  driving: string[];
  untrusted: string[];
};

function ClassSelect({ value, onChange }: {
  value: EffectClass | ""; onChange: (c: EffectClass) => void;
}) {
  const color = value ? CLASS_COLOR[value] : C.amber;
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as EffectClass)}
      style={{
        background: C.bg, border: `1px solid ${color}`, borderRadius: 4, color,
        fontFamily: MONO, fontSize: 10.5, fontWeight: 700, padding: "3px 6px",
        outline: "none", cursor: "pointer",
      }}>
      {value === "" && <option value="" disabled>? classify</option>}
      {EFFECT_CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}

// editable chips (driving_args / untrusted_fields): × removes, Enter adds
function Chips({ label, values, onChange, placeholder }: {
  label: string; values: string[]; onChange: (v: string[]) => void; placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setDraft("");
  };
  return (
    <div className="wrapline" style={{ gap: 5 }}>
      <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, minWidth: 96 }}>{label}</span>
      {values.map((v) => (
        <span key={v} className="flex items-center" style={{
          fontFamily: MONO, fontSize: 10, color: C.text, background: C.panel2,
          border: `1px solid ${C.line}`, borderRadius: 10, padding: "1px 4px 1px 8px", gap: 3,
        }}>
          {v}
          <button onClick={() => onChange(values.filter((x) => x !== v))}
            title={`remove ${v}`}
            style={{ background: "none", border: "none", color: C.dim, cursor: "pointer", fontFamily: MONO, fontSize: 11, padding: "0 3px" }}>
            ×
          </button>
        </span>
      ))}
      <input value={draft} placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }}
        onBlur={add}
        style={{ ...inp, fontSize: 10, padding: "2px 7px", width: 110 }} />
    </div>
  );
}

function downloadBlob(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function WrapResult({ result, goInstrumented }: {
  result: WrapManifestsResult; goInstrumented: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const manifestsJson = JSON.stringify(result.manifests, null, 2);
  return (
    <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.green}`, borderRadius: 10 }}>
      <div className="flex items-center gap-2 mb-2">
        <Check size={15} color={C.green} />
        <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>
          {result.wrap.tools} manifest{result.wrap.tools === 1 ? "" : "s"} built — {result.wrap.generated_by}
        </span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.8, marginBottom: 10 }}>
        egress sinks{" "}
        <b style={{ color: result.wrap.egress_sinks.length ? C.red : C.mut }}>
          {result.wrap.egress_sinks.join(", ") || "none"}
        </b>
        {" · "}untrusted sources{" "}
        <b style={{ color: result.wrap.untrusted_sources.length ? C.amber : C.mut }}>
          {result.wrap.untrusted_sources.join(", ") || "none"}
        </b>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.05em", margin: "10px 0 6px" }}>
        TOOL MANIFESTS — tool-manifest/v1, validated
      </div>
      {result.manifests.map((m, i) => (
        <details key={i} style={{ marginBottom: 4 }}>
          <summary style={{ fontFamily: MONO, fontSize: 11, color: C.text, cursor: "pointer", padding: "3px 0" }}>
            {String(m.id)}{" "}
            <span style={{ color: CLASS_COLOR[String((m.effect as Record<string, unknown>)?.default_class)] ?? C.mut }}>
              {String((m.effect as Record<string, unknown>)?.default_class)}
            </span>
          </summary>
          <pre style={{
            margin: "4px 0 8px", background: C.panel2, border: `1px solid ${C.line}`,
            borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 9.5,
            color: C.mut, overflow: "auto", lineHeight: 1.5, maxHeight: 260,
          }}>{JSON.stringify(m, null, 2)}</pre>
        </details>
      ))}

      <div className="wrapline" style={{ justifyContent: "space-between", margin: "12px 0 6px" }}>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.05em" }}>
          GOVERNANCE.YAML — GovernanceConfig-loadable
        </span>
        <button style={btn({ padding: "2px 8px", fontSize: 10 })}
          onClick={() => {
            navigator.clipboard.writeText(result.governance_yaml);
            setCopied(true); setTimeout(() => setCopied(false), 1400);
          }}>
          {copied ? <Check size={11} color={C.green} /> : <Copy size={11} />} {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre style={{
        margin: 0, background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6,
        padding: 10, fontFamily: MONO, fontSize: 9.5, color: C.mut, overflow: "auto",
        lineHeight: 1.5, maxHeight: 220,
      }}>{result.governance_yaml}</pre>

      <div className="wrapline mt-3" style={{ gap: 8 }}>
        <button style={btn()} onClick={() => downloadBlob("manifests.json", manifestsJson, "application/json")}>
          <Download size={12} /> manifests.json
        </button>
        <button style={btn()} onClick={() => downloadBlob("governance.yaml", result.governance_yaml, "text/yaml")}>
          <Download size={12} /> governance.yaml
        </button>
      </div>

      <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${C.line}` }}>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7, marginBottom: 8 }}>
          Next: run your agent under these manifests with the wrapped runtime
          (<span style={{ color: C.steel }}>axor-wrap connect-lab</span> or{" "}
          <span style={{ color: C.steel }}>WrappedToolset</span>), then connect it —
          Lab assigns experiments, the runtime executes and pushes traces.
        </div>
        <button onClick={goInstrumented} style={cta(true)}>
          Connect the wrapped runtime — endpoint instrumented <ArrowRight size={13} />
        </button>
      </div>
    </div>
  );
}

function WrapCode({ goInstrumented }: { goInstrumented: () => void }) {
  const { controlToken, setControlToken } = useApp();
  const [rows, setRows] = useState<ToolRow[] | null>(null);
  const [scannedFiles, setScannedFiles] = useState<string[]>([]);
  const [result, setResult] = useState<WrapManifestsResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notInstalled, setNotInstalled] = useState(false);

  const fail = (err: unknown) => {
    const msg = String(err instanceof Error ? err.message : err);
    if (msg.startsWith("501")) setNotInstalled(true);
    else setError(msg);
  };

  const onFiles = async (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-picking the same files
    const pyFiles = picked.filter((f) => f.name.endsWith(".py"));
    if (!pyFiles.length) {
      setError(picked.length ? "no .py files in the selection — the scanner reads Python sources only" : null);
      return;
    }
    setBusy(true); setError(null); setNotInstalled(false); setResult(null); setRows(null);
    try {
      const files = await Promise.all(pyFiles.map(async (f) => ({
        path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name,
        content: await f.text(),
      })));
      const tools = await api.wrapScan(files);
      setScannedFiles(files.map((x) => x.path));
      setRows(tools.map((t) => ({
        ...t,
        chosen: t.guess.default_class === "UNKNOWN" ? "" : t.guess.default_class,
        driving: [...t.guess.driving_args],
        untrusted: [...t.guess.untrusted_fields],
      })));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const patchRow = (i: number, patch: Partial<ToolRow>) =>
    setRows((prev) => prev && prev.map((r, k) => (k === i ? { ...r, ...patch } : r)));

  const unknownLeft = rows?.filter((r) => r.chosen === "").length ?? 0;
  const canBuild = !!rows && rows.length > 0 && unknownLeft === 0 && !busy;

  const build = async () => {
    if (!rows || !canBuild) return;
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await api.wrapManifests(rows.map((r) => ({
        id: r.id, source: r.source, description: r.description,
        args_schema: r.args_schema, framework: r.framework,
        schema_confidence: r.schema_confidence,
        effect: {
          default_class: r.chosen as EffectClass,
          driving_args: r.driving,
          untrusted_fields: r.untrusted,
          sensitive_fields: [], // field-level sensitivity stays manual work
        },
      }))));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-4 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em", marginBottom: 10 }}>
        UPLOAD AGENT CODE — POST /wrap/scan (static AST scan; nothing is executed)
      </div>
      <div className="flex flex-col gap-2">
        <label className="wrapline" style={{ gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 90 }}>agent .py files</span>
          <input type="file" multiple accept=".py" onChange={onFiles}
            style={{ fontFamily: MONO, fontSize: 11, color: C.mut }} />
        </label>
        <label className="wrapline" style={{ gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 90 }}>control token</span>
          <input value={controlToken} onChange={(e) => setControlToken(e.target.value)}
            placeholder="only if the jobs server is token-gated"
            style={{ ...inp, flex: "1 1 180px" }} />
        </label>
      </div>
      {busy && !rows && (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 10 }}>scanning…</div>
      )}

      {notInstalled && (
        <div className="mt-3">
          <EmptyState title="the wrap engine is not installed on the server">
            /wrap/scan answered 501 — the axor-wrap package is missing where the
            runtime-jobs server runs. Install it next to lab_server and retry:
            <Cmd>pip install axor-wrap{"\n"}# or: pip install axor-lab[wrap]</Cmd>
          </EmptyState>
        </div>
      )}
      {error && (
        <div className="mt-3">
          <EmptyState title="wrap request failed">
            {error}. Is the runtime-jobs server up?
            <Cmd>python -m lab_server --root ./lab-store --runtime-port 8010</Cmd>
          </EmptyState>
        </div>
      )}

      {rows && rows.length === 0 && (
        <div className="mt-3">
          <EmptyState title="no tools detected">
            The scanner found no @tool / StructuredTool / FastMCP / registry /
            subprocess patterns in {scannedFiles.join(", ")}. Dynamically
            registered tools are out of static reach.
          </EmptyState>
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="mt-3">
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.05em", marginBottom: 6 }}>
            DETECTED TOOLS — {rows.length} in {scannedFiles.length} file{scannedFiles.length === 1 ? "" : "s"} ·
            review every guess; <span style={{ color: C.amber }}>UNKNOWN</span> needs a human decision
          </div>
          <div className="flex flex-col gap-2">
            {rows.map((r, i) => {
              const guessCls = r.guess.default_class;
              const overridden = r.chosen !== "" && r.chosen !== guessCls;
              return (
                <div key={`${r.id}-${i}`} className="p-3" style={{
                  background: C.panel2, borderRadius: 8,
                  border: `1px solid ${r.chosen === "" ? C.amber : C.line}`,
                }}>
                  <div className="wrapline" style={{ justifyContent: "space-between", gap: 8 }}>
                    <div className="wrapline" style={{ gap: 8 }}>
                      <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>{r.id}</span>
                      <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.steel, border: `1px solid ${C.line}`, borderRadius: 3, padding: "1px 6px" }}>{r.framework}</span>
                      <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{r.source}</span>
                    </div>
                    <div className="wrapline" style={{ gap: 6 }}>
                      {guessCls === "UNKNOWN"
                        ? <span title="the heuristic matched nothing — classify manually" style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 700, color: C.amber }}>?</span>
                        : overridden && (
                          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, textDecoration: "line-through" }}>
                            {guessCls}
                          </span>
                        )}
                      <ClassSelect value={r.chosen} onChange={(c) => patchRow(i, { chosen: c })} />
                      <span style={{ fontFamily: MONO, fontSize: 9.5, color: r.guess.confidence === "high" ? C.mut : C.amber }}>
                        {r.guess.confidence}
                      </span>
                    </div>
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, margin: "5px 0 7px", lineHeight: 1.5 }}>
                    {r.guess.reason}{r.description ? ` — ${r.description}` : ""}
                  </div>
                  <div className="flex flex-col gap-1">
                    <Chips label="driving_args" values={r.driving} placeholder="+ arg"
                      onChange={(v) => patchRow(i, { driving: v })} />
                    <Chips label="untrusted" values={r.untrusted} placeholder="+ result.path"
                      onChange={(v) => patchRow(i, { untrusted: v })} />
                  </div>
                </div>
              );
            })}
          </div>
          <div className="wrapline mt-3" style={{ gap: 10 }}>
            <button onClick={build} disabled={!canBuild} style={cta(canBuild)}>
              {busy ? "building…" : "Build manifests"} <Upload size={13} />
            </button>
            {unknownLeft > 0 && (
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.amber }}>
                {unknownLeft} UNKNOWN left — classify before building
              </span>
            )}
          </div>
        </div>
      )}

      {result && <WrapResult result={result} goInstrumented={goInstrumented} />}
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

      {mode === "code" && <WrapCode goInstrumented={() => setMode("endpoint_instrumented")} />}
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
