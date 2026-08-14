import { useState } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Loading, Tag } from "../components/ui";

export function Integrations() {
  const { data, error, loading, reload } = useAsync(() => api.runtimes());
  const [label, setLabel] = useState("");
  const [issued, setIssued] = useState<string | null>(null);

  async function connect() {
    const result = await api.connectRuntime(label);
    setIssued(result.ingest_key);
    reload();
  }

  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Integrations</h1>
        <p className="muted">
          Lab assigns experiments; a connected runtime claims one and runs it
          beside your agent. Lab never executes the agent.
        </p>
      </header>
      <Card>
        <Field label="Runtime label" hint="A name to recognize this connection — e.g. your agent's build. Lab does not call any model; the label is just for display.">
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="support-bot v3" />
        </Field>
        <Button onClick={connect} disabled={!label}>
          Connect a runtime
        </Button>
        {issued && (
          <p className="muted small">
            Ingest key issued — it is shown once. Give it to the adapter; it is
            not stored anywhere you can read it back.
          </p>
        )}
      </Card>
      {(data?.runtimes ?? []).length === 0 ? (
        <Empty>No runtime connected.</Empty>
      ) : (
        <ul className="rows">
          {(data?.runtimes ?? []).map((runtime) => (
            <li key={runtime.runtime_ref}>
              <code>{runtime.runtime_ref}</code>
              <span className="muted">{runtime.runtime_label}</span>
              {runtime.status && <Tag>{runtime.status}</Tag>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
