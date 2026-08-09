import { useEffect } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Empty, Failed, Json, Link, Loading, Stat } from "../components/ui";
import { StatusTag, Timeline } from "../components/Timeline";
import { useRunEvents } from "../lib/useRunEvents";

// pre-start / dead states the progress bar must NOT paint as "live" — a run
// awaiting confirmation or waiting for a runtime has not started
const PRE_START = new Set(["awaiting_confirmation", "waiting_for_runtime"]);

/** "3m ago" from an epoch-seconds timestamp — so a run's age is visible and a
 * stuck run (running, but idle for a long time) is distinguishable from a fresh
 * one. */
function ago(epochSeconds?: number): string {
  if (!epochSeconds) return "";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function Runs() {
  // every run, from the dedicated endpoint — the Runs screen used to read
  // home.recent_runs (capped at 5), so a sixth run silently vanished
  const { data, error, loading, reload } = useAsync(() => api.runs());
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  const runs = data?.runs ?? [];
  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Runs</h1>
      </header>
      {runs.length === 0 ? (
        <Empty>No runs yet. A connected runtime claims an assignment and pushes traces back.</Empty>
      ) : (
        <ul className="rows">
          {runs.map((run) => (
            <li key={run.run_id}>
              <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
              <span className="muted small">{run.completed}/{run.planned}</span>
              <StatusTag status={run.state} />
              <span className="muted small">{ago(run.updated_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RunReport({ runId }: { runId: string }) {
  const report = useAsync(() => api.runReport(runId), [runId]);
  const results = useAsync(() => api.runResults(runId), [runId]);
  // Live progress. The report itself is REFETCHED when the run reaches a
  // terminal state rather than being patched from the stream: the stream
  // carries progress, and coverage/aggregates are the backend's to compute.
  // Deriving them here would be the second opinion the contract forbids.
  const progress = useRunEvents(runId);
  const finished = progress?.terminal ?? false;
  useEffect(() => {
    if (finished) {
      report.reload();
      results.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  if (report.loading || results.loading) return <Loading />;
  if (report.error) return <Failed error={report.error} onRetry={report.reload} />;
  if (!report.data) return null;
  const { coverage, trials_by_status, metric_coverage, aggregates, estimate } = report.data;
  const state = progress?.state ?? report.data.state;
  const preStart = PRE_START.has(state);
  const terminal = progress?.terminal ?? ["completed", "failed", "cancelled"].includes(state);

  async function confirm() {
    await api.confirmRun(runId);
    report.reload();
    results.reload();
  }
  async function cancel() {
    await api.cancelRun(runId);
    report.reload();
  }

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Run {runId}</h1>
        {/* The live state when the stream has one, the loaded state otherwise —
            never both, so the screen cannot show two different answers. */}
        <StatusTag status={state} />
        {/* the progress bar means "in flight" — only for a run that has actually
            started, never for one awaiting confirmation or a runtime */}
        {progress && !progress.terminal && !preStart && (
          <div className="progress">
            <div
              className="progress-bar"
              style={{
                width: `${
                  progress.planned > 0
                    ? Math.round((progress.completed / progress.planned) * 100)
                    : 0
                }%`,
              }}
            />
            <span className="muted small">
              {progress.completed}/{progress.planned} trial(s) — live
            </span>
          </div>
        )}
        {state === "awaiting_confirmation" && (
          <div className="row">
            <span className="muted small">
              Estimate:{" "}
              {estimate && Object.keys(estimate).length > 0
                ? Object.entries(estimate).map(([k, v]) => `${k} ${v}`).join(" · ")
                : "not provided"}
            </span>
            <Button onClick={confirm}>Confirm &amp; start</Button>
          </div>
        )}
        {!terminal && !preStart && (
          <Button variant="secondary" onClick={cancel}>
            Cancel run
          </Button>
        )}
      </header>

      {/* Coverage BEFORE aggregates, per statistics.md §5: a report that shows
          rates without saying what fraction of the plan produced them invites
          reading a partial run as a complete one. */}
      <section>
        <h2>Coverage</h2>
        <div className="stats">
          <Stat label="completed" value={coverage.completed} />
          <Stat label="planned" value={coverage.planned} />
          {/* the by-status breakdown, WITHOUT completed — it is already the
              first tile, and showing it twice ("4 completed … 4 completed")
              read as a bug */}
          {Object.entries(trials_by_status)
            .filter(([status]) => status !== "completed")
            .map(([status, n]) => (
              <Stat key={status} label={status} value={n} />
            ))}
        </div>
        {coverage.completed < coverage.planned && (
          <p className="muted">
            {coverage.planned - coverage.completed} planned trial(s) did not
            complete. Aggregates below are over the completed subset.
          </p>
        )}
      </section>

      <section>
        <h2>Metrics measured</h2>
        {Object.keys(metric_coverage).length === 0 ? (
          <Empty>No completed trial recorded a metric.</Empty>
        ) : (
          <ul className="rows">
            {Object.entries(metric_coverage).map(([metric, n]) => (
              <li key={metric}>
                <code>{metric}</code>
                <span className="muted">
                  {n}/{coverage.completed} trial(s)
                </span>
              </li>
            ))}
          </ul>
        )}
        {/* An unmeasured metric is ABSENT from that map, never a zero. The
            screen must not fill the gap either: a run that priced nothing has
            no cost, and showing 0 would make it look free. */}
      </section>

      <section>
        <h2>Aggregates</h2>
        {aggregates.length === 0 ? (
          <Empty>
            The backend stored no aggregate for this run. Nothing is computed here —
            a rate derived in the browser would be a second opinion about a published number.
          </Empty>
        ) : (
          <AggregatesTable rows={aggregates} />
        )}
      </section>

      <section>
        <h2>Trials</h2>
        <ul className="rows">
          {(results.data?.trials ?? []).map((trial) => (
            <li key={trial.trial_id}>
              <Link to={`/runs/${runId}/trials/${trial.trial_id}`}>{trial.trial_id}</Link>
              <StatusTag status={trial.status} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** Aggregates as a table, not a JSON dump. Every value is RENDERED from the
 * stored aggregate — nothing is computed here (the contract's rule) — but a
 * per-metric/per-arm row with the interval and any comparison test spelled out
 * is what the Run Report promised instead of a `<pre>`. */
function AggregatesTable({ rows }: { rows: Record<string, unknown>[] }) {
  const num = (v: unknown) => (typeof v === "number" ? v.toFixed(3) : "—");
  return (
    <div className="table-scroll">
      <table className="agg-table">
        <thead>
          <tr>
            <th>metric</th><th>arm</th><th>estimate</th><th>interval</th>
            <th>n</th><th>test</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const interval = (row.interval ?? {}) as Record<string, unknown>;
            const test = row.test as Record<string, unknown> | undefined;
            return (
              <tr key={i}>
                <td><code>{String(row.metric ?? "")}</code></td>
                <td>{String(row.condition_id ?? "")}</td>
                <td>{num(row.estimate)}</td>
                <td className="muted small">
                  {interval.method && interval.method !== "none"
                    ? `${interval.method} [${num(interval.low)}, ${num(interval.high)}]`
                    : "—"}
                </td>
                <td>{String(row.n ?? "")}</td>
                <td className="muted small">
                  {test
                    ? `${test.name} vs ${test.vs}: p=${num(test.p)} (${test.status})`
                    : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function TrialScreen({ runId, trialId }: { runId: string; trialId: string }) {
  const { data, error, loading, reload } = useAsync(
    () => api.trial(runId, trialId),
    [runId, trialId],
  );
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Trial</h1>
        <p className="muted">
          <Link to={`/runs/${runId}`}>{runId}</Link> · {trialId}
        </p>
        <StatusTag status={data.trial.status} />
      </header>

      <section>
        <h2>Metrics</h2>
        <div className="stats">
          {Object.entries(data.trial.metrics ?? {}).map(([name, value]) => (
            <Stat key={name} label={name} value={String(value)} />
          ))}
        </div>
      </section>

      <section>
        <h2>Timeline</h2>
        {data.trace ? (
          <Timeline trace={data.trace} />
        ) : data.trial.status === "failed" ? (
          <Empty>
            This trial failed and produced no usable trace.
            {data.trial.failure_reason ? ` ${data.trial.failure_reason}` : ""}
          </Empty>
        ) : data.trial.status === "pending" ? (
          <Empty>This trial has not started — the runtime has not claimed it yet.</Empty>
        ) : (
          <Empty>No trace has arrived for this trial.</Empty>
        )}
      </section>

      {data.trace && (
        <section>
          <h2>Value ledger</h2>
          <Json value={(data.trace as { values?: unknown }).values ?? []} />
        </section>
      )}
    </div>
  );
}
