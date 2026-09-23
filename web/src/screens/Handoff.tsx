import { useState } from "react";
import { api, type CheckReport, type HandoffPackage, type Json } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Loading, Stat, Tag } from "../components/ui";
import { saveBlob, saveFile } from "../lib/save";

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

type Imported = Awaited<ReturnType<typeof api.importIncident>>;

/** What an incident import RETURNED. Nothing is stored server-side — the
 * bundle, its traces and the file map come back in the response and nowhere
 * else — so showing only the bundle id threw the result away: the user saw an
 * id that no other screen could open. */
function ImportedIncident({ result }: { result: Imported }) {
  const trials = Array.isArray(result.bundle?.trials) ? (result.bundle.trials as unknown[]) : [];
  const files = Object.keys(result.files ?? {});
  return (
    <div data-testid="incident-result">
      <p className="muted small">
        Imported <code>{result.bundle_id}</code> from trace{" "}
        <code>{result.trace_id}</code> — replay status{" "}
        <Tag tone={result.replay_status === "match" ? "success" : "warning"}>
          {result.replay_status}
        </Tag>
      </p>
      <div className="grid">
        <Stat label="Trials" value={trials.length} />
        <Stat label="Traces" value={(result.traces ?? []).length} />
        <Stat label="Files" value={files.length} />
      </div>
      {files.length > 0 && (
        <ul className="rows">
          {files.map((name) => (
            <li key={name}>
              <code>{name}</code>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">
        Not stored anywhere — save it now to keep it.
      </p>
      <Button
        variant="secondary"
        onClick={() =>
          saveFile(
            `${result.bundle_id}-incident.json`,
            JSON.stringify({ bundle: result.bundle, traces: result.traces }, null, 2),
          )}
      >
        Download bundle + traces (JSON)
      </Button>{" "}
      <Button
        variant="secondary"
        onClick={() =>
          saveFile(`${result.bundle_id}-files.json`, JSON.stringify(result.files, null, 2))}
      >
        Download file map (JSON)
      </Button>
    </div>
  );
}

export function Handoff() {
  // EVERY run, not Home's `recent_runs`: that is the launchpad's last five, so
  // a run older than that could not be handed off from the screen at all
  const { data, error, status, loading, reload } = useAsync(() => api.runs());
  const [pkg, setPkg] = useState<HandoffPackage | null>(null);
  const [runId, setRunId] = useState("");
  const [report, setReport] = useState<CheckReport | null>(null);
  const [allowUnsigned, setAllowUnsigned] = useState(true);
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  // the second funnel and the offline package check both take a document the
  // user already holds, so they are pasted rather than picked from a list.
  const [incident, setIncident] = useState("");
  const [imported, setImported] = useState<Imported | null>(null);
  const [pkgText, setPkgText] = useState("");
  // OFF by default, like the CLI's `--allow-bare`: a bare package carries no
  // proof objects, and accepting one is a decision the reader makes, not one
  // the screen made for them on every click
  const [allowBare, setAllowBare] = useState(false);
  const [pkgReport, setPkgReport] = useState<CheckReport | null>(null);

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

  const importIncident = () =>
    guard("incident", async () => {
      const parsed = JSON.parse(incident) as {
        trace: Json; scenario: Json; manifests: Json; condition: Json;
      };
      setImported(await api.importIncident(parsed));
    });

  const verifyPackage = () =>
    guard("package", async () => {
      setPkgReport(await api.verifyPackage(JSON.parse(pkgText) as Json, allowBare));
    });

  if (loading) return <Loading />;
  if (error) return <Failed error={error} status={status} onRetry={reload} />;
  const runs = data?.runs ?? [];

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
            onClick={() => guard("zip", async () =>
              saveBlob(`${runId}-handoff.zip`, await api.exportHandoffZip(runId)))}
            disabled={busy !== ""}
          >
            {busy === "zip" ? "Packing…" : "Download handoff (.zip)"}
          </Button>{" "}
          <Button
            variant="secondary"
            onClick={() => saveFile(`${runId}-handoff.json`, JSON.stringify(pkg.files, null, 2))}
          >
            Download file map (JSON)
          </Button>
          <p className="muted small">
            The zip is the handoff directory — the same tree the CLI writes, so{" "}
            <code>axor-lab verify-cp-export &lt;dir&gt;</code> checks what you
            received. The JSON is the same bytes as one nested file map, for a
            reader who wants to inspect it without unpacking.
          </p>
        </Card>
      )}

      <Card>
        <h4>Verify a reproduction package</h4>
        <p className="muted small">
          A downloaded package, checked without trusting the server that served
          it. A published package is checked for content hashes, bit-identical
          replay, and every proof object bound; a bare one carries no proof
          objects, so only the first two — and only if you accept bare below.
          Paste the package JSON.
        </p>
        <Field
          label="Package JSON"
          hint="A publication page's Download (published), or an artifact's Download reproduction package (bare)."
        >
          <textarea
            id="package-json"
            rows={4}
            value={pkgText}
            onChange={(e) => setPkgText(e.target.value)}
            placeholder='{"schema_version": "axor-reproduction-package/v1", …}'
          />
        </Field>
        <Field
          label="Accept a bare package"
          hint="A bare package ({bundle, traces}, what an artifact's Download reproduction package gives) has no proof objects, so only integrity and replay can be checked — the same as axor-lab verify --allow-bare. Off, a bare package is refused."
        >
          <input
            id="package-allow-bare"
            type="checkbox"
            checked={allowBare}
            onChange={(e) => setAllowBare(e.target.checked)}
          />
        </Field>
        <Button onClick={verifyPackage} disabled={busy !== "" || !pkgText}>
          {busy === "package" ? "Verifying…" : "Verify package"}
        </Button>
        {pkgReport && (
          <>
            <p className="muted small">
              Outcome:{" "}
              <Tag
                tone={
                  pkgReport.outcome === "ok"
                    ? "success"
                    : pkgReport.outcome === "unverified"
                      ? "warning"
                      : "danger"
                }
              >
                {pkgReport.outcome}
              </Tag>
            </p>
            <Checks report={pkgReport} />
          </>
        )}
      </Card>

      <Card>
        <h4>Import an incident</h4>
        <p className="muted small">
          The second funnel: a production trace becomes a bundle you can test a
          policy against. The recorded condition is required and used verbatim —
          reconstructing it would lose enforcement mode, policy and allowlist.
          Nothing is stored until the incident replays under it.
        </p>
        <Field
          label="Incident JSON"
          hint="An object with trace, scenario, manifests and condition."
        >
          <textarea
            id="incident-json"
            rows={4}
            value={incident}
            onChange={(e) => setIncident(e.target.value)}
            placeholder='{"trace": {…}, "scenario": {…}, "manifests": […], "condition": {…}}'
          />
        </Field>
        <Button onClick={importIncident} disabled={busy !== "" || !incident}>
          {busy === "incident" ? "Importing…" : "Import incident"}
        </Button>
        {imported && <ImportedIncident result={imported} />}
      </Card>

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
