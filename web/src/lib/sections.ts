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

export type Widget = "text" | "textarea" | "number" | "select" | "json" | "tags";

export interface FieldSpec {
  /** dotted path INTO the manifest, e.g. `execution.repeats` */
  path: string;
  label: string;
  widget: Widget;
  options?: string[];
  advanced?: boolean;
  help?: string;
}

export interface SectionSpec {
  id: "agents" | "scenarios" | "environment" | "execution" | "evaluation" | "artifact";
  title: string;
  fields: FieldSpec[];
}

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
    widget: "tags",
    help: "declaring 'governance' requires execution.conditions, and vice versa",
  },
];

export const SECTIONS: SectionSpec[] = [
  {
    id: "agents",
    title: "Agents",
    fields: [
      { path: "agents", label: "Agents", widget: "json" },
      { path: "topology", label: "Topology", widget: "json", advanced: true },
    ],
  },
  {
    id: "scenarios",
    title: "Scenarios",
    fields: [
      { path: "scenarios", label: "Scenarios", widget: "json" },
      { path: "scenario_refs", label: "Scenario refs", widget: "tags", advanced: true },
    ],
  },
  {
    id: "environment",
    title: "Environment & Tools",
    fields: [
      { path: "environment.tools", label: "Tool manifests", widget: "json" },
      { path: "environment.simulation", label: "Simulation", widget: "json" },
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
      { path: "evaluation.metrics", label: "Metrics", widget: "json" },
      { path: "evaluation.aggregations", label: "Aggregations", widget: "json" },
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
      { path: "artifact.sections", label: "Report sections", widget: "tags" },
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
