// Run progress (lab-run-progress mockup) for the connected_runtime backend.
// Polls GET /runs/{id}/results every 2s (react-query refetchInterval) and
// renders the real lifecycle from runtime_jobs.py:
//   validating -> waiting_for_runtime -> running -> receiving_traces
//   -> analyzing -> completed        (+ awaiting_confirmation before start)
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, ChevronRight, Loader } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { navigate } from "../router";
import { api, RunResults } from "../api";
import { useApp } from "../store";
import EmptyState, { Cmd } from "../components/EmptyState";

const PIPELINE: [string, string][] = [
  ["validating", "schema, bindings, predicates, $inputs"],
  ["waiting_for_runtime", "assigned — the connected runtime pulls and claims it"],
  ["running", "the runtime executes trials and streams kernel events"],
  ["receiving_traces", "trial traces uploading as they finish"],
  ["analyzing", "runner posts bundle.aggregates (CIs per statistics.md)"],
  ["completed", "results + EvidenceCases ready"],
];

const stageIndex = (state: string): number => {
  const i = PIPELINE.findIndex(([s]) => s === state);
  if (i >= 0) return i;
  if (state === "awaiting_confirmation") return 0;
  return 0;
};

function Pipeline({ results }: { results: RunResults }) {
  const idx = stageIndex(results.state);
  const completed = results.state === "completed";
  const done = results.trials.filter((t) => t.status !== "pending").length;
  const planned = results.planned_trials.length;
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: 16 }}>
      {PIPELINE.map(([stage, note], i) => {
        const isDone = i < idx || completed;
        const isCur = i === idx && !completed;
        return (
          <div key={stage} className="wrapline" style={{ gap: 10, padding: "5px 0", opacity: i > idx && !completed ? 0.4 : 1 }}>
            <span style={{ width: 18, flexShrink: 0, display: "flex", justifyContent: "center" }}>
              {isDone ? <Check size={13} color={C.green} />
                : isCur ? <Loader size={13} color={C.steel} className="animate-spin" />
                : <span style={{ width: 6, height: 6, borderRadius: 3, background: C.dim }} />}
            </span>
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: isDone || isCur ? C.text : C.dim, fontWeight: isCur ? 600 : 400, minWidth: 130 }}>{stage}</span>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{note}</span>
          </div>
        );
      })}
      {planned > 0 && !completed && (
        <div className="mt-2" style={{ paddingLeft: 28 }}>
          <div style={{ height: 5, background: C.panel2, borderRadius: 3, overflow: "hidden", maxWidth: 320 }}>
            <div style={{ width: `${(done / planned) * 100}%`, height: "100%", background: C.steel }} />
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 4 }}>
            {done} / {planned} trials
          </div>
        </div>
      )}
    </div>
  );
}

export default function RunProgress({ runId }: { runId?: string }) {
  const lastRunId = useApp((s) => s.lastRunId);
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const id = runId;

  const results = useQuery({
    queryKey: ["run-results", id],
    queryFn: () => api.runResults(id!),
    enabled: !!id,
    // poll every 2s until the run is terminal
    refetchInterval: (query) =>
      query.state.data?.state === "completed" ? false : 2000,
  });

  if (!id) {
    return (
      <div style={{ maxWidth: 620, margin: "0 auto" }}>
        <h1 style={{ fontSize: 19, fontWeight: 650, margin: "0 0 12px" }}>Runs.</h1>
        <EmptyState title="no run selected">
          Runs live at <span style={{ color: C.mut }}>#/runs/{"{run_id}"}</span> — start one in the
          builder (plan → assign to a connected runtime).
          {lastRunId && (
            <div className="mt-2">
              <button onClick={() => navigate(`runs/${lastRunId}`)} style={cta(true)}>
                open last run · {lastRunId} <ChevronRight size={13} />
              </button>
            </div>
          )}
        </EmptyState>
      </div>
    );
  }

  const r = results.data;
  const state = r?.state ?? "…";
  const awaiting = state === "awaiting_confirmation";

  const confirm = async () => {
    setConfirming(true);
    try {
      await api.confirmRun(id);
      await queryClient.invalidateQueries({ queryKey: ["run-results", id] });
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div style={{ maxWidth: 620, margin: "0 auto" }}>
      <div className="wrapline mb-4" style={{ gap: 10, justifyContent: "space-between" }}>
        <h1 style={{ fontSize: 19, fontWeight: 650, margin: 0 }}>
          {state === "completed" ? "Run complete." : awaiting ? "Awaiting your confirmation." : "Running your experiment."}
        </h1>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>run {id}</span>
      </div>

      {r && (
        <div className="wrapline" style={{ marginBottom: 14, gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
            {r.planned_trials.length} planned trials
            {typeof r.estimate.conditions === "number" && typeof r.estimate.repeats === "number"
              ? ` · ${r.estimate.conditions}×${r.estimate.repeats}${typeof r.estimate.scenarios === "number" ? `×${r.estimate.scenarios}` : ""}`
              : ""}
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>backend</span>
          <span style={{ fontFamily: MONO, fontSize: 9, color: C.steel, border: `1px solid ${C.steel}`, borderRadius: 3, padding: "2px 7px" }}>
            connected_runtime
          </span>
        </div>
      )}

      {results.isError && (
        <EmptyState title="run unreachable">
          {String(results.error instanceof Error ? results.error.message : results.error)} — the
          runtime-jobs server must be running (and this run must exist on it; the in-memory store
          forgets runs on restart):
          <Cmd>python -m lab_server --root ./lab-store --runtime-port 8010</Cmd>
        </EmptyState>
      )}

      {awaiting && r && (
        <div className="mb-3 p-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 8 }}>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600, marginBottom: 4 }}>
            before you run — estimate & checks
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.8 }}>
            trials: <span style={{ color: C.text }}>{r.planned_trials.length}</span>
            {Object.entries(r.estimate).map(([k, v]) => ` · ${k} ${String(v)}`).join("")}
          </div>
          <div className="wrapline mt-2">
            <button onClick={confirm} disabled={confirming} style={cta(!confirming)}>
              <Check size={13} /> {confirming ? "confirming…" : "Confirm & start"}
            </button>
          </div>
        </div>
      )}

      {r && <Pipeline results={r} />}

      {r && state !== "completed" && !awaiting && (
        <div className="wrapline mt-3" style={{ gap: 8 }}>
          <span className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
            <Ban size={11} /> cancel is not part of the v1 jobs surface — completed trials are never lost either way
          </span>
        </div>
      )}
      {state === "completed" && (
        <div className="wrapline mt-3" style={{ gap: 8 }}>
          <button onClick={() => navigate(`results/${id}`)} style={cta(true)}>
            View results <ChevronRight size={13} />
          </button>
        </div>
      )}

      {/* live trial table */}
      {r && r.trials.length > 0 && (
        <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", padding: "10px 14px", borderBottom: `1px solid ${C.line}` }}>
            TRIALS — as the runtime reports them
          </div>
          {r.trials.map((t) => (
            <div key={t.trial_id} className="wrapline px-4 py-2" style={{ borderTop: `1px solid ${C.line}`, gap: 10 }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, flex: "1 1 140px" }}>{t.trial_id}</span>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: t.status === "completed" ? C.green : t.status === "failed" ? C.red : C.steel }}>
                {t.status}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                {t.events} events{t.superseded > 0 ? ` · attempt ${t.attempt} (${t.superseded} superseded)` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
