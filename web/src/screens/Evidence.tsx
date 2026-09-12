import { useState } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Json, Link, Loading, Tag } from "../components/ui";

export function EvidenceList() {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setCreateError(null);
    try {
      await api.createEvidence(JSON.parse(draft));
      setDraft("");
      reload();
    } catch (exc) {
      setCreateError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }
  const { data, error, loading, reload } = useAsync(() => api.evidence());
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  const cases = data?.evidence_cases ?? [];
  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Evidence</h1>
        <p className="muted">
          An EvidenceCase is a curated investigation of ONE trial — a latency
          spike, a budget overflow, a planner failure, an injection that landed.
        </p>
      </header>
      {cases.length === 0 ? (
        <Empty>No cases yet. Curate one from a trial.</Empty>
      ) : (
        <div className="grid">
          {cases.map((row) => (
            <Card key={row.id}>
              <div className="row-between">
                <h4>
                  <Link to={`/evidence/${row.id}`}>{row.title}</Link>
                </h4>
                {row.severity && <Tag tone="warning">{row.severity}</Tag>}
              </div>
              <Tag tone="info">{row.kind}</Tag>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <h4>Create from JSON</h4>
        <p className="muted small">
          An EvidenceCase is a curated investigation of ONE trial — a latency spike, a budget overrun, an injection that landed. Authored elsewhere and pasted here.
        </p>
        <Field label="evidence-case/v1" hint="Validated by the server against its schema; a malformed document is refused, not stored.">
          <textarea
            id="evidence-json"
            rows={4}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder='{\"schema_version\": \"evidence-case/v1\", \"id\": \"ec_1\", …}'
          />
        </Field>
        <Button onClick={create} disabled={busy || !draft}>
          {busy ? "Creating…" : "Create EvidenceCase"}
        </Button>
        {createError && <p className="errors">{createError}</p>}
      </Card>
    </div>
  );
}

interface TimelineEntry {
  seq: number;
  actor?: string;
  label?: string;
  note?: string;
  highlight?: boolean;
}

export function EvidenceScreen({ id }: { id: string }) {
  const { data, error, loading, reload } = useAsync(() => api.evidenceCase(id), [id]);
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (!data) return null;

  const timeline = (data.timeline as TimelineEntry[] | undefined) ?? [];
  const governance = data.governance as Record<string, unknown> | undefined;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>{String(data.title)}</h1>
        <Tag tone="info">{String(data.kind)}</Tag>
        {data.severity ? <Tag tone="warning">{String(data.severity)}</Tag> : null}
      </header>

      {data.summary ? <p>{String(data.summary)}</p> : null}

      <section>
        <h2>Metrics</h2>
        <Json value={data.metrics ?? {}} />
      </section>

      <section>
        <h2>Timeline</h2>
        {/* Each entry POINTS AT a trace event by sequence. The case carries no
            copy of the narrative, so it cannot drift from the trace — which is
            why the seq is rendered as the anchor and not decoration. */}
        <ol className="timeline">
          {timeline.map((entry) => (
            <li key={entry.seq} className={entry.highlight ? "gate" : ""}>
              <span className="seq">{entry.seq}</span>
              <span className="kind">{entry.actor}</span>
              <span>{entry.label}</span>
              {entry.note && <span className="muted small">{entry.note}</span>}
            </li>
          ))}
        </ol>
      </section>

      {/* Present only for a governance-capable run; absent for every other kind
          of investigation, which is the common case. */}
      {governance && (
        <section>
          <h2>Governance</h2>
          <Json value={governance} />
        </section>
      )}
    </div>
  );
}
