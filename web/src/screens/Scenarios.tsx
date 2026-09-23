import { useState } from "react";
import { api, type Json } from "../lib/api";
import { describeError, useAsync } from "../lib/useAsync";
import { navigate } from "../lib/router";
import {
  Button, Card, Empty, Failed, Field, InlineError, Json as JsonView, Link, Loading, Tag,
} from "../components/ui";

/**
 * The scenario registry — what a suite's `scenario_refs` resolve against.
 *
 * The server has had the whole verb set (list, read, save, delete, publish to
 * the org) and the only client was the Builder's ref picker, which can READ the
 * list and nothing else. So a scenario could be referenced but never written,
 * and a registry nothing can fill is a dropdown that is always empty.
 */

/** A starting document: one scenario plus the tool manifests it runs with.
 *
 * The manifests are part of what is saved-against, not decoration — the server
 * validates the scenario's semantics against them (a declared tool with no
 * manifest, a breach predicate with no sink), so a template without them would
 * fail on the first Save. Mirrors the built-in `blank` suite's first scenario. */
const TEMPLATE = {
  scenario: {
    schema_version: "scenario/v1",
    name: "my-scenario",
    task: "Record a note.",
    inputs: { note: "hello" },
    tools: [{ $ref: "note" }],
    fixtures: { note: { result: { ok: true } } },
    task_success: { event: "tool_call", tool: "note" },
  },
  manifests: {
    note: {
      schema_version: "tool-manifest/v1",
      id: "note",
      args_schema: {
        type: "object",
        properties: { text: { type: "string" } },
        required: ["text"],
      },
      result_schema: {
        type: "object",
        properties: { ok: { type: "boolean" } },
        required: ["ok"],
      },
      effect: { default_class: "WRITE", driving_args: [] },
      untrusted_fields: [],
      side_effecting: false,
      reset: { strategy: "fixture", fixture_ref: "note" },
    },
  },
};

/** Parse the editor's `{scenario, manifests}`. A parse failure is the user's to
 * fix, so it throws a sentence rather than a SyntaxError position. */
function parseDraft(text: string): { scenario: Json; manifests: Record<string, Json> } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("not valid JSON");
  }
  const draft = parsed as { scenario?: unknown; manifests?: unknown };
  if (!draft || typeof draft.scenario !== "object" || draft.scenario === null) {
    throw new Error("the document needs a {scenario} object");
  }
  const manifests = draft.manifests ?? {};
  if (typeof manifests !== "object" || manifests === null || Array.isArray(manifests)) {
    throw new Error("{manifests} must be an object of tool id -> manifest");
  }
  return { scenario: draft.scenario as Json, manifests: manifests as Record<string, Json> };
}

export function ScenarioList() {
  const { data, error, status, loading, reload } = useAsync(() => api.registryScenarios());
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState<{ message: string; status: number | null } | null>(
    null,
  );
  const [checked, setChecked] = useState<{ ok: boolean; errors: string[] } | null>(null);

  async function guard(label: string, work: () => Promise<void>) {
    setBusy(label);
    setFailure(null);
    try {
      await work();
    } catch (caught) {
      setFailure(describeError(caught));
    } finally {
      setBusy("");
    }
  }

  const validate = () =>
    guard("validate", async () => {
      const { scenario, manifests } = parseDraft(draft);
      setChecked(await api.validateScenario(scenario, manifests));
    });

  const save = () =>
    guard("save", async () => {
      const { scenario, manifests } = parseDraft(draft);
      setChecked(null);
      const saved = await api.saveScenario(scenario, manifests);
      setDraft("");
      reload();
      navigate(`/scenarios/${encodeURIComponent(saved.name)}`);
    });

  if (loading) return <Loading />;
  if (error) return <Failed error={error} status={status} onRetry={reload} />;
  const rows = data?.scenarios ?? [];

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Scenarios</h1>
        <p className="muted">
          The registry a suite&apos;s <code>scenario_refs</code> resolve against:
          this workspace&apos;s saved scenarios, with the org&apos;s shared ones
          underneath. A ref is frozen into a run at plan time, so editing one here
          never changes what a finished run executed.
        </p>
      </header>

      {rows.length === 0 ? (
        <Empty>No scenarios saved — a suite&apos;s refs have nothing to resolve to yet.</Empty>
      ) : (
        <ul className="rows">
          {rows.map((row) => (
            <li key={row.name}>
              <Link to={`/scenarios/${encodeURIComponent(row.name)}`}>{row.name}</Link>
              <span className="muted small">{row.task}</span>
            </li>
          ))}
        </ul>
      )}

      <Card>
        <h4>Save a scenario</h4>
        <p className="muted small">
          A scenario and the tool manifests it runs with. The server checks its
          semantics against those manifests before saving — a scenario the
          registry accepted and a run then refused would break every suite that
          refs it.
        </p>
        <Field label="Scenario JSON" hint="{scenario, manifests} — manifests keyed by tool id.">
          <textarea
            id="scenario-json"
            rows={10}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setChecked(null);
            }}
            placeholder='{"scenario": {…}, "manifests": {"tool_id": {…}}}'
          />
        </Field>
        <div className="row">
          <Button
            variant="ghost"
            disabled={busy !== ""}
            onClick={() => {
              setDraft(JSON.stringify(TEMPLATE, null, 2));
              setChecked(null);
            }}
          >
            Start from a template
          </Button>
          <Button variant="secondary" onClick={validate} disabled={busy !== "" || !draft}>
            {busy === "validate" ? "Checking…" : "Validate"}
          </Button>
          <Button onClick={save} disabled={busy !== "" || !draft}>
            {busy === "save" ? "Saving…" : "Save scenario"}
          </Button>
        </div>
        {checked &&
          (checked.ok ? (
            <p className="muted small">
              <Tag tone="success">valid</Tag> against the manifests given.
            </p>
          ) : (
            <ul className="errors">
              {checked.errors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          ))}
        {failure && <InlineError error={failure.message} status={failure.status} />}
      </Card>
    </div>
  );
}

export function ScenarioScreen({ name }: { name: string }) {
  const { data, error, status, loading, reload } = useAsync(
    () => api.registryScenario(name),
    [name],
  );
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState<{ message: string; status: number | null } | null>(
    null,
  );
  const [shared, setShared] = useState<string | null>(null);

  async function guard(label: string, work: () => Promise<void>) {
    setBusy(label);
    setFailure(null);
    try {
      await work();
    } catch (caught) {
      setFailure(describeError(caught));
    } finally {
      setBusy("");
    }
  }

  const remove = () =>
    guard("delete", async () => {
      await api.deleteScenario(name);
      navigate("/scenarios");
    });

  const publish = () =>
    guard("publish", async () => {
      setShared((await api.publishScenario(name)).org);
    });

  if (loading) return <Loading />;
  if (error) return <Failed error={error} status={status} onRetry={reload} />;
  if (!data) return null;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Scenario {name}</h1>
        {typeof data.task === "string" && <p className="muted">{data.task}</p>}
        <p className="small">
          <Link to="/scenarios">← Scenarios</Link>
        </p>
      </header>

      <Card>
        <div className="row">
          <Button variant="secondary" onClick={publish} disabled={busy !== ""}>
            {busy === "publish" ? "Sharing…" : "Share with the org"}
          </Button>
          <Button variant="ghost" onClick={remove} disabled={busy !== ""}>
            {busy === "delete" ? "Deleting…" : "Delete this workspace's copy"}
          </Button>
        </div>
        <p className="muted small">
          Sharing puts it in the org registry, where every workspace in the org
          can ref it (the plan&apos;s <code>private_registry</code> capability).
          Deleting removes only this workspace&apos;s copy — an org-shared scenario
          of the same name stays, and becomes the one refs resolve to.
        </p>
        {shared && (
          <p className="muted small">
            <Tag tone="success">shared</Tag> with org <code>{shared}</code>.
          </p>
        )}
        {failure && <InlineError error={failure.message} status={failure.status} />}
      </Card>

      <section>
        <h2>Document</h2>
        <JsonView value={data} />
      </section>
    </div>
  );
}
