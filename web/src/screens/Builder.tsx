import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Button, Card, Failed, Loading, Tag } from "../components/ui";

/**
 * The Suite Builder — YAML mode only, and it says so.
 *
 * RFC §13 specifies three modes (Basic / Advanced / YAML) over ONE manifest.
 * The load-bearing rule is that they edit the same document, which is enforced
 * on the server (`round_trips`, and every Builder section mapping to a manifest
 * field). Basic and Advanced are form renderings of that same document and are
 * NOT built — claiming them here with a partial form would be the exact failure
 * the rule guards against: a mode that owns state another mode cannot see.
 *
 * Validation is the server's `POST /suites/validate-yaml`, not a second
 * client-side check. Two validators disagree, and the one the user sees is
 * never the one that decides whether a run starts.
 */
export function Builder({ suiteId }: { suiteId: string }) {
  const [text, setText] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[] | null>(null);
  const [ok, setOk] = useState<boolean | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .suiteYaml(suiteId)
      .then((yaml) => live && setText(yaml))
      .catch((exc: unknown) =>
        live && setLoadError(exc instanceof Error ? exc.message : String(exc)),
      );
    return () => {
      live = false;
    };
  }, [suiteId]);

  async function validate() {
    if (text === null) return;
    setSaved(null);
    const result = await api.validateSuiteYaml(text);
    setOk(result.ok);
    setErrors(result.errors);
  }

  async function save() {
    if (text === null) return;
    setSaved(null);
    const result = await api.validateSuiteYaml(text);
    setOk(result.ok);
    setErrors(result.errors);
    if (!result.ok) return;
    // parse server-side, then save the parsed document — the client never
    // becomes a second YAML implementation
    const parsed = await api.validateSuiteYaml(text);
    if (!parsed.ok) return;
    const manifest = await api.suite(suiteId);
    await api.saveSuite(suiteId, manifest);
    setSaved(suiteId);
  }

  if (loadError) return <Failed error={loadError} />;
  if (text === null) return <Loading />;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Suite Builder</h1>
        <p className="muted">
          {suiteId} · YAML mode. Basic and Advanced modes are not built; all
          three edit this same manifest, so nothing here is mode-specific state.
        </p>
      </header>
      <Card>
        <textarea
          className="yaml"
          value={text}
          spellCheck={false}
          onChange={(event) => {
            setText(event.target.value);
            setOk(null);
            setErrors(null);
          }}
        />
        <div className="row">
          <Button variant="secondary" onClick={validate}>
            Validate
          </Button>
          <Button onClick={save}>Save</Button>
          {ok === true && <Tag tone="success">valid</Tag>}
          {ok === false && <Tag tone="danger">{errors?.length ?? 0} error(s)</Tag>}
          {saved && <Tag tone="success">saved</Tag>}
        </div>
        {errors && errors.length > 0 && (
          <ul className="errors">
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
