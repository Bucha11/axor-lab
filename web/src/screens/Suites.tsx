import { useState } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Failed, Loading, Tag } from "../components/ui";
import { navigate } from "../lib/router";

export function Suites() {
  const { data, error, loading, reload } = useAsync(() => api.suites());
  // the org's SHARED catalog, distinct from this workspace's own suites. It
  // fails for a workspace whose plan does not grant `private_registry`, and that
  // is not an error worth a red screen — the section simply does not appear.
  const registry = useAsync(() => api.registrySuites());
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [published, setPublished] = useState<string | null>(null);

  async function createSuite() {
    // derive from the `blank` built-in — a guaranteed-valid starter — rather
    // than hand-building a manifest here that would drift from the schema (the
    // scenario needs a tool, a fixture, a success predicate). A fresh id +
    // origin makes it the workspace's own.
    const id = `suite-${Math.floor(Date.now() / 1000)}`;
    setBusy(true);
    setCreateError(null);
    try {
      const base = await api.suite("blank");
      await api.createSuite({ ...base, id, name: id, origin: "workspace" });
      navigate(`/suites/${id}`);
    } catch (exc) {
      setCreateError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    await api.deleteSuite(id);
    reload();
  }

  async function publishToOrg(id: string) {
    setBusy(true);
    setCreateError(null);
    try {
      const result = await api.publishSuiteToOrg(id);
      setPublished(result.id);
      registry.reload();
    } catch (exc) {
      setCreateError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Suites</h1>
        <p className="muted">
          An Experiment Suite is one portable manifest: scenarios, agent,
          execution strategy, evaluators, metrics, artifact layout, invariants.
        </p>
        <div className="row">
          <Button onClick={createSuite} disabled={busy}>+ New suite</Button>
        </div>
        {createError && <p className="errors">{createError}</p>}
      </header>
      <div className="grid">
        {(data?.suites ?? []).map((suite) => (
          <Card
            key={suite.id}
            onClick={suite.available ? () => navigate(`/suites/${suite.id}`) : undefined}
          >
            <div className="row-between">
              <h4>{suite.name}</h4>
              {/* The catalog says NOT YET out loud. Rendering these three as
                  ordinary cards offers suites that run nothing; dropping them
                  reads as "the feature was cut" to anyone holding the design
                  board. */}
              {!suite.available && <Tag tone="warning">Not yet</Tag>}
            </div>
            {/* id + origin, so two suites that share a name (a fork of a
                built-in) are still distinguishable */}
            <p className="muted small">
              <code>{suite.id}</code>
              {suite.origin && ` · ${suite.origin}`}
            </p>
            <p className="muted">{suite.description}</p>
            {!suite.available && <p className="muted small">{suite.reason}</p>}
            {(suite.capabilities ?? []).map((c) => (
              <Tag key={c} tone="info">
                {c}
              </Tag>
            ))}
            {suite.origin === "workspace" && (
              <>
                <button
                  type="button"
                  className="item-remove"
                  onClick={(event) => {
                    event.stopPropagation();
                    remove(suite.id);
                  }}
                >
                  Delete
                </button>
                <button
                  type="button"
                  className="item-remove"
                  onClick={(event) => {
                    event.stopPropagation();
                    publishToOrg(suite.id);
                  }}
                >
                  Publish to org
                </button>
              </>
            )}
          </Card>
        ))}
      </div>
      {published && (
        <p className="muted small">
          Published <code>{published}</code> to the org registry — every workspace
          in the org can see it now. The copy is the org's; editing yours does not
          change theirs.
        </p>
      )}
      {(registry.data?.suites ?? []).length > 0 && (
        <section>
          <h3>Org registry</h3>
          <p className="muted small">
            Suites your organization shares. Visible to every workspace in the
            org; owned by none of them individually.
          </p>
          <div className="grid">
            {(registry.data?.suites ?? []).map((suite) => (
              <Card key={`org-${suite.id}`}>
                <h4>{suite.name}</h4>
                <p className="muted small">
                  <code>{suite.id}</code>
                  {suite.origin && ` · ${suite.origin}`}
                </p>
                <p className="muted">{suite.description}</p>
              </Card>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
