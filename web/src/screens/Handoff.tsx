import { useState } from "react";
import { api, type CheckReport, type HandoffPackage } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Loading, Stat, Tag } from "../components/ui";

/** Verification is a SEQUENCE of distinct guarantees, and a screen that merges
 * them teaches the reader to conflate them: an export can be intact without
 * being authentic, and derivability is neither. One row per check, each keeping
 * its own word. */
function Checks({ report }: { report: CheckReport }) {
  return (
    <ul className="rows">
      {report.checks.map((check) => (
        <li key={check.name + check.message}>
          <code>{check.name}</code>
          <Tag
            tone={
              check.status === "ok"
                ? "success"
                : check.status === "unverified"
                  ? "warning"
                  : "danger"
            }
          >
            {check.status}
          </Tag>
          <span className="muted small">{check.message}</span>
        </li>
      ))}
    </ul>
  );
}

function download(name: string, body: string) {
  const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function Handoff() {
  const { data, error, loading, reload } = useAsync(() => api.home());
  const [pkg, setPkg] = useState<HandoffPackage | null>(null);
  const [runId, setRunId] = useState("");
  const [report, setReport] = useState<CheckReport | null>(null);
  const [allowUnsigned, setAllowUnsigned] = useState(true);
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");

  async function guard(label: string, work: () => Promise<void>) {
    setBusy(label);
    setFailure("");
    try {
      await work();
    } catch (caught) {
      // an export the evidence does not EARN is an answer, not a crash — say
      // which, rather than leaving a dead button
      setFailure(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  const exportRun = (id: string) =>
    guard("export", async () => {
      setReport(null);
      setRunId(id);
      setPkg(await api.exportHandoff(id));
    });

  const verify = () =>
    guard("verify", async () => {
      if (pkg) setReport(await api.verifyHandoff(pkg.files, allowUnsigned));
    });

  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  const runs = data?.recent_runs ?? [];

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Control Plane handoff</h1>
        <p className="muted">
          What carries over to production — the validated policy, the tool
          manifests and the pinned regressions — plus the evidence to recompute
          it from. Real tool bindings, credentials and topology are NOT reused;
          the package says so in production-todo.md.
        </p>
      </header>

      {failure && <Failed error={failure} />}

      <Card>
        <h4>Export from a run</h4>
        {runs.length === 0 ? (
          <Empty>No runs yet — dispatch a suite first.</Empty>
        ) : (
          <ul className="rows">
            {runs.map((run) => (
              <li key={run.run_id}>
                <code>{run.run_id}</code>
                <Tag>{run.state}</Tag>
                <Button
                  variant="secondary"
                  disabled={busy !== ""}
                  onClick={() => exportRun(run.run_id)}
                >
                  {busy === "export" && runId === run.run_id ? "Exporting…" : "Export handoff"}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {pkg && (
        <Card>
          <h4>Handoff for {runId}</h4>
          <div className="grid">
            <Stat label="Condition" value={pkg.condition_id} />
            <Stat label="Baseline" value={pkg.baseline_condition_id} />
            <Stat label="Files" value={Object.keys(pkg.files).length} />
            <Stat label="Regressions carried" value={pkg.regressions_carried} />
          </div>
          <p className="muted small">
            {pkg.signed
              ? "Signed — a reader can confirm who released this exact handoff."
              : "Unsigned — integrity and derivability only. Anyone can produce an internally consistent package, so this proves nothing about WHO built it."}
          </p>
          <p className="muted small">
            {pkg.earned_bridge
              ? "Earned bridge: governance changed the outcome against the baseline — run THIS config in production."
              : "No aggregate shows governance changed an outcome yet; the bridge surfaces once one does."}
          </p>
          <Field
            label="Accept an unsigned manifest"
            hint="Without this, an unsigned handoff verifies as UNVERIFIED rather than passing — integrity is not authenticity."
          >
            <input
              id="handoff-allow-unsigned"
              type="checkbox"
              checked={allowUnsigned}
              onChange={(e) => setAllowUnsigned(e.target.checked)}
            />
          </Field>
          <Button onClick={verify} disabled={busy !== ""}>
            {busy === "verify" ? "Verifying…" : "Verify this handoff"}
          </Button>{" "}
          <Button
            variant="secondary"
            onClick={() => download(`${runId}-handoff.json`, JSON.stringify(pkg.files, null, 2))}
          >
            Download files
          </Button>
        </Card>
      )}

      {report && (
        <Card>
          <h4>
            Verification{" "}
            <Tag
              tone={
                report.outcome === "ok"
                  ? "success"
                  : report.outcome === "unverified"
                    ? "warning"
                    : "danger"
              }
            >
              {report.outcome}
            </Tag>
          </h4>
          <Checks report={report} />
        </Card>
      )}
    </div>
  );
}
