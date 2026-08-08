import { useState } from "react";
import { api, type InvariantOutcome } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Json, Link, Loading, Tag, outcomeTone } from "../components/ui";

export function RegressionList() {
  const { data, error, loading, reload } = useAsync(() => api.regressions());
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  const rows = data?.regressions ?? [];
  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Regressions</h1>
        <p className="muted">
          A regression is an EXECUTABLE INVARIANT. `expectation` is prose for
          humans and is never evaluated; `rule` is what runs.
        </p>
      </header>
      {rows.length === 0 ? (
        <Empty>Nothing pinned yet.</Empty>
      ) : (
        <ul className="rows">
          {rows.map((row) => (
            <li key={row.id}>
              <Link to={`/regressions/${row.id}`}>{row.name}</Link>
              <code>{row.id}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RegressionScreen({ id }: { id: string }) {
  const { data, error, loading, reload } = useAsync(() => api.regression(id), [id]);
  const [runId, setRunId] = useState("");
  const [outcome, setOutcome] = useState<InvariantOutcome | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  async function check() {
    setRunError(null);
    setOutcome(null);
    try {
      setOutcome(await api.runRegression(id, runId));
    } catch (exc) {
      setRunError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>{String(data.name)}</h1>
        <code>{id}</code>
      </header>

      <section>
        <h2>Rule</h2>
        <Json value={data.rule} />
        {data.expectation ? (
          <p className="muted">
            <strong>Expectation (prose, never evaluated):</strong>{" "}
            {String(data.expectation)}
          </p>
        ) : null}
      </section>

      <Card>
        <h3>Check against a run</h3>
        <Field label="Run id">
          <input value={runId} onChange={(e) => setRunId(e.target.value)} />
        </Field>
        <Button onClick={check} disabled={!runId}>
          Run the invariant
        </Button>
        {runError && <Failed error={runError} />}
        {outcome && (
          <div className="outcome">
            <Tag tone={outcomeTone(outcome.status)}>{outcome.status}</Tag>
            <p>{outcome.detail}</p>
            <p className="muted small">
              {outcome.trials_checked} trial(s) checked, {outcome.trials_failed} failing
            </p>
            {/* `error` is not `failed`: the invariant could not be EVALUATED.
                Saying which is the difference between "your change broke this"
                and "nobody measured the thing this bounds". */}
            {outcome.status === "error" && (
              <p className="muted small">
                This invariant was not evaluated. It has neither been satisfied
                nor violated.
              </p>
            )}
          </div>
        )}
      </Card>

      {Array.isArray(data.history) && (data.history as unknown[]).length > 0 && (
        <section>
          <h2>History</h2>
          <Json value={data.history} />
        </section>
      )}
    </div>
  );
}
