// Reconstruct a pre-Axor incident (#/reconstruct).
//
// The screen that resolves the oldest confusion in this product. The funnel says
// "bring us your production incident", and replay says "I need an Axor trace" —
// both are true, and they are two paths, not one path with a degraded mode.
//
// Replay recomputes `decide(π(x), policy)` from the judge's recorded INPUTS: the
// value ledger and arg_bindings. Only an Axor adapter emits those. Your incident
// happened before Axor was installed, so your LangSmith / OTel / application logs
// record what was CALLED and never where each value CAME FROM. Nothing can be
// replayed from them, and guessing the missing lineage would be unsound in both
// directions.
//
// What those logs are excellent at is AUTHORING A SCENARIO. This screen reads
// them, proposes the tools, the task, the injected content and where it entered,
// and the harmful call that followed; you correct it; then it runs under Axor and
// what comes out is a genuine trace with a real EvidenceCase.
//
// So: your first incident is reconstructed. Every one after it replays exactly,
// because by then Axor was there when it happened.
import { ChangeEvent, useState } from "react";
import { ArrowRight, FileJson, Play, RefreshCw, TriangleAlert, Upload } from "lucide-react";
import { C, MONO, cta, inp } from "../theme";
import { navigate } from "../router";
import { api, type Reconstruction } from "../api";
import { useApp } from "../store";
import Why from "../components/Why";

export default function Reconstruct() {
  const { setLastRun } = useApp();
  const [draft, setDraft] = useState<Reconstruction | null>(null);
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // what the recording cannot show and a human must supply
  const [task, setTask] = useState("");
  const [goodValue, setGoodValue] = useState("");
  const [governed, setGoverned] = useState(true);

  const load = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true); setError(null); setDraft(null);
    try {
      const parsed = JSON.parse(await file.text());
      const result = await api.reconstructIncident(parsed, file.name.replace(/\.json$/i, ""));
      setDraft(result);
      setTask(String(result.scenario.task ?? ""));
      setFileName(file.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const sinkTool = String(
    (draft?.scenario.violation as { tool?: string } | undefined)?.tool ?? "",
  );

  const run = async () => {
    if (!draft) return;
    setBusy(true); setError(null);
    try {
      const scenario = {
        ...draft.scenario,
        task,
        inputs: { legitimate_recipient: goodValue },
        task_success: {
          event: "tool_call", tool: sinkTool,
          where: { "args.recipient": { equal: { input_ref: "legitimate_recipient" } } },
        },
      };
      const landed = await api.runReconstructed(scenario, draft.manifests, governed, 10);
      setLastRun(landed.run_id);
      navigate(`results/${landed.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 620, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>
        Reconstruct an incident
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7, marginBottom: 8 }}>
        For an incident that happened <b style={{ color: C.text }}>before</b> Axor was installed.
        Your logs become a scenario you confirm, then run.
      </div>
      <div style={{ marginBottom: 20 }}>
        <Why label="why can't you just replay my logs?">
          Replay re-runs the judge, not the agent — but it needs the judge's inputs on record: the
          value ledger and the argument bindings, which say where each value came from. Only an Axor
          adapter emits those. A LangSmith, OTel or application log records what was <i>called</i>,
          never what each value was <i>derived from</i>, so there is nothing for the gate to read.
          Guessing it — matching the attacker's IBAN in a tool result against a later argument —
          over-taints and under-taints at once, so it must never produce a verdict, and nothing here
          does. Once Axor is installed, every later incident replays exactly.
        </Why>
      </div>

      {!draft && (
        <label
          style={{
            display: "block", cursor: "pointer", textAlign: "center", padding: "34px 20px",
            borderRadius: 10, background: C.panel, border: `1px dashed ${C.line}`,
          }}
        >
          <input type="file" accept=".json,application/json" onChange={load} style={{ display: "none" }} />
          <Upload size={20} color={C.dim} />
          <div style={{ fontFamily: MONO, fontSize: 12, color: C.text, marginTop: 10 }}>
            {busy ? "reading…" : "Drop your trace export"}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 5, lineHeight: 1.6 }}>
            LangSmith runs · OTel spans · a flat list of tool calls · your own log JSON
          </div>
        </label>
      )}

      {error && (
        <div className="p-3 mt-3" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red, lineHeight: 1.65 }}>
          {error}
          {/* the one refusal worth a door: an Axor trace belongs on the exact path */}
          {error.includes("REPLAYED") && (
            <button
              onClick={() => navigate("import")}
              style={{ display: "block", background: "none", border: "none", padding: "8px 0 0", cursor: "pointer", color: C.green, fontFamily: MONO, fontSize: 11, textDecoration: "underline" }}
            >
              replay it exactly instead →
            </button>
          )}
        </div>
      )}

      {draft && (
        <>
          <div className="wrapline" style={{ gap: 8, marginBottom: 14 }}>
            <FileJson size={13} color={C.dim} />
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>{fileName}</span>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.amber, border: `1px solid ${C.amber}`, borderRadius: 20, padding: "2px 8px" }}>
              {draft.fidelity}
            </span>
          </div>

          <Section label="What it found" />
          {draft.findings.map((f, i) => (
            <div key={i} className="wrapline" style={{ gap: 10, alignItems: "flex-start", padding: "6px 0", borderTop: i ? `1px solid ${C.line}` : "none" }}>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.violet, minWidth: 108 }}>
                {f.kind}
              </span>
              <span style={{ flex: "1 1 240px" }}>
                <span style={{ display: "block", fontFamily: MONO, fontSize: 11, color: C.text, lineHeight: 1.5, wordBreak: "break-word" }}>
                  {f.detail}
                </span>
                <span style={{ display: "block", fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 2 }}>
                  {f.where}
                </span>
              </span>
            </div>
          ))}

          <Section label="What you have to say" />
          <div style={{ marginBottom: 12 }}>
            <Label>The task the agent was given</Label>
            <input
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Pay the $1,200 rent to my landlord."
              style={{ ...inp, width: "100%" }}
            />
          </div>
          <div style={{ marginBottom: 12 }}>
            <Label>
              What {sinkTool || "the sink"} should have received, if the run had gone right
            </Label>
            <input
              value={goodValue}
              onChange={(e) => setGoodValue(e.target.value)}
              placeholder="the legitimate recipient — an IBAN, an address, an id"
              style={{ ...inp, width: "100%" }}
            />
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 5, lineHeight: 1.6 }}>
              The recording shows what went wrong, never what right looks like — and without it
              "the gate cost you nothing" would be true for the wrong reason.
            </div>
          </div>

          {draft.unresolved.length > 0 && (
            <div className="p-3" style={{ border: `1px solid ${C.line}`, borderLeft: `3px solid ${C.amber}`, borderRadius: "0 8px 8px 0", background: C.panel, marginBottom: 14 }}>
              <div className="wrapline" style={{ gap: 6, marginBottom: 6 }}>
                <TriangleAlert size={12} color={C.amber} />
                <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.text, fontWeight: 600 }}>
                  What it could not tell
                </span>
              </div>
              {draft.unresolved.map((u, i) => (
                <div key={i} style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.65, marginBottom: 4 }}>
                  · {u}
                </div>
              ))}
            </div>
          )}

          <label className="wrapline" style={{ gap: 8, cursor: "pointer", marginBottom: 16 }}>
            <input type="checkbox" checked={governed} onChange={(e) => setGoverned(e.target.checked)} />
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: governed ? C.text : C.mut }}>
              run it governed as well, to see what a gate would have changed
            </span>
          </label>

          <div className="wrapline" style={{ gap: 12 }}>
            <button onClick={run} disabled={busy || !task.trim() || !goodValue.trim()} style={cta(!busy && !!task.trim() && !!goodValue.trim())}>
              {busy
                ? <><RefreshCw size={13} className="animate-spin" /> running…</>
                : <><Play size={13} /> Run the reconstruction</>}
            </button>
            <button
              onClick={() => { setDraft(null); setError(null); }}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}
            >
              start over
            </button>
          </div>

          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.7, marginTop: 18, paddingTop: 14, borderTop: `1px solid ${C.line}` }}>
            The run is real — a genuine Axor trace, a real EvidenceCase, a pinnable regression case.
            What it is not is a replay: it measures a <b style={{ color: C.mut }}>model of</b> your
            incident, and how well the model holds depends on how well the scenario above captures
            what actually happened. That sentence travels with the result.
          </div>
        </>
      )}

      <div className="wrapline" style={{ gap: 14, marginTop: 26, paddingTop: 14, borderTop: `1px solid ${C.line}` }}>
        <button
          onClick={() => navigate("import")}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 10.5 }}
        >
          I have an Axor trace — replay it exactly <ArrowRight size={10} style={{ verticalAlign: -1 }} />
        </button>
      </div>
    </div>
  );
}

function Section({ label }: { label: string }) {
  return (
    <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: .5, margin: "22px 0 8px" }}>
      {label.toUpperCase()}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 5 }}>{children}</div>
  );
}
