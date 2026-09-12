import { useState } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Loading, Tag } from "../components/ui";

export function Integrations() {
  const { data, error, loading, reload } = useAsync(() => api.runtimes());
  // runtimes the PLATFORM provisions, as opposed to ones the customer connects.
  // Gated by the plan's `hosted_execution` capability, so a failure here is a
  // plan boundary rather than a fault — the section stays hidden.
  const hosted = useAsync(() => api.hostedRuntimes());
  const [label, setLabel] = useState("");
  const [issued, setIssued] = useState<string | null>(null);
  const [poolLabel, setPoolLabel] = useState("");
  const [poolKey, setPoolKey] = useState<string | null>(null);
  const [poolError, setPoolError] = useState("");

  async function connect() {
    const result = await api.connectRuntime(label);
    setIssued(result.ingest_key);
    reload();
  }

  async function provision() {
    setPoolError("");
    try {
      const result = await api.provisionHostedRuntime(poolLabel);
      setPoolKey(result.ingest_key);
      hosted.reload();
    } catch (caught) {
      setPoolError(caught instanceof Error ? caught.message : String(caught));
    }
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

      {!hosted.error && (
        <section>
          <h3>Hosted runtimes</h3>
          <p className="muted small">
            Runtimes the platform provisions and runs for you, as opposed to ones
            you connect from your own infrastructure. Your plan bounds how many.
          </p>
          {poolError && <p className="errors">{poolError}</p>}
          <Card>
            <Field
              label="Pool label"
              hint="Names the managed runtime in listings. The platform still runs no model of its own — the pool executes the agent you bring."
            >
              <input
                id="hosted-label"
                value={poolLabel}
                onChange={(e) => setPoolLabel(e.target.value)}
                placeholder="eu-pool-1"
              />
            </Field>
            <Button onClick={provision} disabled={!poolLabel}>
              Provision a hosted runtime
            </Button>
            {poolKey && (
              <p className="muted small">
                Ingest key issued — shown once, like a connected runtime's.
              </p>
            )}
          </Card>
          {(hosted.data?.hosted_runtimes ?? []).length === 0 ? (
            <Empty>No hosted runtime provisioned.</Empty>
          ) : (
            <ul className="rows">
              {(hosted.data?.hosted_runtimes ?? []).map((runtime) => (
                <li key={runtime.runtime_ref}>
                  <code>{runtime.runtime_ref}</code>
                  <span className="muted">{runtime.runtime_label}</span>
                  <Tag tone="info">hosted</Tag>
                  {runtime.status && <Tag>{runtime.status}</Tag>}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
