import { useEffect } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Empty, Failed, Json, Link, Loading, Stat } from "../components/ui";
import { StatusTag, Timeline } from "../components/Timeline";
import { useRunEvents } from "../lib/useRunEvents";

export function Runs() {
  const { data, error, loading, reload } = useAsync(() => api.home());
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  const runs = data?.recent_runs ?? [];
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
              <StatusTag status={run.state} />
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
  const { coverage, trials_by_status, metric_coverage, aggregates } = report.data;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Run {runId}</h1>
        {/* The live state when the stream has one, the loaded state otherwise —
            never both, so the screen cannot show two different answers. */}
        <StatusTag status={progress?.state ?? report.data.state} />
        {progress && !progress.terminal && (
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
      </header>

      {/* Coverage BEFORE aggregates, per statistics.md §5: a report that shows
          rates without saying what fraction of the plan produced them invites
          reading a partial run as a complete one. */}
      <section>
        <h2>Coverage</h2>
        <div className="stats">
          <Stat label="completed" value={coverage.completed} />
          <Stat label="planned" value={coverage.planned} />
          {Object.entries(trials_by_status).map(([status, n]) => (
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
          <Json value={aggregates} />
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
        ) : (
          /* A planned trial with no trace is a real state, not an error: the
             runtime has not pushed it yet, or it failed before producing one. */
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
