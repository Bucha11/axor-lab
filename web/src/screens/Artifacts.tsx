import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Card, Empty, Failed, Json, Link, Loading, Stat } from "../components/ui";

export function ArtifactList() {
  const { data, error, loading, reload } = useAsync(() => api.artifacts());
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  const rows = data?.artifacts ?? [];
  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Artifacts</h1>
        {/* The user-facing noun is Artifact. "Bundle" is banned from new
            surfaces (terminology lint) — it is the body an artifact wraps. */}
        <p className="muted">The portable output of a run: trials, metrics, evidence, invariants, and how to reproduce it.</p>
      </header>
      {rows.length === 0 ? (
        <Empty>No artifacts yet.</Empty>
      ) : (
        <div className="grid">
          {rows.map((row) => (
            <Card key={row.artifact_id}>
              <h4>
                <Link to={`/artifacts/${row.artifact_id}`}>{row.artifact_id}</Link>
              </h4>
              {row.created && <p className="muted small">{row.created}</p>}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

export function ArtifactScreen({ id }: { id: string }) {
  const { data, error, loading, reload } = useAsync(() => api.artifact(id), [id]);
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (!data) return null;
  const reproduce = data.reproduce as Record<string, unknown> | undefined;
  const suite = data.suite as Record<string, unknown> | undefined;
  const bundle = (data.bundle ?? {}) as Record<string, unknown>;
  const aggregates = (bundle.aggregates ?? []) as Record<string, unknown>[];
  const trials = (bundle.trials ?? []) as Record<string, unknown>[];
  const num = (v: unknown) => (typeof v === "number" ? v.toFixed(3) : "—");
  const byStatus: Record<string, number> = {};
  for (const t of trials) {
    const s = String(t.status ?? "unknown");
    byStatus[s] = (byStatus[s] ?? 0) + 1;
  }

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Artifact {id}</h1>
        {/* Overview from the artifact body, not a JSON dump — what suite ran,
            when, and how many trials of what status it carries. */}
        <p className="muted small">
          {suite ? `${suite.name} (${suite.id})` : "suite unknown"}
          {data.created ? ` · ${data.created}` : ""}
        </p>
      </header>

      <section>
        <h2>Trials</h2>
        <div className="stats">
          {Object.entries(byStatus).map(([status, n]) => (
            <Stat key={status} label={status} value={n} />
          ))}
        </div>
      </section>

      {aggregates.length > 0 && (
        <section>
          <h2>Metrics</h2>
          <div className="table-scroll">
            <table className="agg-table">
              <thead>
                <tr><th>metric</th><th>arm</th><th>estimate</th><th>n</th></tr>
              </thead>
              <tbody>
                {aggregates.map((row, i) => (
                  <tr key={i}>
                    <td><code>{String(row.metric ?? "")}</code></td>
                    <td>{String(row.condition_id ?? "")}</td>
                    <td>{num(row.estimate)}</td>
                    <td>{String(row.n ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {reproduce && (
        <section>
          <h2>Reproduce</h2>
          <pre className="json">{String(reproduce.command ?? "")}</pre>
          <p className="muted small">claim: {String(reproduce.reproducibility ?? "")}</p>
        </section>
      )}
      {Array.isArray(data.evidence_cases) && (data.evidence_cases as unknown[]).length > 0 && (
        <section>
          <h2>Evidence</h2>
          <Json value={data.evidence_cases} />
        </section>
      )}
      {Array.isArray(data.regressions) && (data.regressions as unknown[]).length > 0 && (
        <section>
          <h2>Invariants</h2>
          <Json value={data.regressions} />
        </section>
      )}
    </div>
  );
}
