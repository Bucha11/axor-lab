// Run the same conditions against several models — the comparison the scripted
// agent cannot make and the connected-runtime path does not answer.
//
// Worth being blunt about why this exists. The scripted agent's attack rate is a
// PARAMETER (`scripted@0.6` means it follows the injection ~60% of the time), so
// an ungoverned ASR from it is a dial, not a measurement. It proves the kernel
// denies a tainted sink — real, and replayable — but nothing about any model.
// Only a live model turns the ungoverned arm into a measurement, and only a
// live model can answer "which of these models gets exfiltrated".
//
// Three things this screen refuses to blur:
//   * you see the price before you hand over a key;
//   * the approval binds to the run it priced — a token from a 6-trial estimate
//     will not execute 120 trials;
//   * a live model samples each condition independently, so these results are
//     two-proportion comparisons and never a paired McNemar p-value.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Play, RefreshCw, TriangleAlert } from "lucide-react";
import { C, MONO, btn, cta, inp } from "../theme";
import { navigate } from "../router";
import { api, type ComposeSpec, type LiveBudget, type LivePlan } from "../api";
import EmptyState from "../components/EmptyState";

const MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"];

interface Outcome {
  model: string;
  runId?: string;
  asrUngoverned?: number;
  asrGoverned?: number;
  usd?: number;
  error?: string;
}

export default function LiveModels() {
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: api.catalog });
  const [suite, setSuite] = useState("");
  const [repeats, setRepeats] = useState(5);
  const [models, setModels] = useState<string[]>([MODELS[0]]);
  const [maxUsd, setMaxUsd] = useState("2.00");
  const [maxOutputTokens, setMaxOutputTokens] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [plans, setPlans] = useState<Record<string, LivePlan> | null>(null);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const suiteId = suite || catalog.data?.suites[0]?.id || "";
  const spec = (): ComposeSpec => ({ suite: suiteId, repeats });
  const budget = (): LiveBudget => ({
    ...(maxUsd.trim() ? { max_usd: Number(maxUsd) } : {}),
    ...(maxOutputTokens.trim() ? { max_output_tokens: Number(maxOutputTokens) } : {}),
  });

  const doPlan = async () => {
    setBusy("plan"); setError(null); setPlans(null); setOutcomes([]);
    try {
      const priced: Record<string, LivePlan> = {};
      for (const model of models) priced[model] = await api.planLive(spec(), model, budget());
      setPlans(priced);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const doRun = async () => {
    if (!plans) return;
    setError(null); setOutcomes([]);
    const collected: Outcome[] = [];
    for (const model of models) {
      setBusy(model);
      try {
        const run = await api.runLive(spec(), model, budget(), plans[model].confirm_token, apiKey);
        const results = await api.runResults(run.run_id);
        const asr = Object.fromEntries(
          results.aggregates.filter((a) => a.metric === "ASR").map((a) => [a.condition_id, a.estimate]),
        );
        collected.push({
          model, runId: run.run_id, usd: run.usage?.usd,
          asrUngoverned: asr["ungoverned"], asrGoverned: asr["governed"],
        });
      } catch (e) {
        collected.push({ model, error: e instanceof Error ? e.message : String(e) });
      }
      setOutcomes([...collected]);
    }
    setBusy(null);
  };

  const totalUsd = plans
    ? Object.values(plans).reduce((sum, p) => sum + p.estimate.usd, 0)
    : 0;

  if (catalog.isLoading) {
    return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading catalog…</div>;
  }
  if (catalog.isError || !catalog.data) {
    return (
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <EmptyState title="run API unreachable">
          Start the server and reload: python -m lab_server --root ./lab-store
        </EmptyState>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 4px" }}>Compare models.</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 16, lineHeight: 1.7 }}>
        Same scenarios, same governance, several models — which one actually follows the injection.
        This is the only run type where the ungoverned arm is a <b style={{ color: C.text }}>measurement</b>:
        the scripted agent's attack rate is a parameter you set, so its ASR is a dial, not a finding.
      </div>

      <div className="p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        <div className="wrapline" style={{ gap: 8, marginBottom: 10 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 62 }}>suite</span>
          {catalog.data.suites.map((s) => (
            <button key={s.id} onClick={() => setSuite(s.id)}
              style={{ background: suiteId === s.id ? "rgba(155,140,204,0.1)" : "none", border: `1px solid ${suiteId === s.id ? C.violet : C.line}`, borderRadius: 4, color: suiteId === s.id ? C.text : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "3px 9px", cursor: "pointer" }}>
              {s.label} ({s.scenarios.length})
            </button>
          ))}
        </div>

        <div className="wrapline" style={{ gap: 8, marginBottom: 10 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 62 }}>models</span>
          {MODELS.map((m) => {
            const on = models.includes(m);
            return (
              <button key={m} onClick={() => setModels(on ? models.filter((x) => x !== m) : [...models, m])}
                style={{ background: on ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${on ? C.steel : C.line}`, borderRadius: 4, color: on ? C.steel : C.mut, fontFamily: MONO, fontSize: 10.5, padding: "3px 9px", cursor: "pointer" }}>
                {on ? "✓ " : ""}{m}
              </button>
            );
          })}
        </div>

        <div className="wrapline" style={{ gap: 8, marginBottom: 10 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 62 }}>repeats</span>
          <input type="range" min={1} max={20} value={repeats}
            onChange={(e) => setRepeats(Number(e.target.value))}
            style={{ flex: "1 1 160px", accentColor: C.violet }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.text }}>{repeats}</span>
        </div>

        <div className="wrapline" style={{ gap: 8, marginBottom: 6 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 62 }}>max $</span>
          <input value={maxUsd} onChange={(e) => setMaxUsd(e.target.value)}
            style={{ ...inp, width: 80, fontSize: 10.5 }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>max out tokens</span>
          <input value={maxOutputTokens} onChange={(e) => setMaxOutputTokens(e.target.value)}
            placeholder="optional — a HARD bound"
            style={{ ...inp, flex: "1 1 150px", fontSize: 10.5 }} />
        </div>
        <div className="wrapline" style={{ gap: 6, marginBottom: 10 }}>
          <TriangleAlert size={11} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.amber, lineHeight: 1.6 }}>
            the token ceiling is hard — Lab counts what it sends and caps each call. The dollar
            figure comes from an illustrative price table, not your provider's billing: it stops the
            run <b>near</b> the number. Set a token ceiling when an exact bound matters.
          </span>
        </div>

        <button onClick={doPlan} disabled={busy !== null || !models.length} style={btn({ padding: "5px 12px", fontSize: 11 })}>
          {busy === "plan" ? "pricing…" : "price this run"}
        </button>
      </div>

      {error && (
        <div className="mt-3">
          <EmptyState title="refused">{error}</EmptyState>
        </div>
      )}

      {plans && (
        <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 10 }}>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600, marginBottom: 8 }}>
            Estimate — you pay your provider, not us
          </div>
          {Object.entries(plans).map(([model, p]) => (
            <div key={model} style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.9 }}>
              {model}: {p.estimate.line}
            </div>
          ))}
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginTop: 6 }}>
            total ≈ ${totalUsd.toFixed(2)} across {models.length} model{models.length === 1 ? "" : "s"}
          </div>

          <div className="wrapline mt-2" style={{ gap: 6 }}>
            <AlertTriangle size={11} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.amber, lineHeight: 1.6 }}>
              {Object.values(plans)[0].design_note}
            </span>
          </div>

          <label className="wrapline mt-3" style={{ gap: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 62 }}>API key</span>
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
              placeholder="your Anthropic key — used for this run, never stored"
              style={{ ...inp, flex: "1 1 260px", fontSize: 10.5 }} />
          </label>

          <div className="wrapline mt-3" style={{ gap: 10 }}>
            <button onClick={doRun} disabled={!apiKey.trim() || busy !== null} style={cta(!!apiKey.trim() && busy === null)}>
              {busy && busy !== "plan"
                ? <><RefreshCw size={13} className="animate-spin" /> running {busy}…</>
                : <><Play size={13} /> confirm and run</>}
            </button>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, lineHeight: 1.5 }}>
              this approval is bound to the run above — changing the suite, repeats, models or
              ceiling invalidates it
            </span>
          </div>
        </div>
      )}

      {outcomes.length > 0 && (
        <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
          <div className="px-4 py-3" style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600 }}>
            Attack success rate by model
          </div>
          {outcomes.map((o) => (
            <div key={o.model} className="wrapline px-4 py-2" style={{ borderTop: `1px solid ${C.line}`, justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, flex: "1 1 150px" }}>{o.model}</span>
              {o.error
                ? <span style={{ fontFamily: MONO, fontSize: 10, color: C.red, flex: "2 1 240px" }}>{o.error}</span>
                : (
                  <>
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>
                      ungoverned <b style={{ color: (o.asrUngoverned ?? 0) > 0 ? C.red : C.green }}>
                        {o.asrUngoverned?.toFixed(2) ?? "—"}
                      </b>
                      {" → governed "}
                      <b style={{ color: (o.asrGoverned ?? 0) > 0 ? C.amber : C.green }}>
                        {o.asrGoverned?.toFixed(2) ?? "—"}
                      </b>
                    </span>
                    <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                      ${o.usd?.toFixed(2) ?? "0.00"}
                    </span>
                    {o.runId && (
                      <button onClick={() => navigate(`results/${o.runId}`)} style={btn({ padding: "2px 8px", fontSize: 10 })}>
                        <Check size={10} /> results
                      </button>
                    )}
                  </>
                )}
            </div>
          ))}
          <div className="px-4 py-3" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 9.5, color: C.dim, lineHeight: 1.7 }}>
            Each model was sampled independently, so these are separate two-proportion results, not a
            paired comparison — and comparing two models to each other here is descriptive, not a test.
            The governed arm's denials replay bit-identically; the model's choices do not.
          </div>
        </div>
      )}
    </div>
  );
}
