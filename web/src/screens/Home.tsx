import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Failed, Link, Loading, Stat, Tag } from "../components/ui";
import { navigate } from "../lib/router";

/** The step the backend derived, in the words a user reads. The DERIVATION
 * lives on the server (`home_payload`); duplicating the branching here would
 * let the screen tell a different story than the API. */
const STEP_COPY: Record<string, { title: string; body: string; action?: [string, string] }> = {
  connect_runtime: {
    title: "Connect a runtime",
    body: "Lab assigns experiments; your runtime runs them beside your agent and pushes traces back. Or try a suite on simulated tools first.",
    action: ["Open the Playground", "/playground"],
  },
  run_a_suite: {
    title: "Run a suite",
    body: "A runtime is connected. Pick a suite and start a run.",
    action: ["Browse suites", "/suites"],
  },
  inspect_a_run: {
    title: "Inspect a run",
    body: "You have results. Open a trial, and curate what is worth keeping as an EvidenceCase.",
    action: ["See runs", "/runs"],
  },
  pin_a_regression: {
    title: "Pin what must not regress",
    body: "Turn a finding into an executable invariant that runs against every future run.",
    action: ["See regressions", "/regressions"],
  },
};

export function Home() {
  const { data, error, loading, reload } = useAsync(() => api.home());
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (!data) return null;

  const step = STEP_COPY[data.onboarding_step] ?? {
    title: data.onboarding_step,
    body: "",
  };

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Axor Lab</h1>
        <p className="muted">A reproducible experiment platform for AI agents.</p>
      </header>

      <Card>
        <h3>{step.title}</h3>
        <p className="muted">{step.body}</p>
        {step.action && (
          <Button onClick={() => navigate(step.action![1])}>{step.action[0]}</Button>
        )}
      </Card>

      <section>
        <h2>Suites</h2>
        {/* Home lists only what is available. It is a launch surface: a card
            here is a button, and offering one that runs nothing is worse than
            not showing it. The full catalog, unavailable entries included,
            is the Suites screen. */}
        <div className="grid">
          {data.suites.map((suite) => (
            <Card key={suite.id} onClick={() => navigate(`/suites/${suite.id}`)}>
              <h4>{suite.name}</h4>
              <p className="muted">{suite.description}</p>
              {(suite.capabilities ?? []).map((c) => (
                <Tag key={c} tone="info">
                  {c}
                </Tag>
              ))}
            </Card>
          ))}
        </div>
      </section>

      <section>
        <h2>Workspace</h2>
        <div className="stats">
          {Object.entries(data.counts).map(([label, value]) => (
            <Stat key={label} label={label.replace(/_/g, " ")} value={value} />
          ))}
        </div>
      </section>

      {data.recent_runs.length > 0 && (
        <section>
          <h2>Recent runs</h2>
          <ul className="rows">
            {data.recent_runs.map((run) => (
              <li key={run.run_id}>
                <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                <Tag>{run.state}</Tag>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
