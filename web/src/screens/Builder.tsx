import { useEffect, useState } from "react";
import { api, type Json, type RuntimeRow } from "../lib/api";
import { navigate } from "../lib/router";
import {
  IDENTITY,
  SECTIONS,
  type FieldSpec,
  type ItemFieldSpec,
  readPath,
  writePath,
} from "../lib/sections";
import { Button, Card, Failed, Link, Loading, Tag } from "../components/ui";

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

/** One line saying what the field holds — shown beside "Edit in YAML" so the
 * link is a description, not a mystery door. */
function summaryOf(value: unknown): string {
  if (value === undefined || value === null) return "not set";
  if (Array.isArray(value)) return `${value.length} item(s)`;
  if (typeof value === "object") return `${Object.keys(value).length} key(s)`;
  return String(value);
}

function Widget({
  spec,
  value,
  onChange,
  onJump,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
  onJump: (path: string) => void;
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
    case "checkbox":
      return (
        <span className="toggle">
          <input
            type="checkbox"
            checked={value === true}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span className="toggle-track" aria-hidden="true" />
        </span>
      );
    case "chips":
      return (
        <ChipsField value={value} options={spec.options ?? []} onChange={onChange} />
      );
    case "list":
      return (
        <ListField
          value={value}
          fields={spec.item ?? []}
          blank={spec.blank ?? {}}
          onChange={onChange}
          onJump={() => onJump(spec.path)}
        />
      );
    case "yaml-link":
      return (
        <div className="row">
          <span className="muted small">{summaryOf(value)}</span>
          <button type="button" className="item-add" onClick={() => onJump(spec.path)}>
            Edit in YAML →
          </button>
        </div>
      );
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
 * A set-of-strings field as toggle chips.
 *
 * The known values are clickable — nobody should have to know that
 * "governance" is a word this platform understands, the screen says so. The
 * schema keeps the list open for third-party capabilities, so a typed extra is
 * accepted too and renders as a removable chip beside the known ones.
 */
function ChipsField({
  value,
  options,
  onChange,
}: {
  value: unknown;
  options: string[];
  onChange: (next: unknown) => void;
}) {
  const [draft, setDraft] = useState("");
  const selected = Array.isArray(value) ? value.map(String) : [];
  const write = (items: string[]) => onChange(items.length === 0 ? undefined : items);
  const toggle = (item: string) =>
    write(
      selected.includes(item)
        ? selected.filter((existing) => existing !== item)
        : [...selected, item],
    );
  const custom = selected.filter((item) => !options.includes(item));

  return (
    <div className="chips">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          className={`chip${selected.includes(option) ? " chip-on" : ""}`}
          onClick={() => toggle(option)}
        >
          {option}
        </button>
      ))}
      {custom.map((item) => (
        <button
          key={item}
          type="button"
          className="chip chip-on"
          title="remove"
          onClick={() => toggle(item)}
        >
          {item} ×
        </button>
      ))}
      <input
        className="chip-input"
        value={draft}
        placeholder="custom…"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          const item = draft.trim();
          if (item && !selected.includes(item)) write([...selected, item]);
          setDraft("");
        }}
      />
    </div>
  );
}

/**
 * A list of objects as a list of small forms — one card per item, each field a
 * real input, add and remove as buttons. The named fields cover what a person
 * edits routinely; everything else the item carries stays under Details as
 * JSON, preserved untouched — the same carry-through rule as the manifest.
 */
function ListField({
  value,
  fields,
  blank,
  onChange,
  onJump,
}: {
  value: unknown;
  fields: ItemFieldSpec[];
  blank: Json;
  onChange: (next: unknown) => void;
  onJump: () => void;
}) {
  const items: Json[] = Array.isArray(value)
    ? value.filter((item): item is Json => !!item && typeof item === "object")
    : [];
  const write = (next: Json[]) => onChange(next.length === 0 ? undefined : next);
  const writeItem = (index: number, item: Json) =>
    write(items.map((existing, i) => (i === index ? item : existing)));
  const named = new Set(fields.map((field) => field.key));

  return (
    <div className="item-list">
      {items.map((item, index) => {
        const rest = Object.fromEntries(
          Object.entries(item).filter(([key]) => !named.has(key)),
        );
        return (
          <div key={index} className="item-card">
            <div className="item-fields">
              {fields.map((field) => (
                <label key={field.key} className="field">
                  <span className="field-label">{field.label}</span>
                  <ItemInput
                    field={field}
                    value={item[field.key]}
                    onChange={(next) => {
                      const updated: Json = { ...item };
                      if (next === undefined) delete updated[field.key];
                      else updated[field.key] = next;
                      writeItem(index, updated);
                    }}
                  />
                </label>
              ))}
            </div>
            {Object.keys(rest).length > 0 && (
              <div className="row">
                <span className="muted small">
                  also: {Object.keys(rest).join(", ")}
                </span>
                <button type="button" className="item-add" onClick={onJump}>
                  Edit in YAML →
                </button>
              </div>
            )}
            <button
              type="button"
              className="item-remove"
              onClick={() => write(items.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>
        );
      })}
      <button type="button" className="item-add" onClick={() => write([...items, { ...blank }])}>
        + Add
      </button>
    </div>
  );
}

function ItemInput({
  field,
  value,
  onChange,
}: {
  field: ItemFieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const clear = (raw: string) => (raw.trim() === "" ? undefined : raw);
  switch (field.widget) {
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
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        >
          <option value="">—</option>
          {(field.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    case "textarea":
      return (
        <textarea
          className="small-area"
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
    default:
      return (
        <input
          value={value === undefined ? "" : String(value)}
          placeholder={field.placeholder}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
  }
}

function Fields({
  fields,
  manifest,
  advanced,
  onChange,
  onJump,
}: {
  fields: FieldSpec[];
  manifest: Json;
  advanced: boolean;
  onChange: (next: Json) => void;
  onJump: (path: string) => void;
}) {
  const shown = fields.filter((field) => advanced || !field.advanced);
  return (
    <div className="builder-fields">
      {shown.map((spec) => (
        <label
          key={spec.path}
          className={spec.widget === "checkbox" ? "field field-inline" : "field"}
        >
          {/* a toggle reads as "[switch] label", not a label floating over a
              lone control — so the checkbox renders before its text */}
          {spec.widget !== "checkbox" && (
            <span className="field-label">{spec.label}</span>
          )}
          <Widget
            spec={spec}
            value={readPath(manifest, spec.path)}
            onChange={(next) => onChange(writePath(manifest, spec.path, next))}
            onJump={onJump}
          />
          {spec.widget === "checkbox" && <span>{spec.label}</span>}
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
  const [runtimes, setRuntimes] = useState<RuntimeRow[] | null>(null);
  const [runtimeRef, setRuntimeRef] = useState("");
  const [runError, setRunError] = useState<string | null>(null);
  const [jumpTarget, setJumpTarget] = useState<string | null>(null);

  // runs AFTER the YAML textarea is committed — the whole reason the cursor
  // placement is an effect and not a callback racing the render
  useEffect(() => {
    if (mode !== "yaml" || jumpTarget === null) return;
    setJumpTarget(null);
    const area = document.querySelector<HTMLTextAreaElement>(".yaml");
    if (!area) return;
    const offset = yamlOffsetOf(area.value, jumpTarget);
    area.focus();
    area.setSelectionRange(offset, offset);
    const line = area.value.slice(0, offset).split("\n").length - 1;
    area.scrollTop = Math.max(0, line * 18 - 60);
  }, [mode, jumpTarget]);

  // the connected agents this suite can be dispatched to. Loaded here, not
  // hardcoded: the dropdown IS the list of runtimes Integrations connected,
  // and an empty list renders as "go connect one", never as a fake choice.
  useEffect(() => {
    let live = true;
    api
      .runtimes()
      .then(({ runtimes: rows }) => {
        if (!live) return;
        setRuntimes(rows);
        const only = rows.length === 1 ? rows[0] : undefined;
        if (only) setRuntimeRef(only.runtime_ref);
      })
      .catch(() => live && setRuntimes([]));
    return () => {
      live = false;
    };
  }, []);

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
        // Only an UNPARSEABLE text pins the user here — there is no document
        // to switch to. A parsed document that fails validation moves to the
        // form with its errors showing; trapping someone in YAML until they
        // fix semantics blind is the opposite of a mode switch.
        if (!result.suite) return;
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
    // a parsed document is returned even when invalid — Save and Run
    // re-validate and refuse; only an unparseable text yields nothing
    return result.suite ?? null;
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
      const id = String(document.id ?? suiteId);
      // an id change is a NEW suite (the server upserts by path id). Create it,
      // then navigate to its route — otherwise the URL kept saying the old id
      // while the form edited the new one, and reloading showed the pristine
      // original, so the edits looked lost.
      if (id !== suiteId) {
        await api.createSuite(document);
        setManifest(document);
        setSaved(true);
        navigate(`/suites/${id}`);
        return;
      }
      await api.saveSuite(id, document);
      setManifest(document);
      setSaved(true);
    } catch (exc) {
      setOk(false);
      setErrors([exc instanceof Error ? exc.message : String(exc)]);
    } finally {
      setBusy(false);
    }
  }

  /** Character offset of `path`'s key in a YAML text — found by walking the
   * keys with 2-space indentation, the shape `to_yaml` emits. A miss returns 0:
   * landing at the top of the document beats not landing in it. */
  function yamlOffsetOf(text: string, path: string): number {
    let from = 0;
    let indent = 0;
    for (const key of path.split(".")) {
      const found = new RegExp(`^ {${indent}}("?)${key}\\1:`, "m").exec(text.slice(from));
      if (!found) return 0;
      from += found.index + found[0].length;
      indent += 2;
    }
    return from;
  }

  /** The form's escape hatch for structures too deep to form: serialize the
   * CURRENT document (same server round trip as the mode switch) and open the
   * YAML editor with the cursor on the requested key. The cursor is placed in
   * an effect, not a rAF — a rAF can fire before React commits the textarea,
   * and then there is nothing to place a cursor into. */
  async function jumpToYaml(path: string) {
    if (manifest === null) return;
    setBusy(true);
    try {
      const text = await api.suiteYamlOf(manifest);
      setYaml(text);
      setMode("yaml");
      setJumpTarget(path);
    } catch (exc) {
      setErrors([exc instanceof Error ? exc.message : String(exc)]);
      setOk(false);
    } finally {
      setBusy(false);
    }
  }

  /** Save, then dispatch to the chosen agent, then land on the live run.
   * Save-first is deliberate: the server dispatches the STORED suite, and a
   * run of anything other than what is on screen is the stale-document bug
   * the Builder already fixed once. */
  async function run() {
    if (!runtimeRef) return;
    setBusy(true);
    setRunError(null);
    try {
      const document = await current();
      if (document === null) return;
      const result = await api.validateSuite(document);
      setOk(result.ok);
      setErrors(result.errors);
      if (!result.ok) return;
      const id = String(document.id ?? suiteId);
      await api.saveSuite(id, document);
      setManifest(document);
      setSaved(true);
      const created = await api.dispatchSuite(id, runtimeRef);
      navigate(`/runs/${created.run_id}`);
    } catch (exc) {
      setRunError(exc instanceof Error ? exc.message : String(exc));
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
              onJump={jumpToYaml}
            />
          </Card>
          {SECTIONS.filter(
            // a section whose every field is Advanced does not render an
            // empty card in Basic — a card with nothing to do is not a form
            (section) =>
              mode === "advanced" || section.fields.some((field) => !field.advanced),
          ).map((section) => (
            <Card key={section.id}>
              <h3>{section.title}</h3>
              {section.description && (
                <p className="muted small">{section.description}</p>
              )}
              <Fields
                fields={section.fields}
                manifest={manifest}
                advanced={mode === "advanced"}
                onChange={edited}
                onJump={jumpToYaml}
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

      <Card>
        <h3>Run</h3>
        {runtimes === null ? (
          <Loading />
        ) : runtimes.length === 0 ? (
          <p className="muted">
            No agent connected yet. Bring your agent on{" "}
            <Link to="/integrations">Integrations</Link> — the connected runtime
            executes the suite on its side and pushes traces back; Lab never
            runs the agent itself.
          </p>
        ) : (
          <div className="row">
            <select
              value={runtimeRef}
              onChange={(event) => setRuntimeRef(event.target.value)}
            >
              <option value="">— choose an agent —</option>
              {runtimes.map((runtime) => (
                <option key={runtime.runtime_ref} value={runtime.runtime_ref}>
                  {runtime.model || runtime.agent_ref || "agent"} (
                  {runtime.runtime_ref})
                </option>
              ))}
            </select>
            <Button onClick={run} disabled={busy || !runtimeRef}>
              Save &amp; run
            </Button>
          </div>
        )}
        {runError && <p className="errors">{runError}</p>}
      </Card>
    </div>
  );
}
