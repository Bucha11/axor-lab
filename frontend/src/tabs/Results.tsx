// Results (lab-results mockup) for a finished run: renders the aggregates
// stored by POST /runs/{id}/aggregates (bundle.aggregates — computed by the
// runner/analyzer per contracts/statistics.md, RENDERED here, never
// recomputed), plus the per-trial EvidenceCase entry point over the collected
// traces (GET /runs/{id}/trials/{trial_id}/trace).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, FileText, Search, Upload } from "lucide-react";
import { C, MONO, btn, cta } from "../theme";
import { navigate } from "../router";
import { api, PublishError, type CpExportResult, type RegressionPinBody } from "../api";
import { useApp } from "../store";
import AggregateTable from "../components/AggregateTable";
import TraceSteps from "../components/TraceSteps";
import EmptyState, { Cmd } from "../components/EmptyState";

export default function Results({ runId }: { runId?: string }) {
  const lastRunId = useApp((s) => s.lastRunId);
  const id = runId ?? lastRunId ?? undefined;
  const [openTrial, setOpenTrial] = useState<string | null>(null);
  // publish-this-run state: the question the publication answers, its visibility
  // (unlisted by default — the backend's safe default), and the in-flight status.
  const [question, setQuestion] = useState("");
  const [visibility, setVisibility] = useState<"unlisted" | "public">("unlisted");
  const [publishing, setPublishing] = useState(false);
  const [publishErr, setPublishErr] = useState<string | null>(null);
  const [publishedId, setPublishedId] = useState<string | null>(null);

  const results = useQuery({
    queryKey: ["run-results", id],
    queryFn: () => api.runResults(id!),
    enabled: !!id,
  });
  const trace = useQuery({
    queryKey: ["trial-trace", id, openTrial],
    queryFn: () => api.trialTrace(id!, openTrial!),
    enabled: !!id && !!openTrial,
  });

  if (!id) {
    return (
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 12px" }}>Results.</h1>
        <EmptyState title="no run selected">
          Results live at <span style={{ color: C.mut }}>#/results/{"{run_id}"}</span>. Start a run in the
          builder; when it completes, its aggregates land here.
        </EmptyState>
      </div>
    );
  }

  const r = results.data;
  const doExport = () => {
    if (!r) return;
    const lines = [
      `# run ${r.run_id}`,
      "",
      "| metric | condition | estimate | 95% CI | n |",
      "|---|---|---|---|---|",
      ...r.aggregates.map((a) =>
        `| ${a.metric} | ${a.condition_id} | ${a.estimate.toFixed(2)} | [${a.interval.low.toFixed(2)}, ${a.interval.high.toFixed(2)}] | ${a.n} |`),
      "",
      "note: model layer is stochastic (CI over repeats); governance verdicts replay bit-identical",
      "",
      "reproduce:  axor-lab replay ./bundle    # governance verdicts, exact",
    ].join("\n");
    const url = URL.createObjectURL(new Blob([lines], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${r.run_id}-results.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // publish this run: assemble the bundle from the completed run (GET
  // /runs/{id}/bundle), then POST it to the publications server, which RE-VERIFIES
  // (content hashes + bit-identical replay + statistical recomputation) before
  // minting. Any failure — a 402 entitlement, a replay mismatch, a content-hash
  // failure — surfaces the server's own reason, unedited.
  const doPublish = async () => {
    if (!r) return;
    setPublishing(true);
    setPublishErr(null);
    setPublishedId(null);
    try {
      const { bundle, traces } = await api.runBundle(r.run_id);
      const res = await api.publishBundle(bundle, traces, question.trim(), visibility);
      setPublishedId(res.publication_id);
    } catch (e) {
      setPublishErr(
        e instanceof PublishError
          ? `${e.status} — ${e.message}`
          : String(e instanceof Error ? e.message : e),
      );
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <div className="wrapline mb-2" style={{ gap: 10, justifyContent: "space-between" }}>
        <h1 style={{ fontSize: 21, fontWeight: 650, margin: 0 }}>Results.</h1>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>run {id}</span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
        live runs call the model — stochastic, reported with CI over repeats. Only the governance verdicts replay bit-for-bit.
      </div>

      {results.isError && (
        <EmptyState title="run unreachable">
          {String(results.error instanceof Error ? results.error.message : results.error)} — start the
          runtime-jobs server (the in-memory store forgets runs on restart):
          <Cmd>python -m lab_server --root ./lab-store</Cmd>
        </EmptyState>
      )}

      {r && r.aggregates.length === 0 && (
        <EmptyState title={`no aggregates yet (state: ${r.state})`}>
          {r.traces.length} trace{r.traces.length === 1 ? "" : "s"} collected. Aggregates are computed by the
          runner/analyzer and attached via
          <Cmd>{`POST /runs/${id}/aggregates   {"aggregates": [...]}   # bundle.aggregates`}</Cmd>
          Lab renders aggregates; it does not compute them (ui-backend-contract §3).
        </EmptyState>
      )}

      {r && r.aggregates.length > 0 && (
        <>
          <AggregateTable aggregates={r.aggregates} />
          <div className="mt-2 flex items-start gap-2 p-2" style={{ background: C.panel2, borderRadius: 4 }}>
            <AlertTriangle size={11} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.5 }}>
              This measures the effect of our own enforcement — a labeled "effect of governance" study, not a
              neutral benchmark. Reproduce it independently. The model layer is stochastic; only the governance
              verdicts replay exactly.
            </span>
          </div>
        </>
      )}

      {/* Investigate, pin, export — over YOUR OWN run, no publishing required.
          All three were CLI-only: the web could render an EvidenceCase for a
          PUBLISHED bundle only, pinning ran off imported incidents only, and the
          CP handoff had no web path at all. */}
      {id && r && r.state === "completed" && (
        <RunWorkbench runId={id} />
      )}

      {/* raw trace steps per trial — the layer under the EvidenceCase */}
      {r && r.trials.some((t) => t.has_trace) && (
        <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
          <div className="px-4 py-3 flex items-center gap-2">
            <Search size={13} color={C.dim} />
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>Raw trial traces</div>
          </div>
          {r.trials.filter((t) => t.has_trace).map((t) => (
            <div key={t.trial_id}>
              <button onClick={() => setOpenTrial(openTrial === t.trial_id ? null : t.trial_id)}
                className="wrapline px-4 py-2 w-full"
                style={{ background: "none", border: "none", borderTop: `1px solid ${C.line}`, cursor: "pointer", justifyContent: "space-between", textAlign: "left" }}>
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.text }}>{t.trial_id}</span>
                <span className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                  {t.status}
                  {openTrial === t.trial_id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                </span>
              </button>
              {openTrial === t.trial_id && (
                <div className="px-4 pb-3">
                  {trace.isLoading && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>loading trace…</span>}
                  {trace.isError && (
                    <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>
                      {String(trace.error instanceof Error ? trace.error.message : trace.error)}
                    </span>
                  )}
                  {trace.data && <TraceSteps trace={trace.data} />}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* export & publish */}
      {r && r.aggregates.length > 0 && (
        <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 8 }}>
          <div className="wrapline mb-3" style={{ justifyContent: "space-between" }}>
            <div>
              <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600 }}>Export & publish</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 2 }}>
                a published bundle embeds kernel version + config hash + model id + frozen traces — governance replays exact
              </div>
            </div>
          </div>
          <div className="wrapline" style={{ marginBottom: 12 }}>
            <button onClick={doExport} style={btn({ color: C.text, background: C.bg, padding: "7px 13px" })}>
              <FileText size={12} /> Export Markdown
            </button>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
              or publish this run below — the server re-verifies (replay + stats) before minting
            </span>
          </div>

          {/* publish this run — the bundle is assembled from the completed run
              (GET /runs/{id}/bundle) and re-verified server-side before minting */}
          {r.state !== "completed" ? (
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.5 }}>
              publish becomes available once the run reaches <span style={{ color: C.text }}>completed</span>{" "}
              (current state: <span style={{ color: C.amber }}>{r.state}</span>) — every planned trial must
              finish so the bundle carries its full evidence.
            </div>
          ) : publishedId ? (
            <div className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>
              <ExternalLink size={12} />
              <span>published — </span>
              <a
                href={`#/e/${publishedId}`}
                onClick={() => navigate(`e/${publishedId}`)}
                style={{ color: C.steel, textDecoration: "underline" }}
              >
                #/e/{publishedId}
              </a>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="the question this publication answers (e.g. does content-ledger governance cut ASR?)"
                style={{
                  fontFamily: MONO, fontSize: 11, color: C.text, background: C.bg,
                  border: `1px solid ${C.line}`, borderRadius: 5, padding: "7px 10px", width: "100%",
                  boxSizing: "border-box",
                }}
              />
              <div className="wrapline" style={{ gap: 10 }}>
                <label style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, display: "flex", alignItems: "center", gap: 6 }}>
                  visibility
                  <select
                    value={visibility}
                    onChange={(e) => setVisibility(e.target.value as "unlisted" | "public")}
                    style={{
                      fontFamily: MONO, fontSize: 10.5, color: C.text, background: C.bg,
                      border: `1px solid ${C.line}`, borderRadius: 5, padding: "5px 8px",
                    }}
                  >
                    <option value="unlisted">unlisted (capability URL only)</option>
                    <option value="public">public (listed in the catalog)</option>
                  </select>
                </label>
                <button
                  onClick={doPublish}
                  disabled={publishing || !question.trim()}
                  style={cta(!publishing && !!question.trim(), { padding: "7px 13px" })}
                >
                  <Upload size={12} /> {publishing ? "publishing…" : "publish this run"}
                </button>
              </div>
              {publishErr && (
                <div className="flex items-start gap-2" style={{ fontFamily: MONO, fontSize: 10, color: C.red, lineHeight: 1.5 }}>
                  <AlertTriangle size={11} style={{ marginTop: 1, flexShrink: 0 }} />
                  <span>{publishErr}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {r && r.state !== "completed" && (
        <div className="wrapline mt-4">
          <button onClick={() => navigate(`runs/${id}`)} style={cta(true)}>
            back to run progress <ChevronRight size={13} />
          </button>
        </div>
      )}
    </div>
  );
}

// ── the run workbench: investigate → pin → export, over an unpublished run ────
//
// These three were CLI-only. The web could render an EvidenceCase for a
// PUBLISHED bundle only — backwards, since investigation is what decides whether
// a run is worth publishing. Pinning ran off imported production incidents only,
// so closing the experiment → regression loop needed an incident to exist first.
// And the Lab → Control Plane handoff had no web path at all, which put the
// bridge into the paid contour behind a terminal.
function RunWorkbench({ runId }: { runId: string }) {
  const [openTrace, setOpenTrace] = useState<string | null>(null);
  const [pins, setPins] = useState<Record<string, RegressionPinBody>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cp, setCp] = useState<CpExportResult | null>(null);
  const [onlyDenied, setOnlyDenied] = useState(true);

  const index = useQuery({
    queryKey: ["run-traces", runId],
    queryFn: () => api.runTraces(runId),
  });
  const evidence = useQuery({
    queryKey: ["run-evidence", runId, openTrace],
    queryFn: () => api.runEvidence(runId, openTrace!),
    enabled: !!openTrace,
  });

  const all = index.data?.traces ?? [];
  const denied = all.filter((t) => t.denied);
  const shown = onlyDenied ? denied : all;

  const pin = async (traceId: string) => {
    setBusy(traceId); setError(null);
    try {
      const res = await api.pinRunTrace(runId, traceId);
      setPins((p) => ({ ...p, [traceId]: res.pin }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const exportCp = async () => {
    setBusy("cp"); setError(null); setCp(null);
    try {
      setCp(await api.cpExport(runId, Object.values(pins)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (index.isLoading) {
    return <div className="mt-3" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>loading traces…</div>;
  }
  if (index.isError || all.length === 0) return null;

  return (
    <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.steel}`, borderRadius: 8, overflow: "hidden" }}>
      <div className="px-4 py-3 wrapline" style={{ justifyContent: "space-between" }}>
        <div className="flex items-center gap-2">
          <Search size={13} color={C.steel} />
          <div>
            <div style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>Investigate this run</div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 1 }}>
              the exact injection, provenance, the gated call, the verdict — no publishing required
            </div>
          </div>
        </div>
        <button onClick={() => setOnlyDenied(!onlyDenied)} style={btn({ padding: "3px 9px", fontSize: 10 })}>
          {onlyDenied ? `${denied.length} denied` : `all ${all.length}`}
        </button>
      </div>

      {shown.slice(0, 40).map((t) => (
        <div key={t.trace_id} style={{ borderTop: `1px solid ${C.line}` }}>
          <div className="wrapline px-4 py-2" style={{ justifyContent: "space-between", gap: 8 }}>
            <button onClick={() => setOpenTrace(openTrace === t.trace_id ? null : t.trace_id)}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", textAlign: "left", flex: "1 1 220px", minWidth: 180 }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.text }}>
                {t.scenario_id} · <span style={{ color: t.denied ? C.green : C.mut }}>{t.condition_id}</span>
              </span>
              <span style={{ display: "block", fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                seed {t.seed} · {t.verdicts.join(", ") || "no gate decision"}
              </span>
            </button>
            <div className="wrapline" style={{ gap: 6 }}>
              {pins[t.trace_id]
                ? <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.green }}>
                    pinned {pins[t.trace_id].expected_sequence.join(",")}
                  </span>
                : <button onClick={() => pin(t.trace_id)} disabled={busy === t.trace_id}
                    style={btn({ padding: "2px 8px", fontSize: 10 })}>
                    {busy === t.trace_id ? "pinning…" : "pin as regression"}
                  </button>}
              <button onClick={() => setOpenTrace(openTrace === t.trace_id ? null : t.trace_id)}
                style={btn({ padding: "2px 8px", fontSize: 10 })}>
                <FileText size={10} /> EvidenceCase
              </button>
            </div>
          </div>
          {openTrace === t.trace_id && (
            <div className="px-4 pb-3">
              {evidence.isLoading && <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>building the case…</span>}
              {evidence.isError && (
                <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>
                  {String(evidence.error instanceof Error ? evidence.error.message : evidence.error)}
                </span>
              )}
              {evidence.data && (
                <pre style={{ margin: 0, maxHeight: 320, overflow: "auto", background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 9.5, color: C.mut, lineHeight: 1.5 }}>
                  {JSON.stringify(evidence.data, null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
      ))}
      {shown.length > 40 && (
        <div className="px-4 py-2" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
          showing 40 of {shown.length} — narrow with the filter above
        </div>
      )}

      {/* Lab → Control Plane */}
      <div className="px-4 py-3" style={{ borderTop: `1px solid ${C.line}` }}>
        <div className="wrapline" style={{ justifyContent: "space-between", gap: 10 }}>
          <div style={{ flex: "1 1 300px", minWidth: 240 }}>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>Hand off to Control Plane</div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 2, lineHeight: 1.6 }}>
              the validated policy + manifests + any pins above, as a deployable CP config.
              {Object.keys(pins).length > 0 && ` Carrying ${Object.keys(pins).length} pin(s).`}
            </div>
          </div>
          <button onClick={exportCp} disabled={busy === "cp"} style={btn({ padding: "4px 10px", fontSize: 10.5 })}>
            <Upload size={11} /> {busy === "cp" ? "building…" : "build CP config"}
          </button>
        </div>

        {cp && (
          <div className="mt-3">
            <div className="wrapline" style={{ gap: 6, marginBottom: 6 }}>
              {cp.earned_bridge
                ? <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.green }}>
                    earned — the advantage cleared the statistical and practical-significance gates
                  </span>
                : <span className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber }}>
                    <AlertTriangle size={11} /> observed, not earned — this config's advantage did not clear the
                    bridge, so deploy it as a judgement call, not as a measured result
                  </span>}
            </div>
            <pre style={{ margin: 0, maxHeight: 220, overflow: "auto", background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 9.5, color: C.mut, lineHeight: 1.5 }}>
              {JSON.stringify(cp.config, null, 2)}
            </pre>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 8, lineHeight: 1.6 }}>
              This is the config and its production to-do. Writing the signed, manifest-bound export
              tree stays with <span style={{ color: C.mut }}>axor-lab export-cp</span>, where your
              signing key lives — a server cannot sign on your behalf.
            </div>
          </div>
        )}
      </div>

      {error && (
        <div className="px-4 pb-3" style={{ fontFamily: MONO, fontSize: 10.5, color: C.red, lineHeight: 1.6 }}>
          {error}
        </div>
      )}
    </div>
  );
}
