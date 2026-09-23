import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Failed, Json, Link, Loading, Tag } from "../components/ui";

/**
 * One suite from the ORG's shared registry, read-only.
 *
 * `/registry/suites/{id}` existed with a client method nothing called: the
 * registry could be listed but an entry could not be opened, so the only way to
 * see what a colleague shared was to guess it would match a local suite of the
 * same id. Read-only on purpose — the org's copy belongs to whoever published
 * it; a workspace edits its OWN copy in the Builder.
 */
export function RegistrySuiteScreen({ id }: { id: string }) {
  const { data, error, status, loading, reload } = useAsync(() => api.registrySuite(id), [id]);
  if (loading) return <Loading />;
  if (error) return <Failed error={error} status={status} onRetry={reload} />;
  if (!data) return null;
  const scenarios = (Array.isArray(data.scenarios) ? data.scenarios : []) as Record<
    string,
    unknown
  >[];
  const conditions = (Array.isArray(data.conditions) ? data.conditions : []) as Record<
    string,
    unknown
  >[];
  return (
    <div className="screen">
      <header className="screen-head">
        <h1>{String(data.name ?? id)}</h1>
        <p className="muted small">
          <Tag tone="info">org registry</Tag> <code>{id}</code>
          {typeof data.version === "string" ? ` · v${data.version}` : ""}
        </p>
        {typeof data.description === "string" && <p className="muted">{data.description}</p>}
        <p className="small">
          <Link to="/suites">← Suites</Link>
        </p>
      </header>
      <section>
        <h2>Scenarios</h2>
        <p className="muted small">
          {scenarios.length === 0
            ? "None inline."
            : scenarios.map((s) => String(s.name ?? "")).join(", ")}
        </p>
        {conditions.length > 0 && (
          <>
            <h2>Conditions</h2>
            <p className="muted small">
              {conditions.map((c) => String(c.id ?? "")).join(", ")}
            </p>
          </>
        )}
      </section>
      <section>
        <h2>Manifest</h2>
        <Json value={data} />
      </section>
    </div>
  );
}
