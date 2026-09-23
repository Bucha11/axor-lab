import { useEffect, useState } from "react";
import {
  ApiError,
  api,
  type Json,
  type PlaygroundResult,
  type RuntimeRow,
  type SuitePlan,
} from "../lib/api";
import { navigate } from "../lib/router";
import {
  IDENTITY,
  SECTIONS,
  type FieldSpec,
  type ItemFieldSpec,
  type Widget,
  blankFor,
  configFields,
  itemOptions,
  readPath,
  writePath,
} from "../lib/sections";
import { Button, Card, Failed, Link, Loading, Tag } from "../components/ui";
import { TrialResult } from "../components/TrialResult";

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
  blank,
  onChange,
  onJump,
  control,
  itemChoices,
  locked,
}: {
  spec: FieldSpec;
  /** `chips` only: the spec's basicLocked, passed only while in Basic mode */
  locked?: { options: string[]; hint: string };
  itemChoices: Record<string, string[]>;
  value: unknown;
  /** id + aria-describedby for the ONE control a simple widget renders, so its
   * label points at it by id and its help is DESCRIPTION rather than part of
   * the control's name. A composite widget has no single control to carry
   * them and labels its own buttons instead. */
  control: { id: string; "aria-describedby"?: string };
  /** `list` only: what "+ Add" starts a new item as, already resolved against
   * the document (a new scenario has to reference a tool THIS suite declares,
   * so the blank cannot be a constant). */
  blank: Json;
  onChange: (next: unknown) => void;
  onJump: (path: string) => void;
}) {
  const clear = (raw: string) => (raw.trim() === "" ? undefined : raw);

  switch (spec.widget) {
    case "number":
      return (
        <input
          {...control}
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
          {...control}
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
      return <TagsInput {...control} value={value} onChange={onChange} />;
    case "textarea":
      return (
        <textarea
          {...control}
          className="small-area"
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
    case "checkbox":
      return (
        <span className="toggle">
          <input
            {...control}
            type="checkbox"
            checked={value === true}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span className="toggle-track" aria-hidden="true" />
        </span>
      );
    case "chips":
      return (
        <ChipsField
          value={value}
          options={spec.options ?? []}
          onChange={onChange}
          describedBy={control["aria-describedby"]}
          locked={locked}
        />
      );
    case "list":
      return (
        <ListField
          value={value}
          label={spec.label}
          fields={(spec.item ?? []).map((field) => {
            const choices = itemChoices[`${spec.path}.${field.key}`];
            return choices ? { ...field, options: choices } : field;
          })}
          blank={blank}
          onChange={onChange}
          onJump={() => onJump(spec.path)}
        />
      );
    case "yaml-link":
      return (
        <div className="row">
          <span className="muted small">{summaryOf(value)}</span>
          {/* three of these sit side by side under Environment; without the
              field in the name they are one button repeated three times */}
          <button
            type="button"
            className="item-add"
            aria-label={`Edit ${spec.label} in YAML`}
            onClick={() => onJump(spec.path)}
          >
            Edit in YAML →
          </button>
        </div>
      );
    default:
      return (
        <input
          {...control}
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
  }
}

const parseTags = (text: string) =>
  text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

/**
 * A list of strings as one comma-separated text input.
 *
 * The text the person is TYPING is kept as typed; only the parsed array goes
 * into the manifest. Rendering the input from the array instead re-joined it on
 * every keystroke, so the comma you had just typed (an empty trailing item) and
 * the space after it were normalised away before the next key landed — a
 * second tag could not be typed at all. The text is re-derived from the
 * document only when the document changed from ELSEWHERE (a mode switch, a
 * YAML edit, a removed list item), and tidied on blur.
 */
function TagsInput({
  value,
  onChange,
  placeholder,
  ...control
}: {
  value: unknown;
  onChange: (next: unknown) => void;
  placeholder?: string;
  id: string;
  "aria-describedby"?: string;
}) {
  const joined = Array.isArray(value) ? value.map(String).join(", ") : "";
  const [raw, setRaw] = useState(joined);
  // adjusting state during render (not in an effect) so a foreign change never
  // paints one frame of the stale text
  if (parseTags(raw).join(", ") !== joined) setRaw(joined);
  return (
    <input
      {...control}
      value={raw}
      placeholder={placeholder ?? "comma separated"}
      onChange={(e) => {
        setRaw(e.target.value);
        const items = parseTags(e.target.value);
        onChange(items.length === 0 ? undefined : items);
      }}
      onBlur={() => setRaw(joined)}
    />
  );
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
  describedBy,
  locked,
}: {
  value: unknown;
  options: string[];
  onChange: (next: unknown) => void;
  describedBy?: string;
  /** options shown with their state but not toggleable here — see
   * FieldSpec.basicLocked */
  locked?: { options: string[]; hint: string };
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
    <div className="chips" aria-describedby={describedBy}>
      {options.map((option) => {
        const isLocked = locked?.options.includes(option) ?? false;
        return (
          <button
            key={option}
            type="button"
            aria-pressed={selected.includes(option)}
            className={`chip${selected.includes(option) ? " chip-on" : ""}`}
            disabled={isLocked}
            title={isLocked ? locked?.hint : undefined}
            onClick={() => toggle(option)}
          >
            {option}
          </button>
        );
      })}
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
 * edits routinely; everything else the item carries is listed under "also:"
 * with an "Edit in YAML →" link, preserved untouched — the same carry-through
 * rule as the manifest.
 */
function ListField({
  value,
  label,
  fields,
  blank,
  onChange,
  onJump,
}: {
  value: unknown;
  /** the FIELD's label, so this list's buttons say what they act on — a screen
   * with six lists otherwise offers six identical "+ Add" and "Remove" */
  label: string;
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
  const named = new Set(fields.map((field) => field.key.split(".")[0]));
  const slug = `item-${label.replace(/[^A-Za-z0-9]+/g, "-").toLowerCase()}`;

  return (
    <div className="item-list">
      {items.map((item, index) => {
        const rest = Object.fromEntries(
          Object.entries(item).filter(([key]) => !named.has(key)),
        );
        return (
          <div key={index} className="item-card">
            <div className="item-fields">
              {fields.map((field) => {
                // the same explicit pairing the outer fields use, so every
                // control in the Builder is addressed one way. A wrapping
                // <label> is valid around ONE control, but it resolves
                // inconsistently (a wrapped <select> is not found by label at
                // all in some tooling), and half a form addressable one way and
                // half the other is a form nobody can drive.
                const id = `${slug}-${index}-${field.key}`;
                return (
                  <div key={field.key} className="field">
                    <label className="field-label" htmlFor={id}>{field.label}</label>
                    <ItemInput
                      field={field}
                      id={id}
                      value={readPath(item, field.key)}
                      // writePath prunes an emptied key and the object it
                      // leaves behind, so clearing the allowlist does not leave
                      // `policy: {}` on an arm that declares no policy
                      onChange={(next) => writeItem(index, writePath(item, field.key, next))}
                    />
                  </div>
                );
              })}
            </div>
            {Object.keys(rest).length > 0 && (
              <div className="row">
                <span className="muted small">
                  also: {Object.keys(rest).join(", ")}
                </span>
                <button
                  type="button"
                  className="item-add"
                  aria-label={`Edit ${label} ${index + 1} in YAML`}
                  onClick={onJump}
                >
                  Edit in YAML →
                </button>
              </div>
            )}
            <button
              type="button"
              className="item-remove"
              aria-label={`Remove ${label} ${index + 1}`}
              onClick={() => write(items.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="item-add"
        aria-label={`Add ${label}`}
        onClick={() => write([...items, { ...blank }])}
      >
        + Add
      </button>
    </div>
  );
}

function ItemInput({
  field,
  id,
  value,
  onChange,
}: {
  field: ItemFieldSpec;
  id: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const clear = (raw: string) => (raw.trim() === "" ? undefined : raw);
  switch (field.widget) {
    case "number":
      return (
        <input
          id={id}
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
          id={id}
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
          id={id}
          className="small-area"
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
    case "tags":
      return (
        <TagsInput id={id} value={value} placeholder={field.placeholder} onChange={onChange} />
      );
    default:
      return (
        <input
          id={id}
          value={value === undefined ? "" : String(value)}
          placeholder={field.placeholder}
          onChange={(e) => onChange(clear(e.target.value))}
        />
      );
  }
}

/** Widgets that hold MORE THAN ONE control, so a wrapping <label> would bind
 * the field's whole text to whichever comes first. */
const COMPOSITE_WIDGETS = new Set<Widget>(["chips", "list", "yaml-link"]);

function Fields({
  fields,
  manifest,
  advanced,
  options,
  itemChoices,
  onChange,
  onJump,
}: {
  fields: FieldSpec[];
  manifest: Json;
  advanced: boolean;
  /** Choices a field's options cannot be declared with, because they are SERVER
   * state rather than a property of the manifest — the scenario registry is
   * the case that forced this. Keyed by field path. */
  options: Record<string, string[]>;
  /** the same thing one level down, for a field INSIDE a list item, keyed
   * `<field path>.<item key>` */
  itemChoices: Record<string, string[]>;
  onChange: (next: Json) => void;
  onJump: (path: string) => void;
}) {
  const shown = fields
    .filter((field) => advanced || !field.advanced)
    .map((field) =>
      options[field.path] ? { ...field, options: options[field.path] } : field,
    );
  return (
    <div className="builder-fields">
      {shown.map((spec) => {
        // A <label> may own exactly ONE control, and <button> is labelable. So
        // wrapping a chips/list/yaml-link widget in one handed the field's
        // whole text to its first button as an accessible name AND made every
        // word of that text a click target for it: clicking the sentence
        // "omit for a single-arm run…" ADDED a condition, and clicking the word
        // "Capabilities" switched governance on. A composite widget gets a
        // plain container and a label that labels nothing.
        const composite = COMPOSITE_WIDGETS.has(spec.widget);
        const id = `field-${spec.path.replace(/[^A-Za-z0-9]+/g, "-")}`;
        const helpId = spec.help ? `${id}-help` : undefined;
        // the label points AT the control by id and the help is a description,
        // so a field reads as "Topology, combobox" with the caveat announced
        // after — not as one control named by a paragraph
        const text = composite
          ? <span className="field-label">{spec.label}</span>
          : <label className="field-label" htmlFor={id}>{spec.label}</label>;
        return (
          <div
            key={spec.path}
            className={spec.widget === "checkbox" ? "field field-inline" : "field"}
          >
            {/* a toggle reads as "[switch] label", not a label floating over a
                lone control — so the checkbox renders before its text */}
            {spec.widget !== "checkbox" && text}
            <Widget
              spec={spec}
              value={readPath(manifest, spec.path)}
              blank={blankFor(spec, manifest)}
              control={{ id, ...(helpId ? { "aria-describedby": helpId } : {}) }}
              itemChoices={itemChoices}
              locked={advanced ? undefined : spec.basicLocked}
              onChange={(next) => {
                const written = writePath(manifest, spec.path, next);
                // a coupled field finishes its own edit — see FieldSpec.couples
                onChange(spec.couples ? spec.couples(written, next) : written);
              }}
              onJump={onJump}
            />
            {spec.widget === "checkbox" && (
              <label className="field-label" htmlFor={id}>{spec.label}</label>
            )}
            {spec.help && (
              <span id={helpId} className="muted small">{spec.help}</span>
            )}
            {!advanced && spec.basicLocked && (
              <span className="muted small">{spec.basicLocked.hint}</span>
            )}
          </div>
        );
      })}
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
  // Whole-suite validation answers "is this manifest valid"; a pile of errors
  // does not say WHICH scenario broke. Per-scenario validation does, and the
  // plan preview says what a run would actually execute before one starts.
  const [scenarioErrors, setScenarioErrors] = useState<[string, string[]][] | null>(null);
  const [plan, setPlan] = useState<SuitePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [runtimes, setRuntimes] = useState<RuntimeRow[] | null>(null);
  // the names a `scenario_refs` entry can actually resolve to. Loaded, not
  // declared: the registry is server state, and the field used to be free text
  // against a registry nothing filled — so every value typed there made the
  // suite permanently invalid.
  const [registryScenarios, setRegistryScenarios] = useState<string[]>([]);
  // RFC §13: "Preview should support a single-trial debugger before full
  // execution." The Playground screen is that debugger, but it ran a SAVED
  // suite by id, so the one document it could not try was the one on screen.
  const [trial, setTrial] = useState<PlaygroundResult | null>(null);
  const [trialScenario, setTrialScenario] = useState("");
  const [runtimeRef, setRuntimeRef] = useState("");
  const [runError, setRunError] = useState<string | null>(null);
  // Each action reports its failure BESIDE its own button. Check scenarios,
  // Preview plan and Try one trial all used to write into `runError`, which
  // renders under Run at the bottom of the page — the click did nothing
  // visible and the reason sat a screen away, under an unrelated heading.
  const [checkError, setCheckError] = useState<string | null>(null);
  const [trialError, setTrialError] = useState<string | null>(null);
  // a failed runtimes load is NOT "no agent connected": a 401/403/500 said
  // "go connect one" to someone whose agent was connected all along
  const [runtimesError, setRuntimesError] = useState<string | null>(null);
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
      .catch((exc: unknown) => {
        if (!live) return;
        setRuntimes([]);
        setRuntimesError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    api
      .registryScenarios()
      .then(({ scenarios }) => live && setRegistryScenarios(scenarios.map((s) => s.name)))
      // an empty registry is the normal state of a fresh workspace, and the
      // chips row still accepts a typed name — a failed load must not be a
      // red screen over a field most suites never use
      .catch(() => live && setRegistryScenarios([]));
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

  /** Every result on screen describes the document that produced it, so a new
   * document invalidates all of them at once. Clearing only the validation
   * verdict left the plan preview and the per-scenario report standing after an
   * edit — change `repeats` and the old trial count kept its place, reading as
   * the plan for what you were now looking at. */
  function clearResults() {
    setOk(null);
    setErrors(null);
    setSaved(false);
    setScenarioErrors(null);
    setPlan(null);
    setRunError(null);
    setCheckError(null);
    setTrialError(null);
    setTrial(null);
  }

  function edited(next: Json) {
    setManifest(next);
    clearResults();
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
    } catch (exc) {
      // without a catch a failed request (network, 401, 500) was swallowed by
      // the finally: the button re-enabled and nothing said it had failed
      setOk(false);
      setErrors([exc instanceof Error ? exc.message : String(exc)]);
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
      // an id change is a NEW suite. Create it, then navigate to its route —
      // otherwise the URL kept saying the old id while the form edited the new
      // one, and reloading showed the pristine original, so the edits looked
      // lost. Create (POST) refuses an id that is already saved with a 409:
      // it used to upsert, so renaming this suite onto another's id silently
      // replaced that other suite.
      if (id !== suiteId) {
        try {
          await api.createSuite(document);
        } catch (exc) {
          if (exc instanceof ApiError && exc.status === 409) {
            setOk(false);
            setErrors([
              `A suite with id "${id}" already exists — pick another id.`,
            ]);
            return;
          }
          throw exc;
        }
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

  /** Per-scenario validation needs the suite's tool manifests, keyed by id —
   * the same map `resolve_suite` builds from `environment.tools`. Sending the
   * scenario alone made the checker report "tool X has no manifest in the
   * bundle" for every tool of every scenario, plus every downstream semantic
   * that reads a manifest (an injection with no untrusted field to land in, a
   * breach predicate with no WRITE/EXPORT/EXEC sink). The button was pure
   * false positives on suites that validate perfectly. */
  function toolManifests(document: Json): Record<string, Json> {
    const environment = (document.environment ?? {}) as Record<string, unknown>;
    const tools = Array.isArray(environment.tools) ? environment.tools : [];
    return Object.fromEntries(
      tools
        .filter((tool): tool is Json => !!tool && typeof tool === "object")
        .filter((tool) => typeof tool.id === "string")
        .map((tool) => [String(tool.id), tool]),
    );
  }

  /** Run ONE trial of the document as it stands — unsaved, unstored, not
   * counted. `/playground/trial` already took an inline `{suite}`; the Builder
   * simply never sent the manifest it was holding, so trying a change meant
   * saving it first and finding the Playground under a different route.
   *
   * Deliberately not a Save: a trial is a debugging click, and the payload's
   * own `counted_in_a_run: false` is rendered beside the result. */
  async function tryOneTrial() {
    setBusy(true);
    setTrialError(null);
    setTrial(null);
    try {
      const document = await current();
      if (document === null) return;
      setTrial(await api.playground({
        suite: document,
        ...(trialScenario ? { scenario: trialScenario } : {}),
      }));
    } catch (exc) {
      setTrialError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function checkScenarios() {
    setBusy(true);
    setCheckError(null);
    try {
      const document = await current();
      if (document === null) return;
      const scenarios = (document.scenarios ?? []) as Record<string, unknown>[];
      const manifests = toolManifests(document);
      const found: [string, string[]][] = [];
      for (const scenario of scenarios) {
        const result = await api.validateScenario(scenario, manifests);
        if (!result.ok) found.push([String(scenario.name ?? "(unnamed)"), result.errors]);
      }
      setScenarioErrors(found);
    } catch (exc) {
      setCheckError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  /** What a run WOULD execute — planned by the server, through the same
   * `plan_suite` a dispatch runs.
   *
   * This used to hand-build an `experiment/v1` out of the manifest and plan
   * THAT. Two things went wrong and both were invisible: a suite declaring no
   * conditions got the literal arm id "condition" where a real dispatch
   * synthesizes the UNGOVERNED arm, and `scenario_refs` were dropped, so their
   * trials never appeared. The count looked right, every trial id was wrong,
   * and the caption promised the ids were deterministic. */
  async function previewPlan() {
    setBusy(true);
    setCheckError(null);
    try {
      const document = await current();
      if (document === null) return;
      setPlan(await api.planSuite(document));
    } catch (exc) {
      setPlan(null);
      setCheckError(exc instanceof Error ? exc.message : String(exc));
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

  const dynamicOptions: Record<string, string[]> = {
    scenario_refs: registryScenarios,
  };
  // choices an item field can only get from the document being edited: an
  // aggregation names one of THIS suite's metrics
  const itemChoices = itemOptions(manifest);
  const suiteConfig = configFields(manifest);

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
              clearResults();
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
              options={dynamicOptions}
                  itemChoices={itemChoices}
              onChange={edited}
              onJump={jumpToYaml}
            />
          </Card>
          {SECTIONS.map((section) => ({
            section,
            // the suite's OWN knobs, laid out into this section by its
            // `ui_schema` (RFC §12). They are ordinary fields from here on —
            // same widgets, same writePath, same carry-through.
            fields: [...section.fields, ...(suiteConfig.bySection[section.id] ?? [])],
          }))
            .filter(
              // a section whose every field is Advanced does not render an
              // empty card in Basic — a card with nothing to do is not a form
              ({ fields }) =>
                mode === "advanced" || fields.some((field) => !field.advanced),
            )
            .map(({ section, fields }) => (
              <Card key={section.id}>
                <h3>{section.title}</h3>
                {section.description && (
                  <p className="muted small">{section.description}</p>
                )}
                <Fields
                  fields={fields}
                  manifest={manifest}
                  advanced={mode === "advanced"}
                  options={dynamicOptions}
                  itemChoices={itemChoices}
                  onChange={edited}
                  onJump={jumpToYaml}
                />
              </Card>
            ))}
          {suiteConfig.unplaced.length > 0 && (
            <Card>
              <h3>Suite options</h3>
              <p className="muted small">
                Declared by this suite's <code>config_schema</code>, placed in no
                section by its <code>ui_schema</code>. Shown here rather than
                dropped — the Builder never silently loses a declared field.
              </p>
              <Fields
                fields={suiteConfig.unplaced}
                manifest={manifest}
                advanced={mode === "advanced"}
                options={dynamicOptions}
                  itemChoices={itemChoices}
                onChange={edited}
                onJump={jumpToYaml}
              />
            </Card>
          )}
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
          <Button variant="secondary" onClick={checkScenarios} disabled={busy}>
            Check scenarios
          </Button>
          <Button variant="secondary" onClick={previewPlan} disabled={busy}>
            Preview plan
          </Button>
          {ok === true && <Tag tone="success">valid</Tag>}
          {ok === false && <Tag tone="danger">{errors?.length ?? 0} error(s)</Tag>}
          {saved && <Tag tone="success">saved</Tag>}
        </div>
        {checkError && <p className="errors" role="alert">{checkError}</p>}
        {scenarioErrors !== null && (
          scenarioErrors.length === 0 ? (
            <p className="muted small">Every scenario validates on its own.</p>
          ) : (
            <ul className="errors">
              {scenarioErrors.map(([name, messages]) => (
                <li key={name}>
                  <code>{name}</code>: {messages.join("; ")}
                </li>
              ))}
            </ul>
          )
        )}
        {plan && (
          <>
            <p className="muted small">
              {plan.trials.length} trial unit(s) — one per (scenario × condition ×
              repeat), across {plan.conditions.length} arm(s):{" "}
              <code>{plan.conditions.join(", ")}</code>. This is a PLAN, not a
              run: the ids come from the same planner a dispatch runs, so what
              you see here is what the run will name.
            </p>
            {plan.conditions.length === 1 && plan.conditions[0] === "ungoverned" &&
              !readPath(manifest, "execution.conditions") && (
                <p className="muted small">
                  This suite declares no conditions, so it gets the single
                  UNGOVERNED arm — wrapped and observed, gates not enforcing. Not
                  a kernel-free run: add a second condition to compare governed
                  against it.
                </p>
              )}
            {!plan.drives_itself && (
              <p className="muted small">
                These trials plan fine and a LOCAL run of them would measure
                nothing: no registered implementation drives this suite's agent.
                Dispatch to a connected agent under Run — that is the path this
                manifest is written for.
              </p>
            )}
            {plan.blockers.length > 0 && (
              <ul className="errors">
                {plan.blockers.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            )}
          </>
        )}
        {errors && errors.length > 0 && (
          <ul className="errors">
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <h3>Try one trial</h3>
        <p className="muted small">
          Runs the document as it stands — unsaved, against the suite's own
          simulated tools. Nothing is stored, nothing is aggregated, and it does
          not need a connected agent. This is the debugger, not the run.
        </p>
        {plan && !plan.drives_itself && (
          <p className="muted small">
            <Tag tone="warning">no implementation</Tag> Nothing registered
            decides what this suite's agent does, so a local trial will call no
            tools and every metric will come back false. The manifest says which
            tools exist, not the order an agent calls them in — that comes from
            a connected agent under Run, or from a suite implementation.
          </p>
        )}
        <div className="row">
          <input
            value={trialScenario}
            aria-label="Scenario to try"
            placeholder="scenario (first declared)"
            onChange={(event) => setTrialScenario(event.target.value)}
          />
          <Button variant="secondary" onClick={tryOneTrial} disabled={busy}>
            Try one trial
          </Button>
        </div>
        {trialError && <p className="errors" role="alert">{trialError}</p>}
      </Card>

      {trial && <TrialResult result={trial} />}

      <Card>
        <h3>Run</h3>
        {runtimes === null ? (
          <Loading />
        ) : runtimesError !== null ? (
          <p className="errors" role="alert">
            Could not load the connected agents: {runtimesError}
          </p>
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
                  {runtime.runtime_label || runtime.agent_ref || "agent"} (
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
