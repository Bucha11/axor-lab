import type { Json } from "./api";

/**
 * The six Builder sections (RFC §13), each bound to manifest fields.
 *
 * The load-bearing rule is that Basic, Advanced and YAML edit the same
 * document. What makes that true here is not the field list below — it is that
 * every editor mutates a copy of the WHOLE manifest and writes back only the
 * key it touched. A field no form renders is carried through untouched, so
 * saving from Basic cannot drop what Basic does not show. `sections.test.ts`
 * asserts exactly that, because it is the property the whole mode design rests
 * on and the one that fails silently.
 *
 * `advanced: true` hides a field in Basic mode. It is STILL the same manifest
 * field either way — the schema says so — so hiding it must never mean dropping
 * it.
 */

export type Widget =
  | "text"
  | "textarea"
  | "number"
  | "select"
  | "json"
  | "tags"
  | "chips"
  | "list";

/** A field of one item inside a `list` widget. Flat on purpose: an item field
 * that itself needs a list is the signal the item belongs in Advanced JSON. */
export interface ItemFieldSpec {
  key: string;
  label: string;
  widget: "text" | "textarea" | "number" | "select";
  options?: string[];
  placeholder?: string;
}

export interface FieldSpec {
  /** dotted path INTO the manifest, e.g. `execution.repeats` */
  path: string;
  label: string;
  widget: Widget;
  options?: string[];
  /** `list` only: the fields each item shows. Keys the item carries that no
   * field names are preserved untouched — same rule as the manifest itself. */
  item?: ItemFieldSpec[];
  /** `list` only: what a newly added item starts as. */
  blank?: Json;
  advanced?: boolean;
  help?: string;
}

export interface SectionSpec {
  id: "agents" | "scenarios" | "environment" | "execution" | "evaluation" | "artifact";
  title: string;
  /** shown under the title — a section whose Basic mode has nothing to edit
   * still says WHY, instead of rendering an empty card. */
  description?: string;
  fields: FieldSpec[];
}

/** The capabilities the platform knows how to honour (suite.schema.json keeps
 * the list open for third-party ones — the chips row accepts a typed extra). */
export const KNOWN_CAPABILITIES = ["governance", "provenance", "control_plane_export"];

/** Suite identity — not one of the six sections, but the Builder has to show it
 * or a new suite has no id to save under. */
export const IDENTITY: FieldSpec[] = [
  { path: "id", label: "Suite id", widget: "text" },
  { path: "name", label: "Name", widget: "text" },
  { path: "version", label: "Version", widget: "text", advanced: true },
  { path: "description", label: "Description", widget: "textarea" },
  { path: "tags", label: "Tags", widget: "tags", advanced: true },
  {
    path: "capabilities",
    label: "Capabilities",
    widget: "chips",
    options: KNOWN_CAPABILITIES,
    help: "declaring 'governance' requires execution.conditions, and vice versa",
  },
];

export const SECTIONS: SectionSpec[] = [
  {
    id: "agents",
    title: "Agents",
    // The suite does NOT pin its executor. Which agent runs is decided at run
    // time — connect it on Integrations, pick it in Run. Pinning a runtime ref
    // into the manifest would make a saved suite remember a connection that
    // dies with the session, and rendering a JSON blob here presented a
    // fixture ref as if it were a model choice.
    description:
      "The agent is chosen when you run, not here: connect it on Integrations, " +
      "pick it in Run below. This section only describes multi-agent topologies " +
      "(planner/worker, attacker/defender) — leave it empty for a single agent.",
    fields: [
      { path: "agents", label: "Agents (topology roles)", widget: "json", advanced: true },
      { path: "topology", label: "Topology", widget: "json", advanced: true },
    ],
  },
  {
    id: "scenarios",
    title: "Scenarios",
    fields: [
      {
        path: "scenarios",
        label: "Scenarios",
        widget: "list",
        item: [
          { key: "name", label: "Name", widget: "text", placeholder: "unique-scenario-01" },
          { key: "task", label: "Task", widget: "textarea" },
        ],
        blank: { schema_version: "scenario/v1", name: "", task: "" },
        help:
          "tools, inputs, fixtures and success predicates live on each scenario — " +
          "edit them per item under Details, or in Advanced / YAML",
      },
      { path: "scenario_refs", label: "Scenario refs", widget: "tags", advanced: true },
    ],
  },
  {
    id: "environment",
    title: "Environment & Tools",
    fields: [
      {
        path: "environment.simulation.enabled",
        label: "Simulated tools",
        widget: "select",
        options: ["true", "false"],
        help: "true replays declared fixtures instead of calling anything real",
      },
      {
        path: "environment.simulation.strict_manifest",
        label: "Strict tool manifests",
        widget: "select",
        options: ["true", "false"],
        advanced: true,
      },
      { path: "environment.tools", label: "Tool manifests", widget: "json", advanced: true },
      { path: "environment.fixtures", label: "Fixtures", widget: "json", advanced: true },
      { path: "environment.variables", label: "Variables", widget: "json", advanced: true },
    ],
  },
  {
    id: "execution",
    title: "Execution",
    fields: [
      {
        path: "execution.strategy",
        label: "Strategy",
        widget: "select",
        options: ["matrix", "sequential"],
      },
      { path: "execution.repeats", label: "Repeats", widget: "number" },
      {
        path: "execution.seed_policy",
        label: "Seed policy",
        widget: "select",
        options: ["fixed", "per_repeat", "random"],
        help: "'random' forfeits exact reproducibility",
      },
      { path: "execution.seeds", label: "Seeds", widget: "tags", advanced: true },
      { path: "execution.concurrency", label: "Concurrency", widget: "number", advanced: true },
      { path: "execution.timeout_s", label: "Timeout (s)", widget: "number", advanced: true },
      { path: "execution.retries", label: "Retries", widget: "number", advanced: true },
      { path: "execution.budgets", label: "Budgets", widget: "json", advanced: true },
      {
        path: "execution.conditions",
        label: "Conditions (governance)",
        widget: "json",
        advanced: true,
        help: "omit for a single-arm run; present requires 'governance' in capabilities",
      },
    ],
  },
  {
    id: "evaluation",
    title: "Evaluation",
    fields: [
      {
        path: "evaluation.metrics",
        label: "Metrics",
        widget: "list",
        // enums copied from suite.schema.json — the schema is the contract,
        // and a dropdown offering a kind the validator refuses is a trap
        item: [
          { key: "name", label: "Name", widget: "text", placeholder: "task_success" },
          { key: "label", label: "Label", widget: "text", placeholder: "Task Success" },
          {
            key: "kind",
            label: "Kind",
            widget: "select",
            options: ["boolean", "count", "duration_ms", "tokens", "usd", "ratio", "number"],
          },
          {
            key: "source",
            label: "Source",
            widget: "select",
            options: ["trial_metric", "evaluator"],
          },
          { key: "from", label: "From", widget: "text", placeholder: "metrics key / evaluator id" },
          { key: "unit", label: "Unit", widget: "text", placeholder: "ms" },
          {
            key: "direction",
            label: "Direction",
            widget: "select",
            options: ["higher_is_better", "lower_is_better", "neutral"],
          },
        ],
        blank: { name: "", kind: "number", source: "trial_metric" },
      },
      {
        path: "evaluation.aggregations",
        label: "Aggregations",
        widget: "list",
        item: [
          { key: "metric", label: "Metric", widget: "text", placeholder: "a declared metric" },
          {
            key: "fn",
            label: "Fn",
            widget: "select",
            options: [
              "rate", "mean", "median", "sum", "count", "min", "max", "p50", "p95", "p99",
            ],
          },
          {
            key: "unit_of_analysis",
            label: "Unit",
            widget: "select",
            options: ["trial", "run"],
          },
          {
            key: "interval",
            label: "Interval",
            widget: "select",
            options: ["wilson", "bootstrap", "none"],
          },
        ],
        blank: { metric: "", fn: "mean", unit_of_analysis: "trial" },
      },
      { path: "evaluation.evaluators", label: "Evaluators", widget: "json", advanced: true },
      { path: "regressions", label: "Invariants", widget: "json", advanced: true },
    ],
  },
  {
    id: "artifact",
    title: "Artifact",
    fields: [
      {
        path: "artifact.include_traces",
        label: "Include traces",
        widget: "select",
        options: ["true", "false"],
        help: "false yields a metrics-only artifact that cannot support an EvidenceCase",
      },
      {
        path: "artifact.sections",
        label: "Report sections",
        widget: "chips",
        // closed enum in suite.schema.json — no custom entry would validate,
        // but the chips widget accepts one anyway; the validator refuses it
        // with the schema's own message, which is the honest place to refuse
        options: ["overview", "metrics", "scenarios", "failures", "evidence", "artifacts"],
      },
      { path: "artifact.renderer", label: "Renderer", widget: "text", advanced: true },
      { path: "artifact.redact", label: "Redacted paths", widget: "tags", advanced: true },
    ],
  },
];

/** Every path any form renders — what `sections.test.ts` checks the built-in
 * manifests against, so a field a suite actually uses does not silently become
 * uneditable in the Builder. */
export function renderedPaths(): string[] {
  return [...IDENTITY, ...SECTIONS.flatMap((s) => s.fields)].map((f) => f.path);
}

export function readPath(document: Json, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (node, key) => (node && typeof node === "object" ? (node as Json)[key] : undefined),
      document,
    );
}

/**
 * A copy of `document` with `path` set — or REMOVED when the value is
 * undefined.
 *
 * Removal matters: an optional field cleared in the form must leave the
 * manifest, not sit there as `null`. `null` is a value the schema rejects for
 * most of these, so writing one turns "I cleared this" into "this suite is
 * invalid".
 */
export function writePath(document: Json, path: string, value: unknown): Json {
  const [head, ...rest] = path.split(".");
  if (!head) return document;
  const next: Json = { ...document };
  if (rest.length === 0) {
    if (value === undefined) delete next[head];
    else next[head] = value;
    return next;
  }
  const child = next[head];
  const branch = child && typeof child === "object" ? (child as Json) : {};
  const updated = writePath(branch, rest.join("."), value);
  if (Object.keys(updated).length === 0) delete next[head];
  else next[head] = updated;
  return next;
}
