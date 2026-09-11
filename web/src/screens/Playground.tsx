import { useState } from "react";
import { api, type PlaygroundResult } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Failed, Field, Json, Loading, Tag } from "../components/ui";
import { Timeline } from "../components/Timeline";

/**
 * One trial, for inspection. NOT a run — no repeats, no aggregation, nothing
 * stored. The screen says so where the user can see it, because the payload
 * says so too (`counted_in_a_run: false`) and a debugging click that quietly
 * became evidence is the failure that flag exists to prevent.
 */
export function Playground() {
  const catalog = useAsync(() => api.suites());
  const [suiteId, setSuiteId] = useState("");
  const [scenario, setScenario] = useState("");
  const [seed, setSeed] = useState("");
  const [result, setResult] = useState<PlaygroundResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const available = (catalog.data?.suites ?? []).filter((s) => s.available);
  const chosen = suiteId || available[0]?.id || "";

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await api.playground({
          suite_id: chosen,
          ...(scenario ? { scenario } : {}),
          ...(seed ? { seed } : {}),
        }),
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setRunning(false);
    }
  }

  if (catalog.loading) return <Loading />;
  if (catalog.error) return <Failed error={catalog.error} onRetry={catalog.reload} />;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Playground</h1>
        <p className="muted">
          Execute a single trial and read its trace. Nothing is stored and
          nothing is aggregated.
        </p>
      </header>

      <Card>
        <div className="form-row">
          <Field label="Suite">
            <select value={chosen} onChange={(e) => setSuiteId(e.target.value)}>
              {available.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Scenario (optional)">
            <input
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              placeholder="first declared"
            />
          </Field>
          <Field label="Seed (optional)">
            <input value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="s000" />
          </Field>
        </div>
        <Button onClick={run} disabled={running || !chosen}>
          {running ? "Running…" : "Run one trial"}
        </Button>
      </Card>

      {error && <Failed error={error} />}

      {result && (
        <>
          <Card>
            <div className="row-between">
              <h3>Trial</h3>
              <Tag tone={result.counted_in_a_run ? "warning" : "info"}>
                {result.counted_in_a_run ? "counted in a run" : "not counted in a run"}
              </Tag>
            </div>
            <Json value={result.trial} />
          </Card>
          {result.trace && (
            <Card>
              <h3>Trace</h3>
              <Timeline trace={result.trace} />
            </Card>
          )}
          {result.evidence_cases.length > 0 && (
            <Card>
              <h3>What the suite would curate</h3>
              <Json value={result.evidence_cases} />
            </Card>
          )}
        </>
      )}
    </div>
  );
}
