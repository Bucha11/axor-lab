import { useState } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Loading, Tag } from "../components/ui";
import { ShownOnce } from "../components/ShownOnce";

export function Integrations() {
  const { data, error, status, loading, reload } = useAsync(() => api.runtimes());
  // runtimes the PLATFORM provisions, as opposed to ones the customer connects.
  // Gated by the plan's `hosted_execution` capability, so a failure here is a
  // plan boundary rather than a fault — the section stays hidden.
  const hosted = useAsync(() => api.hostedRuntimes());
  const [label, setLabel] = useState("");
  const [issued, setIssued] = useState<string | null>(null);
  const [connectError, setConnectError] = useState("");
  const [poolLabel, setPoolLabel] = useState("");
  const [poolKey, setPoolKey] = useState<string | null>(null);
  const [poolError, setPoolError] = useState("");
  // one flag per door: a second click while the first request is in flight
  // minted a SECOND runtime and a second key, and only the last one was shown
  const [busy, setBusy] = useState<"" | "connect" | "provision">("");

  async function connect() {
    setBusy("connect");
    setConnectError("");
    setIssued(null);
    try {
      const result = await api.connectRuntime(label);
      setIssued(result.ingest_key);
      setLabel("");
      reload();
    } catch (caught) {
      // a failed connect used to be an unhandled rejection: the button did
      // nothing, visibly, and the console was the only place that said why
      setConnectError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  async function provision() {
    setBusy("provision");
    setPoolError("");
    setPoolKey(null);
    try {
      const result = await api.provisionHostedRuntime(poolLabel);
      setPoolKey(result.ingest_key);
      setPoolLabel("");
      hosted.reload();
      reload();
    } catch (caught) {
      setPoolError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  if (loading) return <Loading />;
  if (error) return <Failed error={error} status={status} onRetry={reload} />;

  // /runtimes lists EVERY runtime, hosted ones included, so with the hosted
  // section below showing them too each one appeared twice. Where that section
  // renders, it owns them; where the plan hides it, they stay here, labelled.
  const hostedShown = !hosted.error;
  const connected = (data?.runtimes ?? []).filter((r) => !(hostedShown && r.hosted));

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
        <Button onClick={connect} disabled={!label || busy !== ""}>
          {busy === "connect" ? "Connecting…" : "Connect a runtime"}
        </Button>
        {connectError && <p className="errors">{connectError}</p>}
        {issued && (
          <ShownOnce
            value={issued}
            label="Ingest key — give it to the adapter."
            testId="ingest-key"
          />
        )}
      </Card>
      {connected.length === 0 ? (
        <Empty>No runtime connected.</Empty>
      ) : (
        <ul className="rows">
          {connected.map((runtime) => (
            <li key={runtime.runtime_ref}>
              <code>{runtime.runtime_ref}</code>
              <span className="muted">{runtime.runtime_label}</span>
              {runtime.hosted && <Tag tone="info">hosted</Tag>}
              {runtime.status && <Tag>{runtime.status}</Tag>}
            </li>
          ))}
        </ul>
      )}

      {hostedShown && (
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
            <Button onClick={provision} disabled={!poolLabel || busy !== ""}>
              {busy === "provision" ? "Provisioning…" : "Provision a hosted runtime"}
            </Button>
            {poolKey && (
              <ShownOnce
                value={poolKey}
                label="Hosted runtime ingest key — give it to the pool's worker."
                testId="hosted-ingest-key"
              />
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
