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

/**
 * `yaml-link` is the deliberate absence of a widget: a deep structure (a tool
 * manifest's args_schema, a governance policy) is not formable, and rendering
 * a JSON textarea for it would be a second text editor duplicating the YAML
 * mode. The field shows what is there and one click lands the cursor ON that
 * key in YAML — one text editor, reachable from the form.
 */
export type Widget =
  | "text"
  | "textarea"
  | "number"
  | "select"
  | "checkbox"
  | "tags"
  | "chips"
  | "list"
  | "yaml-link";

/** A field of one item inside a `list` widget. Flat on purpose: an item field
 * that itself needs a list is the signal the item belongs in Advanced JSON. */
export interface ItemFieldSpec {
  /** a key of the item, or a DOTTED PATH into it (`policy.profile`).
   *
   * Nested because the thing a governance suite is actually varying lives one
   * level down: an arm's `policy` was reachable only through the YAML link, so
   * the profile, the trust model and the allowlist — the whole subject of the
   * experiment — were invisible in the form that claims to edit the arm. */
  key: string;
  label: string;
  widget: "text" | "textarea" | "number" | "select" | "tags";
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
  /** `list` only: what a newly added item starts as.
   *
   * A FUNCTION when the blank cannot be a constant — a new scenario has to
   * reference a tool that exists in THIS suite, and no literal can know that.
   * Resolved by `blankFor` against the manifest being edited. */
  blank?: Json | ((manifest: Json) => Json);
  /** A second edit this field's write REQUIRES.
   *
   * Not sugar: the schema binds `execution.conditions` and the `governance`
   * capability BOTH ways, so a form that writes one and leaves the other is a
   * form that knowingly produces an invalid document. Applied after the write,
   * over the already-updated document. */
  couples?: (document: Json, value: unknown) => Json;
  /** `chips` only: options that are shown but NOT toggleable in Basic mode,
   * with the sentence that says why. For an option that is only valid together
   * with an Advanced-only field — toggling it in Basic would produce a
   * document the author has no control on screen to make valid again. */
  basicLocked?: { options: string[]; hint: string };
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

/** The tool ids this suite declares, in order — what a new scenario can `$ref`.
 *
 * `scenario.tools` also accepts a full inline manifest, and the resolver
 * honours it. The blank still prefers a `$ref`: referencing a contract the
 * suite already states is an assumption the document supports, whereas
 * inventing a manifest (with a guessed effect class, which decides whether the
 * kernel treats the tool as a sink) would put a fabricated contract in someone
 * else's suite. A suite with no tools therefore gets an empty list, and the
 * validator says so — see `blankScenario`. */
export function suiteToolIds(manifest: Json): string[] {
  const environment = (manifest.environment ?? {}) as Record<string, unknown>;
  const tools = Array.isArray(environment.tools) ? environment.tools : [];
  return tools
    .filter((tool): tool is Json => !!tool && typeof tool === "object")
    .map((tool) => tool.id)
    .filter((id): id is string => typeof id === "string");
}

/** The metric names this suite declares — what an aggregation may name. */
export function declaredMetrics(manifest: Json): string[] {
  const evaluation = (manifest.evaluation ?? {}) as Record<string, unknown>;
  const metrics = Array.isArray(evaluation.metrics) ? evaluation.metrics : [];
  return metrics
    .filter((metric): metric is Json => !!metric && typeof metric === "object")
    .map((metric) => metric.name)
    .filter((name): name is string => typeof name === "string" && name !== "");
}

/** The declared metrics a THRESHOLD can read.
 *
 * A threshold reads its metric as a number; a boolean one is treated as
 * unmeasured and the rule can never pass, which `validate_manifest` refuses.
 * So a blank that has to name a metric names one a threshold can actually
 * read. */
export function numericMetrics(manifest: Json): string[] {
  const evaluation = (manifest.evaluation ?? {}) as Record<string, unknown>;
  const metrics = Array.isArray(evaluation.metrics) ? evaluation.metrics : [];
  return metrics
    .filter((metric): metric is Json => !!metric && typeof metric === "object")
    .filter((metric) => !["boolean"].includes(String(metric.kind ?? "")))
    .map((metric) => metric.name)
    .filter((name): name is string => typeof name === "string" && name !== "");
}

/** This suite's capabilities with `capability` present, order preserved. */
export function withCapability(manifest: Json, capability: string): string[] {
  const declared = Array.isArray(manifest.capabilities)
    ? manifest.capabilities.map(String)
    : [];
  return declared.includes(capability) ? declared : [...declared, capability];
}

/** The declared capabilities minus `capability`, or undefined when that leaves
 * none — `writePath` then drops the key rather than writing `capabilities: []`. */
export function withoutCapability(manifest: Json, capability: string): string[] | undefined {
  const declared = Array.isArray(manifest.capabilities)
    ? manifest.capabilities.map(String)
    : [];
  const rest = declared.filter((existing) => existing !== capability);
  return rest.length === 0 ? undefined : rest;
}

/** Options an ITEM field inside a `list` can only get from the document being
 * edited, keyed `<field path>.<item key>`. The field-level `options` map does
 * the same job one level up. */
export function itemOptions(manifest: Json): Record<string, string[]> {
  return { "evaluation.aggregations.metric": declaredMetrics(manifest) };
}

/**
 * The smallest scenario that RESOLVES, for this suite.
 *
 * Every one of the five keys is load-bearing and none can be a constant:
 * `tools` must be non-empty and must `$ref` a tool the suite declares, and
 * `task_success` must name an event the runtime evaluator supports — only
 * `tool_call` today, so the success predicate is "the tool got called". The
 * item form shows name and task, so anything it does NOT show has to be right
 * from the start or the user cannot fix it without opening YAML.
 *
 * A suite with no tools gets `tools: []`, which is invalid — and correctly so.
 * A scenario MAY carry its own manifest inline, so the blank could invent one;
 * but the effect class it would have to guess is what decides whether the
 * kernel treats the tool as an egress sink, and guessing that into someone
 * else's suite is worse than the validator saying `minItems 1`.
 *
 * Pinned from both sides: `sections.test.ts` checks the derivation, and
 * `tests/test_suite_platform_contracts.py` checks that a scenario of exactly
 * this shape resolves against the real validator.
 */
export function blankScenario(manifest: Json): Json {
  const [tool] = suiteToolIds(manifest);
  return {
    schema_version: "scenario/v1",
    name: nextId(manifest, "scenarios", "scenario"),
    task: "",
    inputs: {},
    tools: tool ? [{ $ref: tool }] : [],
    fixtures: {},
    ...(tool
      ? { task_success: { event: "tool_call", tool } }
      : { task_success: { event: "tool_call" } }),
  };
}

/** A distinct placeholder identifier for a newly added list item.
 *
 * Every "+ Add" used to start its item with an empty id, and an empty id is
 * refused: an aggregation resolves its metric BY NAME, an invariant reads its
 * metric by name, a condition is addressed by id in every trial. So the form's
 * most ordinary action produced a document the validator rejects — you clicked
 * Add and the screen went red before you typed anything.
 *
 * A visible placeholder is the honest middle: it is obviously a name to change,
 * and it keeps Add → Validate green, which is the property a form should have.
 */
export function nextId(manifest: Json, path: string, prefix: string): string {
  const existing = readPath(manifest, path);
  const count = Array.isArray(existing) ? existing.length : 0;
  const taken = new Set(
    (Array.isArray(existing) ? existing : [])
      .map((item) => (item && typeof item === "object" ? String((item as Json).id ?? (item as Json).name ?? "") : ""))
      .filter(Boolean),
  );
  let candidate = `${prefix}-${count + 1}`;
  for (let bump = count + 2; taken.has(candidate); bump++) candidate = `${prefix}-${bump}`;
  return candidate;
}

/** A field's blank item, resolved against the document being edited. */
export function blankFor(spec: FieldSpec, manifest: Json): Json {
  if (typeof spec.blank === "function") return spec.blank(manifest);
  return spec.blank ?? {};
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
    widget: "chips",
    options: KNOWN_CAPABILITIES,
    help: "declaring 'governance' requires execution.conditions, and vice versa",
    // Conditions are Advanced-only, so in Basic the governance chip could only
    // ever be switched into an invalid state (on with no arm) or out of one
    // the author cannot see (off with arms still declared). It stays VISIBLE —
    // hiding it would hide that the suite is governed — but it is toggled by
    // adding or removing an arm, which lives in Advanced.
    basicLocked: {
      options: ["governance"],
      hint: "'governance' follows the suite's conditions — add or remove an arm in Advanced to change it",
    },
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
      {
        path: "agents",
        label: "Agents (topology roles)",
        widget: "list",
        advanced: true,
        item: [
          { key: "ref", label: "Ref", widget: "text", placeholder: "gpt-4o@2026-05" },
          { key: "role", label: "Role", widget: "text", placeholder: "planner / attacker…" },
          { key: "provider", label: "Provider", widget: "text", placeholder: "openai" },
          { key: "model", label: "Model", widget: "text", placeholder: "gpt-4o" },
        ],
        blank: (manifest: Json) => ({ ref: nextId(manifest, "agents", "agent") }),
      },
      {
        path: "topology.kind",
        label: "Topology",
        widget: "select",
        advanced: true,
        options: [
          "single", "planner_workers", "reviewer_pipeline",
          "negotiation", "swarm", "attacker_defender",
        ],
        help:
          "only 'single' runs today — multi-agent topologies are accepted and " +
          "saved, but a run of one is refused until multi-agent execution ships",
      },
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
          // prose, and the only reason it was in YAML is that nobody added the
          // field — the rest of a scenario is predicates and fixtures, which
          // genuinely are not formable
          { key: "notes", label: "Notes", widget: "textarea" },
        ],
        // Derived from the suite, because a constant cannot be valid here. The
        // old literal seeded `tools: []` and `task_success: {event:
        // "final_output"}`, and BOTH are refused:
        //
        //   suite.scenarios[N].tools: minItems 1, got 0
        //   task_success: event 'final_output' is defined in the schema but not
        //     supported by the runtime evaluator (supported: ['tool_call'])
        //
        // so "+ Add" reliably invalidated the suite, and the item form — name
        // and task only — offered no way to fix either. It now references the
        // suite's first tool and asserts success on a call to it: the smallest
        // scenario that actually resolves.
        blank: blankScenario,
        help:
          "a new scenario starts on the suite's first tool; inputs, fixtures and " +
          "the success predicate live on each scenario — listed under \"also:\" on " +
          "each item and edited with its \"Edit in YAML →\" link, or in the YAML mode",
      },
      {
        // chips, not free text: the options are the registry's own names
        // (supplied at render time — the registry is server state, not part of
        // the manifest). A typed name is still accepted, and still refused by
        // the validator if nothing answers to it, which is the honest place to
        // refuse. As free text the field could only ever break a suite: nothing
        // filled the registry, so every value resolved to nothing.
        path: "scenario_refs",
        label: "Scenario refs",
        widget: "chips",
        advanced: true,
        help:
          "scenarios shared across suites, resolved by name at plan time and " +
          "frozen into the artifact — a later edit cannot change a finished run",
      },
    ],
  },
  {
    id: "environment",
    title: "Environment & Tools",
    fields: [
      {
        path: "environment.simulation.enabled",
        label: "Simulated tools",
        widget: "checkbox",
        help: "replay declared fixtures instead of calling anything real",
      },
      {
        path: "environment.simulation.strict_manifest",
        label: "Strict tool manifests",
        widget: "checkbox",
        advanced: true,
      },
      { path: "environment.tools", label: "Tool manifests", widget: "yaml-link", advanced: true },
      { path: "environment.fixtures", label: "Fixtures", widget: "yaml-link", advanced: true },
      { path: "environment.variables", label: "Variables", widget: "yaml-link", advanced: true },
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
      { path: "execution.budgets.max_usd", label: "Budget (USD)", widget: "number", advanced: true },
      {
        path: "execution.budgets.max_trials",
        label: "Budget (trials)",
        widget: "number",
        advanced: true,
      },
      {
        path: "execution.budgets.max_input_tokens",
        label: "Budget (input tokens)",
        widget: "number",
        advanced: true,
      },
      {
        path: "execution.budgets.max_output_tokens",
        label: "Budget (output tokens)",
        widget: "number",
        advanced: true,
      },
      {
        path: "execution.conditions",
        label: "Conditions (governance)",
        widget: "list",
        advanced: true,
        item: [
          { key: "id", label: "Id", widget: "text", placeholder: "governed" },
          { key: "label", label: "Label", widget: "text", placeholder: "governed + allowlist" },
          { key: "enforcement", label: "Enforcement", widget: "select", options: ["off", "on"] },
          // the reference kernel was REMOVED — a run governs through the real axor-core
          // build or not at all, so the hint shows the SHAPE rather than a version
          // that rots the next time the kernel is released.
          { key: "kernel", label: "Kernel", widget: "text", placeholder: "axor-core@<installed version>" },
          // the policy IS the treatment. `criticality_overrides` is a map and
          // stays in YAML; these three are what an arm varies.
          { key: "policy.profile", label: "Policy profile", widget: "text", placeholder: "strict" },
          {
            key: "policy.trust_model",
            label: "Trust model",
            widget: "text",
            placeholder: "content-ledger",
          },
          {
            key: "policy.allowlist",
            label: "Allowlist",
            widget: "tags",
            placeholder: "$inputs.approved_recipients",
          },
        ],
        blank: (manifest: Json) => ({
          schema_version: "condition/v1",
          // a second arm named "governed" is the ordinary case; an id is
          // required and "" is refused, so start on a usable one
          id: nextId(manifest, "execution.conditions", "arm"),
          enforcement: "on",
        }),
        // the schema binds these two BOTH ways, so a form that writes one and
        // not the other knowingly produces an invalid document. Adding the
        // first arm used to leave you on "execution.conditions is set but
        // 'governance' is not in capabilities" — the most common authoring
        // action in this product, landing in an error you fix in another
        // section. And symmetrically: removing the LAST arm drops the
        // capability, because 'governance' with no conditions is the same
        // schema error in the other direction — and one the author could not
        // fix from Basic, where Conditions is not shown.
        couples: (document, value) =>
          Array.isArray(value) && value.length > 0
            ? writePath(document, "capabilities", withCapability(document, "governance"))
            : writePath(document, "capabilities", withoutCapability(document, "governance")),
        help:
          "an arm is a governed variant of the same run; adding one declares the " +
          "'governance' capability, which the schema requires alongside it; " +
          "removing the last one drops it again",
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
        blank: (manifest: Json) => ({
          name: nextId(manifest, "evaluation.metrics", "metric"),
          kind: "number",
          source: "trial_metric",
        }),
      },
      {
        path: "evaluation.aggregations",
        label: "Aggregations",
        widget: "list",
        item: [
          // a SELECT, not free text: an aggregation resolves its metric BY NAME
          // against the ones this suite declares, so a typo is not a new metric
          // — it is an aggregation over nothing, and the validator says so only
          // after you have typed it. The options come from the manifest being
          // edited (`itemOptions`).
          { key: "metric", label: "Metric", widget: "select", options: [] },
          {
            key: "fn",
            label: "Fn",
            widget: "select",
            options: [
              "rate", "mean", "median", "sum", "count", "min", "max", "p50", "p95", "p99",
            ],
          },
          {
            key: "test",
            label: "Comparison test",
            widget: "select",
            options: ["none", "mcnemar", "two_proportion"],
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
        // starts on a metric the suite HAS — the same rule as a new scenario
        // starting on a tool it declares. "" validated as "an aggregation over
        // metric '' which the suite does not declare" the moment it appeared.
        blank: (manifest: Json) => ({
          metric: declaredMetrics(manifest)[0] ?? "",
          fn: "mean",
          unit_of_analysis: "trial",
        }),
      },
      {
        path: "evaluation.evaluators",
        label: "Evaluators",
        widget: "list",
        advanced: true,
        item: [
          { key: "id", label: "Id", widget: "text", placeholder: "read_count" },
          {
            key: "kind",
            label: "Kind",
            widget: "select",
            options: ["predicate", "trial_metric", "suite_hook"],
          },
          { key: "metric", label: "Metric", widget: "text", placeholder: "trial_metric: key" },
          { key: "hook", label: "Hook", widget: "text", placeholder: "suite_hook: entry point" },
          { key: "produces", label: "Produces", widget: "text", placeholder: "reads" },
        ],
        blank: (manifest: Json) => ({
          id: nextId(manifest, "evaluation.evaluators", "evaluator"),
          kind: "suite_hook",
          produces: "",
        }),
        help: "a 'predicate' evaluator's predicate object is edited in YAML",
      },
      {
        path: "regressions",
        label: "Invariants",
        widget: "list",
        advanced: true,
        item: [
          { key: "id", label: "Id", widget: "text", placeholder: "RG-budget-reads" },
          { key: "name", label: "Name", widget: "text" },
          { key: "expectation", label: "Expectation", widget: "textarea" },
          // SCOPE decides what the invariant is even asked of. "a governed arm
          // never mails an attacker's address" is a claim about two arms of
          // three, and scoping it was reachable only in YAML — so the default
          // (every trial) applied silently, and the ungoverned arm failed an
          // invariant that was never about it.
          {
            key: "scope.condition_ids",
            label: "Arms in scope",
            widget: "tags",
            placeholder: "every arm",
          },
          {
            key: "scope.quantifier",
            label: "Quantifier",
            widget: "select",
            options: ["all", "any"],
          },
        ],
        // a PREDICATE over a tool this suite declares, not a threshold.
        // `declaredMetrics(manifest)[0]` is `task_success` in most suites, and a
        // threshold over a boolean is refused at author time (it reads its
        // metric as a number and treats a boolean as UNMEASURED), so the blank
        // was invalid the moment it appeared. A predicate is also the shape
        // these are actually written in: "the agent did / did not do X".
        blank: (manifest: Json) => {
          const [tool] = suiteToolIds(manifest);
          const numeric = numericMetrics(manifest)[0];
          return {
            schema_version: "regression/v1",
            id: nextId(manifest, "regressions", "RG"),
            name: "",
            rule: tool
              ? { kind: "predicate", expect: true, predicate: { event: "tool_call", tool } }
              : { kind: "metric_threshold", metric: numeric ?? "", op: "lt", value: 0 },
          };
        },
        help: "the executable rule is edited in YAML — use \"Edit in YAML →\" on the item",
      },
    ],
  },
  {
    id: "artifact",
    title: "Artifact",
    fields: [
      {
        path: "artifact.include_traces",
        label: "Include traces",
        widget: "checkbox",
        help: "off yields a metrics-only artifact that cannot support an EvidenceCase",
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

/**
 * The Suite SDK's own knobs, as fields the Builder renders (RFC §12/§13:
 * "Every suite contributes declarative schemas").
 *
 * `config_schema` is a JSON Schema over the suite's options and `config` holds
 * their values; `ui_schema` says only WHERE each one appears. Both keys have
 * been in `suite.schema.json` from the start — its own description of
 * `config_schema` reads "The Builder renders it" — and nothing did, so the
 * Suite protocol had no author-time surface at all: a third-party suite could
 * declare knobs no screen showed.
 *
 * Presentation only, as the schema insists: a `ui_schema` cannot introduce a
 * field, and one naming a property `config_schema` does not define is a
 * validation error rather than a rendered input.
 */
export interface ConfigFields {
  /** fields a `ui_schema` section claims, in the order it lists them */
  bySection: Record<string, FieldSpec[]>;
  /** everything `config_schema` defines that no section claimed. Rendered in
   * its own card rather than dropped — the same carry-through rule the
   * manifest itself gets: the Builder never silently loses a declared field. */
  unplaced: FieldSpec[];
}

/** One `config_schema` property as a field. The widget follows the JSON
 * Schema, because the schema IS the contract — offering a control the
 * validator would refuse is the trap the metric/aggregation enums avoid by
 * being copied from the schema, and here they can be read from it directly. */
export function configField(name: string, schema: Json, advanced: boolean): FieldSpec {
  const type = typeof schema.type === "string" ? schema.type : "";
  const enumeration = Array.isArray(schema.enum) ? schema.enum.map(String) : undefined;
  const items = (schema.items ?? {}) as Json;
  const itemEnum = Array.isArray(items.enum) ? items.enum.map(String) : undefined;

  const spec: FieldSpec = {
    path: `config.${name}`,
    label: typeof schema.title === "string" ? schema.title : name,
    widget: "text",
    advanced,
    ...(typeof schema.description === "string" ? { help: schema.description } : {}),
  };
  if (enumeration) return { ...spec, widget: "select", options: enumeration };
  if (type === "boolean") return { ...spec, widget: "checkbox" };
  if (type === "number" || type === "integer") return { ...spec, widget: "number" };
  if (type === "array") {
    // a closed set of strings is clickable; an open one is a tag list. An array
    // of anything else is not formable and links to the one text editor.
    if (itemEnum) return { ...spec, widget: "chips", options: itemEnum };
    return items.type === "string" || items.type === undefined
      ? { ...spec, widget: "tags" }
      : { ...spec, widget: "yaml-link" };
  }
  if (type === "object") return { ...spec, widget: "yaml-link" };
  return spec;
}

export function configFields(manifest: Json): ConfigFields {
  const configSchema = (manifest.config_schema ?? {}) as Json;
  // `Record<string, Json>` would still index to `Json | undefined` under
  // noUncheckedIndexedAccess, and every read below is guarded by an `in` or a
  // key taken from Object.keys
  const properties = (configSchema.properties ?? {}) as Record<string, Json | undefined>;
  const uiSchema = (manifest.ui_schema ?? {}) as Json;
  const uiSections = Array.isArray(uiSchema.sections) ? uiSchema.sections : [];

  const bySection: Record<string, FieldSpec[]> = {};
  const placed = new Set<string>();
  for (const raw of uiSections) {
    if (!raw || typeof raw !== "object") continue;
    const section = raw as Json;
    const id = String(section.id ?? "");
    const advanced = section.advanced === true;
    for (const key of Array.isArray(section.fields) ? section.fields : []) {
      const name = String(key);
      // a layout cannot introduce a field; the validator refuses one that
      // tries, and rendering it here would show an input for a value nothing
      // describes
      if (!(name in properties)) continue;
      (bySection[id] ??= []).push(configField(name, properties[name] ?? {}, advanced));
      placed.add(name);
    }
  }
  const unplaced = Object.keys(properties)
    .filter((name) => !placed.has(name))
    .map((name) => configField(name, properties[name] ?? {}, false));
  return { bySection, unplaced };
}

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
