import { useEffect, useState } from "react";
import { api, type Json } from "../lib/api";
import {
  IDENTITY,
  SECTIONS,
  type FieldSpec,
  readPath,
  writePath,
} from "../lib/sections";
import { Button, Card, Failed, Loading, Tag } from "../components/ui";

/**
 * The Suite Builder — Basic, Advanced and YAML over ONE manifest (RFC §13).
 *
 * The whole rule is that the three modes edit the same document. That is not
 * achieved by making the forms exhaustive — they cannot be, `scenarios` alone is
 * an arbitrarily deep array — but by never editing anything else: the manifest
 * lives in one piece of state, every field writes back through `writePath`, and
 * a key no form renders is carried along untouched. Switching modes moves the
 * DOCUMENT, not the text.
 *
 * YAML parsing is the server's (`POST /suites/validate-yaml` returns the parsed
 * manifest). A parser in the browser would be a second implementation that can
 * disagree about `on`, `~` and `2026-08-08`, and the one that decides whether a
 * run starts is the server's.
 */

type Mode = "basic" | "advanced" | "yaml";

const MODES: [Mode, string][] = [
  ["basic", "Basic"],
  ["advanced", "Advanced"],
  ["yaml", "YAML"],
];

function Widget({
  spec,
  value,
  onChange,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const clear = (raw: string) => (raw.trim() === "" ? undefined : raw);

  switch (spec.widget) {
    case "number":
      return (
        <input
          type="number"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) =>
            onChange(e.target.value === "" ? undefined : Number(e.target.value))
          }
        />
      );
    case "select":
      return (
        <select
          value={value === undefined ? "" : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return onChange(undefined);
            // a select over "true"/"false" edits a BOOLEAN field; writing the
            // string would make the manifest fail its own schema
            onChange(raw === "true" ? true : raw === "false" ? false : raw);
          }}
        >
          <option value="">—</option>
          {(spec.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    case "tags":
      return (
        <input
          value={Array.isArray(value) ? value.join(", ") : ""}
          placeholder="comma separated"
          onChange={(e) => {
            const items = e.target.value
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean);
            onChange(items.length === 0 ? undefined : items);
          }}
        />
      );
    case "textarea":
      return (
        <textarea
          className="small-area"
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
    case "json":
      return <JsonField value={value} onChange={onChange} />;
    default:
      return (
        <input
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
  }
}

/**
 * A structured field edited as JSON.
 *
 * It holds its own TEXT while the text is unparseable, rather than writing
 * through on every keystroke: parsing mid-edit turns every intermediate state
 * into a "field deleted", and the manifest loses the value the moment the user
 * types an opening brace.
 */
function JsonField({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const serialized = value === undefined ? "" : JSON.stringify(value, null, 2);
  const [text, setText] = useState(serialized);
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    setText(serialized);
    setInvalid(false);
  }, [serialized]);

  return (
    <>
      <textarea
        className={`json-area${invalid ? " invalid" : ""}`}
        value={text}
        spellCheck={false}
        onChange={(event) => {
          const raw = event.target.value;
          setText(raw);
          if (raw.trim() === "") {
            setInvalid(false);
            onChange(undefined);
            return;
          }
          try {
            const parsed = JSON.parse(raw);
            setInvalid(false);
            onChange(parsed);
          } catch {
            setInvalid(true); // keep the text, do not touch the manifest
          }
        }}
      />
      {invalid && <span className="muted small">not valid JSON — not saved yet</span>}
    </>
  );
}

function Fields({
  fields,
  manifest,
  advanced,
  onChange,
}: {
  fields: FieldSpec[];
  manifest: Json;
  advanced: boolean;
  onChange: (next: Json) => void;
}) {
  const shown = fields.filter((field) => advanced || !field.advanced);
  return (
    <div className="builder-fields">
      {shown.map((spec) => (
        <label key={spec.path} className="field">
          <span className="field-label">{spec.label}</span>
          <Widget
            spec={spec}
            value={readPath(manifest, spec.path)}
            onChange={(next) => onChange(writePath(manifest, spec.path, next))}
          />
          {spec.help && <span className="muted small">{spec.help}</span>}
        </label>
      ))}
    </div>
  );
}

export function Builder({ suiteId }: { suiteId: string }) {
  const [manifest, setManifest] = useState<Json | null>(null);
  const [yaml, setYaml] = useState("");
  const [mode, setMode] = useState<Mode>("basic");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[] | null>(null);
  const [ok, setOk] = useState<boolean | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .suite(suiteId)
      .then((document) => live && setManifest(document))
      .catch((exc: unknown) =>
        live ? setLoadError(exc instanceof Error ? exc.message : String(exc)) : undefined,
      );
    return () => {
      live = false;
    };
  }, [suiteId]);

  function edited(next: Json) {
    setManifest(next);
    setOk(null);
    setErrors(null);
    setSaved(false);
  }

  /** Switching INTO yaml serializes the current document; switching OUT parses
   * it back — both on the server, so the two directions cannot disagree. */
  async function switchMode(next: Mode) {
    if (next === mode || manifest === null) return;
    setBusy(true);
    try {
      if (next === "yaml") {
        setYaml(await api.suiteYamlOf(manifest));
      } else if (mode === "yaml") {
        const result = await api.validateSuiteYaml(yaml);
        setOk(result.ok);
        setErrors(result.errors);
        if (!result.ok || !result.suite) return; // stay in YAML; the text is wrong
        setManifest(result.suite);
      }
      setMode(next);
    } catch (exc) {
      setErrors([exc instanceof Error ? exc.message : String(exc)]);
      setOk(false);
    } finally {
      setBusy(false);
    }
  }

  /** The document as the server currently sees it — parsed from YAML when that
   * is the active mode, so Save never writes a stale copy of an edited text. */
  async function current(): Promise<Json | null> {
    if (mode !== "yaml") return manifest;
    const result = await api.validateSuiteYaml(yaml);
    setOk(result.ok);
    setErrors(result.errors);
    return result.ok && result.suite ? result.suite : null;
  }

  async function validate() {
    setBusy(true);
    setSaved(false);
    try {
      const document = await current();
      if (document === null) return;
      const result = await api.validateSuite(document);
      setOk(result.ok);
      setErrors(result.errors);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setSaved(false);
    try {
      const document = await current();
      if (document === null) return;
      const result = await api.validateSuite(document);
      setOk(result.ok);
      setErrors(result.errors);
      if (!result.ok) return;
      await api.saveSuite(String(document.id ?? suiteId), document);
      setManifest(document);
      setSaved(true);
    } catch (exc) {
      setOk(false);
      setErrors([exc instanceof Error ? exc.message : String(exc)]);
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <Failed error={loadError} />;
  if (manifest === null) return <Loading />;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Suite Builder</h1>
        <p className="muted">
          {suiteId} · Basic, Advanced and YAML edit the same manifest. A field no
          form shows is carried through untouched.
        </p>
        <div className="row">
          {MODES.map(([id, label]) => (
            <Button
              key={id}
              variant={mode === id ? "primary" : "secondary"}
              onClick={() => switchMode(id)}
              disabled={busy}
            >
              {label}
            </Button>
          ))}
        </div>
      </header>

      {mode === "yaml" ? (
        <Card>
          <textarea
            className="yaml"
            value={yaml}
            spellCheck={false}
            onChange={(event) => {
              setYaml(event.target.value);
              setOk(null);
              setErrors(null);
              setSaved(false);
            }}
          />
        </Card>
      ) : (
        <>
          <Card>
            <h3>Suite</h3>
            <Fields
              fields={IDENTITY}
              manifest={manifest}
              advanced={mode === "advanced"}
              onChange={edited}
            />
          </Card>
          {SECTIONS.map((section) => (
            <Card key={section.id}>
              <h3>{section.title}</h3>
              <Fields
                fields={section.fields}
                manifest={manifest}
                advanced={mode === "advanced"}
                onChange={edited}
              />
            </Card>
          ))}
        </>
      )}

      <Card>
        <div className="row">
          <Button variant="secondary" onClick={validate} disabled={busy}>
            Validate
          </Button>
          <Button onClick={save} disabled={busy}>
            Save
          </Button>
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
