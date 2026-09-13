import { useState } from "react";
import { api, type PublishResult } from "../lib/api";
import { saveFile } from "../lib/save";
import { useAsync } from "../lib/useAsync";
import {
  Button, Card, Empty, Failed, Field, Json, Link, Loading, Stat, Tag,
} from "../components/ui";

/**
 * Handing the artifact over.
 *
 * This screen could render an artifact and not give it to anyone: no download,
 * no publish, and no `publish` anywhere in the client at all — so the only way
 * out of the hosted face was a shell. The three doors here are the same verbs
 * the CLI has, through the same service, so both faces mint the same claims.
 */
function Export({ id }: { id: string }) {
  const [question, setQuestion] = useState("");
  const [visibility, setVisibility] = useState("unlisted");
  const [server, setServer] = useState("");
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  const [minted, setMinted] = useState<PublishResult | null>(null);

  async function run(label: string, work: () => Promise<void>) {
    setBusy(label);
    setFailure("");
    try {
      await work();
    } catch (exc) {
      setFailure(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy("");
    }
  }

  return (
    <section>
      <h2>Export</h2>
      <Card>
        <div className="row">
          <Button
            variant="secondary"
            disabled={busy !== ""}
            onClick={() => run("artifact", async () =>
              saveFile(`${id}.json`, await api.artifactDownload(id)))}
          >
            Download artifact
          </Button>
          <Button
            variant="secondary"
            disabled={busy !== ""}
            onClick={() => run("package", async () =>
              saveFile(`${id}-package.json`, await api.artifactPackage(id)))}
          >
            Download reproduction package
          </Button>
        </div>
        <p className="muted small">
          The package is the bundle plus every trace body — what a reader needs
          to reconstruct the run and re-derive its verdicts:{" "}
          <code>axor-lab verify {id}-package.json --allow-bare</code>. Bare
          means integrity and replay, claimed as no more.
        </p>
      </Card>

      <Card>
        <h3>Publish</h3>
        <div className="form-row">
          <Field label="Question it answers">
            <input
              value={question}
              placeholder="Does taint enforcement contain attachment-borne exfiltration?"
              onChange={(event) => setQuestion(event.target.value)}
            />
          </Field>
          <Field label="Visibility">
            <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
              <option value="unlisted">unlisted</option>
              <option value="private">private</option>
              <option value="public">public</option>
            </select>
          </Field>
          <Field label="Server (optional)">
            <input
              value={server}
              placeholder="http://…  — leave empty to mint locally"
              onChange={(event) => setServer(event.target.value)}
            />
          </Field>
        </div>
        <Button
          disabled={busy !== "" || question.trim() === ""}
          onClick={() => run("publish", async () =>
            setMinted(await api.publishArtifact(id, {
              question,
              visibility,
              ...(server.trim() ? { server: server.trim() } : {}),
            })))}
        >
          {busy === "publish" ? "Publishing…" : "Publish"}
        </Button>
        {/* the difference between the two doors, said before the click rather
            than discovered from a claim list afterwards */}
        <p className="muted small">
          A local publication re-runs the verdicts, so it asserts REPLAY. It does
          not recompute the aggregates and will not claim them — a hand-edited
          bundle could carry a fabricated figure. Give a server and it verifies
          the evidence and recomputes the statistics before minting, and returns
          an acceptance receipt.
        </p>
        {failure && <p className="errors">{failure}</p>}
        {minted && (
          <div className="row-between">
            <span>
              <Tag tone={minted.origin === "server" ? "success" : "info"}>
                {minted.origin}
              </Tag>{" "}
              <code>{minted.publication_id}</code>
              {minted.url && (
                <>
                  {" · "}
                  <a href={minted.url} rel="noreferrer noopener" target="_blank">
                    open
                  </a>
                </>
              )}
            </span>
            {minted.origin === "local" && minted.aggregates_not_claimed
              ? (
                <span className="muted small">
                  {minted.aggregates_not_claimed} aggregate(s) not published as
                  claims
                </span>
              )
              : null}
          </div>
        )}
        {minted?.acceptance && (
          <>
            <p className="muted small">
              Acceptance receipt{" "}
              {minted.acceptance_is_signed ? "(signed)" : "(unsigned)"}
            </p>
            <Json value={minted.acceptance} />
          </>
        )}
      </Card>
    </section>
  );
}

/** What this workspace HANDED OVER. The publish server has had a catalog from
 * the start and the product could not reach it — publishing had no hosted
 * surface at all, so there was nothing to list. */
function Publications() {
  const { data, error, loading } = useAsync(() => api.publications());
  const rows = data?.publications ?? [];
  if (loading || error || rows.length === 0) return null;
  return (
    <section>
      <h2>Published</h2>
      <div className="grid">
        {rows.map((row) => (
          <Card key={row.publication_id}>
            <h4><code>{row.publication_id}</code></h4>
            {row.question && <p className="small">{row.question}</p>}
            <p className="muted small">
              <Tag tone={row.origin === "server" ? "success" : "info"}>
                {row.origin ?? "local"}
              </Tag>{" "}
              {row.visibility ?? "unlisted"} · {row.claims} claim(s)
              {row.created ? ` · ${row.created}` : ""}
            </p>
          </Card>
        ))}
      </div>
    </section>
  );
}

export function ArtifactList() {
  const { data, error, loading, reload } = useAsync(() => api.artifacts());
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  const rows = data?.artifacts ?? [];
  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Artifacts</h1>
        {/* The user-facing noun is Artifact. "Bundle" is banned from new
            surfaces (terminology lint) — it is the body an artifact wraps. */}
        <p className="muted">The portable output of a run: trials, metrics, evidence, invariants, and how to reproduce it.</p>
      </header>
      {rows.length === 0 ? (
        <Empty>No artifacts yet.</Empty>
      ) : (
        <div className="grid">
          {rows.map((row) => (
            <Card key={row.artifact_id}>
              <h4>
                <Link to={`/artifacts/${row.artifact_id}`}>{row.artifact_id}</Link>
              </h4>
              {row.created && <p className="muted small">{row.created}</p>}
            </Card>
          ))}
        </div>
      )}
      <Publications />
    </div>
  );
}

export function ArtifactScreen({ id }: { id: string }) {
  const { data, error, loading, reload } = useAsync(() => api.artifact(id), [id]);
  if (loading) return <Loading />;
  if (error) return <Failed error={error} onRetry={reload} />;
  if (!data) return null;
  const reproduce = data.reproduce as Record<string, unknown> | undefined;
  const suite = data.suite as Record<string, unknown> | undefined;
  const bundle = (data.bundle ?? {}) as Record<string, unknown>;
  const aggregates = (bundle.aggregates ?? []) as Record<string, unknown>[];
  const trials = (bundle.trials ?? []) as Record<string, unknown>[];
  const num = (v: unknown) => (typeof v === "number" ? v.toFixed(3) : "—");
  const byStatus: Record<string, number> = {};
  for (const t of trials) {
    const s = String(t.status ?? "unknown");
    byStatus[s] = (byStatus[s] ?? 0) + 1;
  }

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Artifact {id}</h1>
        {/* Overview from the artifact body, not a JSON dump — what suite ran,
            when, and how many trials of what status it carries. */}
        <p className="muted small">
          {suite ? `${suite.name} (${suite.id})` : "suite unknown"}
          {data.created ? ` · ${data.created}` : ""}
        </p>
      </header>

      <section>
        <h2>Trials</h2>
        <div className="stats">
          {Object.entries(byStatus).map(([status, n]) => (
            <Stat key={status} label={status} value={n} />
          ))}
        </div>
      </section>

      {aggregates.length > 0 && (
        <section>
          <h2>Metrics</h2>
          <div className="table-scroll">
            <table className="agg-table">
              <thead>
                <tr><th>metric</th><th>arm</th><th>estimate</th><th>n</th></tr>
              </thead>
              <tbody>
                {aggregates.map((row, i) => (
                  <tr key={i}>
                    <td><code>{String(row.metric ?? "")}</code></td>
                    <td>{String(row.condition_id ?? "")}</td>
                    <td>{num(row.estimate)}</td>
                    <td>{String(row.n ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <Export id={id} />

      {reproduce && (
        <section>
          <h2>Reproduce</h2>
          <pre className="json">{String(reproduce.command ?? "")}</pre>
          <p className="muted small">claim: {String(reproduce.reproducibility ?? "")}</p>
        </section>
      )}
      {Array.isArray(data.evidence_cases) && (data.evidence_cases as unknown[]).length > 0 && (
        <section>
          <h2>Evidence</h2>
          <Json value={data.evidence_cases} />
        </section>
      )}
      {Array.isArray(data.regressions) && (data.regressions as unknown[]).length > 0 && (
        <section>
          <h2>Invariants</h2>
          <Json value={data.regressions} />
        </section>
      )}
    </div>
  );
}
