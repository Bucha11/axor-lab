import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Card, Empty, Failed, Json, Link, Loading } from "../components/ui";

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
  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Artifact {id}</h1>
      </header>
      {reproduce && (
        <section>
          <h2>Reproduce</h2>
          <pre className="json">{String(reproduce.command ?? "")}</pre>
          <p className="muted small">claim: {String(reproduce.reproducibility ?? "")}</p>
        </section>
      )}
      <section>
        <h2>Evidence</h2>
        <Json value={data.evidence_cases ?? []} />
      </section>
      <section>
        <h2>Invariants</h2>
        <Json value={data.regressions ?? []} />
      </section>
    </div>
  );
}
