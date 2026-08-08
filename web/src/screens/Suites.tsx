import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Card, Failed, Loading, Tag } from "../components/ui";
import { navigate } from "../lib/router";

export function Suites() {
  const { data, error, loading, reload } = useAsync(() => api.suites());
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
            <p className="muted">{suite.description}</p>
            {!suite.available && <p className="muted small">{suite.reason}</p>}
            {(suite.capabilities ?? []).map((c) => (
              <Tag key={c} tone="info">
                {c}
              </Tag>
            ))}
          </Card>
        ))}
      </div>
    </div>
  );
}
