// Results (lab-results mockup) for a finished run: renders the aggregates
// stored by POST /runs/{id}/aggregates (bundle.aggregates — computed by the
// runner/analyzer per contracts/statistics.md, RENDERED here, never
// recomputed), plus the per-trial EvidenceCase entry point over the collected
// traces (GET /runs/{id}/trials/{trial_id}/trace).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ChevronRight, FileText, Search } from "lucide-react";
import { C, MONO, btn, cta } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import { useApp } from "../store";
import AggregateTable from "../components/AggregateTable";
import TraceSteps from "../components/TraceSteps";
import EmptyState, { Cmd } from "../components/EmptyState";

export default function Results({ runId }: { runId?: string }) {
  const lastRunId = useApp((s) => s.lastRunId);
  const id = runId ?? lastRunId ?? undefined;
  const [openTrial, setOpenTrial] = useState<string | null>(null);

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
          <Cmd>python -m lab_server --root ./lab-store --runtime-port 8010</Cmd>
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

      {/* EvidenceCase entry — per trial, not just the aggregate */}
      {r && r.trials.some((t) => t.has_trace) && (
        <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.steel}`, borderRadius: 8, overflow: "hidden" }}>
          <div className="px-4 py-3 wrapline" style={{ justifyContent: "space-between" }}>
            <div className="flex items-center gap-2">
              <Search size={13} color={C.steel} />
              <div>
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>Investigate a trial → EvidenceCase</div>
                <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 1 }}>
                  the exact injection, provenance, the gated call, the verdict — per trial, not just the aggregate
                </div>
              </div>
            </div>
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
          <div className="wrapline">
            <button onClick={doExport} style={btn({ color: C.text, background: C.bg, padding: "7px 13px" })}>
              <FileText size={12} /> Export Markdown
            </button>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
              publish-from-UI is not wired yet — assemble the bundle with the runner, then{" "}
              <span style={{ color: C.mut }}>axor-lab publish ./bundle --server …</span>
            </span>
          </div>
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
