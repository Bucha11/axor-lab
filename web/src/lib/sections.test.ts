import { describe, expect, it } from "vitest";
import type { Json } from "./api";
import {
  IDENTITY,
  SECTIONS,
  blankFor,
  blankScenario,
  configFields,
  readPath,
  renderedPaths,
  suiteToolIds,
  writePath,
} from "./sections";

/**
 * The Builder's three modes edit ONE manifest (RFC §13), and the plan's risk
 * table names the failure: Basic mode owning state the YAML mode cannot see.
 *
 * The forms cannot be exhaustive — `scenarios` alone is an arbitrarily deep
 * array — so exhaustiveness is not the guarantee. The guarantee is that editing
 * one field NEVER touches another, and that a field no form renders survives a
 * round trip through the form. That is what these tests pin, because it is the
 * property the whole mode design rests on and the one that fails silently.
 */

const MANIFEST: Json = {
  schema_version: "suite/v1",
  id: "demo",
  name: "Demo",
  description: "a suite",
  capabilities: ["governance"],
  agents: [{ ref: "scripted@0.6" }],
  scenarios: [{ schema_version: "scenario/v1", name: "s1", task: "t" }],
  environment: { tools: [{ id: "read" }], simulation: { enabled: true } },
  execution: { strategy: "matrix", repeats: 5, seed_policy: "per_repeat" },
  evaluation: { metrics: [{ name: "task_success" }] },
  artifact: { include_traces: true, sections: ["overview"] },
  // A key NO form renders. It is the whole point: the Builder must carry it.
  ui_schema: { sections: [{ id: "execution", advanced: true }] },
  documentation_url: "https://example.test/docs",
};

describe("the six sections are the ones the spec names", () => {
  it("no more and no fewer", () => {
    expect(SECTIONS.map((s) => s.id)).toEqual([
      "agents",
      "scenarios",
      "environment",
      "execution",
      "evaluation",
      "artifact",
    ]);
  });

  it("every rendered path is unique", () => {
    const paths = renderedPaths();
    expect(new Set(paths).size).toBe(paths.length);
  });
});

describe("the Builder is a form, not a wall of JSON textareas", () => {
  it("capabilities are clickable known values, not a comma-separated guess", () => {
    // nobody should have to already know that 'governance' is a word this
    // platform understands — the screen offers it
    const spec = IDENTITY.find((field) => field.path === "capabilities");
    expect(spec?.widget).toBe("chips");
    expect(spec?.options).toContain("governance");
  });

  it("the agents section pins nothing in basic mode", () => {
    // the executor is bound at run time (the Run panel's runtime dropdown);
    // agents[] describes multi-agent topologies and lives in Advanced
    const agents = SECTIONS.find((section) => section.id === "agents");
    expect(agents?.fields.every((field) => field.advanced)).toBe(true);
    expect(agents?.description).toBeTruthy();
  });

  it("the routinely edited arrays are item forms with real inputs", () => {
    for (const path of ["scenarios", "evaluation.metrics", "evaluation.aggregations"]) {
      const spec = SECTIONS.flatMap((section) => section.fields).find(
        (field) => field.path === path,
      );
      expect(spec?.widget, path).toBe("list");
      expect(spec?.item?.length, path).toBeGreaterThan(0);
    }
  });

  it("boolean fields are toggles, never a dropdown containing the word 'true'", () => {
    const all = [...IDENTITY, ...SECTIONS.flatMap((section) => section.fields)];
    for (const path of ["environment.simulation.enabled", "artifact.include_traces"]) {
      expect(all.find((field) => field.path === path)?.widget, path).toBe("checkbox");
    }
    for (const field of all.filter((f) => f.widget === "select")) {
      expect(field.options, field.path).not.toContain("true");
    }
  });

  it("no mode shows a raw JSON editor — deep structures LINK to the one text editor", () => {
    // there is exactly one text editor, the YAML mode. A JSON textarea in a
    // form would be a second one, duplicating it badly; the widget vocabulary
    // no longer even contains "json", and what cannot be formed is a
    // "yaml-link" that jumps the cursor to that key.
    const all = [...IDENTITY, ...SECTIONS.flatMap((section) => section.fields)];
    expect(all.filter((field) => (field.widget as string) === "json")).toEqual([]);
    for (const path of ["environment.tools", "environment.fixtures"]) {
      expect(all.find((field) => field.path === path)?.widget, path).toBe("yaml-link");
    }
  });

  it("advanced structures that CAN be forms are forms", () => {
    const all = SECTIONS.flatMap((section) => section.fields);
    for (const path of ["agents", "execution.conditions", "evaluation.evaluators", "regressions"]) {
      expect(all.find((field) => field.path === path)?.widget, path).toBe("list");
    }
    expect(all.find((field) => field.path === "topology.kind")?.widget).toBe("select");
    expect(all.find((field) => field.path === "execution.budgets.max_usd")?.widget).toBe(
      "number",
    );
  });
});

describe("a new scenario is one that RESOLVES", () => {
  /**
   * The old blank was a constant seeding `tools: []` and
   * `task_success: {event: "final_output"}`. Both are refused —
   * `tools: minItems 1`, and the runtime evaluator supports only `tool_call` —
   * so "+ Add" reliably invalidated the suite, and the item form (name and
   * task) offered no way to fix either without opening YAML.
   *
   * The Python side of this contract is
   * `tests/test_suite_platform_contracts.py`, which runs a scenario of exactly
   * this shape through the real validator.
   */
  it("references a tool the suite actually declares", () => {
    const blank = blankScenario(MANIFEST);
    expect(blank.tools).toEqual([{ $ref: "read" }]);
    expect(suiteToolIds(MANIFEST)).toEqual(["read"]);
  });

  it("asserts success on a call to that same tool", () => {
    // `final_output` is in the schema and NOT in the evaluator, so a blank
    // using it produced a scenario that validates on paper and refuses to run
    expect(blankScenario(MANIFEST).task_success).toEqual({
      event: "tool_call",
      tool: "read",
    });
  });

  it("leaves tools empty when the suite declares none", () => {
    // not a blank's fault to paper over: the suite has nothing to reference,
    // and `minItems 1` is the true state of the document
    const toolless: Json = { ...MANIFEST, environment: { simulation: {} } };
    expect(blankScenario(toolless).tools).toEqual([]);
  });

  it("the scenarios field resolves its blank against the document", () => {
    const spec = SECTIONS.flatMap((s) => s.fields).find((f) => f.path === "scenarios");
    expect(typeof spec?.blank).toBe("function");
    expect(blankFor(spec!, MANIFEST)).toEqual(blankScenario(MANIFEST));
  });

  it("a constant blank still works", () => {
    const spec = SECTIONS.flatMap((s) => s.fields).find(
      (f) => f.path === "execution.conditions",
    );
    expect(blankFor(spec!, MANIFEST)).toEqual(spec?.blank);
  });
});

describe("editing one field touches nothing else", () => {
  it("writes the value at the path", () => {
    const next = writePath(MANIFEST, "execution.repeats", 12);
    expect(readPath(next, "execution.repeats")).toBe(12);
  });

  it("leaves every other key byte-identical", () => {
    const next = writePath(MANIFEST, "execution.repeats", 12);
    const before = { ...MANIFEST, execution: { ...(MANIFEST.execution as Json) } };
    delete (before.execution as Json).repeats;
    const after = { ...next, execution: { ...(next.execution as Json) } };
    delete (after.execution as Json).repeats;
    expect(JSON.stringify(after)).toBe(JSON.stringify(before));
  });

  it("does not mutate the original document", () => {
    const snapshot = JSON.stringify(MANIFEST);
    writePath(MANIFEST, "execution.repeats", 99);
    expect(JSON.stringify(MANIFEST)).toBe(snapshot);
  });

  it("carries a key no form renders", () => {
    // `ui_schema` and `documentation_url` are in no section. Saving from Basic
    // must not drop what Basic does not show.
    const rendered = new Set(renderedPaths());
    expect(rendered.has("ui_schema")).toBe(false);
    let edited: Json = MANIFEST;
    for (const path of renderedPaths()) {
      const value = readPath(edited, path);
      if (value !== undefined) edited = writePath(edited, path, value);
    }
    expect(edited.ui_schema).toEqual(MANIFEST.ui_schema);
    expect(edited.documentation_url).toBe(MANIFEST.documentation_url);
  });

  it("rewriting every rendered field with its own value is a no-op", () => {
    // the Basic form's worst case: a user opens it, touches nothing the form
    // shows, and saves. The document must come back identical.
    let edited: Json = MANIFEST;
    for (const path of renderedPaths()) {
      const value = readPath(edited, path);
      if (value !== undefined) edited = writePath(edited, path, value);
    }
    expect(JSON.stringify(edited)).toBe(JSON.stringify(MANIFEST));
  });
});

describe("a suite contributes its own fields", () => {
  /**
   * RFC §12/§13: "Every suite contributes declarative schemas."
   * `suite.schema.json` describes `config_schema` as "the Builder renders it;
   * a value that fails it is rejected at author time" — and nothing rendered
   * it and nothing validated it, so the Suite protocol had no author-time
   * surface: a third-party suite could declare knobs no screen ever showed.
   */
  const WITH_CONFIG: Json = {
    ...MANIFEST,
    config_schema: {
      type: "object",
      properties: {
        depth: { type: "integer", title: "Search depth", description: "how far" },
        style: { enum: ["terse", "verbose"] },
        strict: { type: "boolean" },
        corpora: { type: "array", items: { type: "string" } },
        families: { type: "array", items: { enum: ["a", "b"] } },
        tuning: { type: "object" },
        note: { type: "string" },
      },
    },
    ui_schema: {
      sections: [
        { id: "execution", fields: ["depth", "style"] },
        { id: "evaluation", fields: ["strict"], advanced: true },
      ],
    },
    config: { depth: 2 },
  };

  it("places a field where the ui_schema puts it, in that order", () => {
    const { bySection } = configFields(WITH_CONFIG);
    expect(bySection.execution?.map((f) => f.path)).toEqual([
      "config.depth",
      "config.style",
    ]);
    expect(bySection.evaluation?.map((f) => f.path)).toEqual(["config.strict"]);
  });

  it("a section's advanced flag hides its fields in Basic, never drops them", () => {
    const { bySection } = configFields(WITH_CONFIG);
    expect(bySection.execution?.every((f) => f.advanced)).toBe(false);
    expect(bySection.evaluation?.every((f) => f.advanced)).toBe(true);
  });

  it("a field no section claims is still rendered", () => {
    // the same carry-through rule the manifest gets: never silently lost
    expect(configFields(WITH_CONFIG).unplaced.map((f) => f.path)).toEqual([
      "config.corpora",
      "config.families",
      "config.tuning",
      "config.note",
    ]);
  });

  it("the widget follows the JSON Schema, so no control offers what the validator refuses", () => {
    const all = [
      ...Object.values(configFields(WITH_CONFIG).bySection).flat(),
      ...configFields(WITH_CONFIG).unplaced,
    ];
    const widget = (path: string) => all.find((f) => f.path === path)?.widget;
    expect(widget("config.depth")).toBe("number");
    expect(widget("config.style")).toBe("select");
    expect(widget("config.strict")).toBe("checkbox");
    expect(widget("config.corpora")).toBe("tags");
    expect(widget("config.families")).toBe("chips");
    expect(widget("config.note")).toBe("text");
    // not formable — links to the one text editor, like every other deep value
    expect(widget("config.tuning")).toBe("yaml-link");
  });

  it("title and description become the label and the help", () => {
    const depth = configFields(WITH_CONFIG).bySection.execution?.[0];
    expect(depth?.label).toBe("Search depth");
    expect(depth?.help).toBe("how far");
    // no title -> the property name, never a blank label
    expect(configFields(WITH_CONFIG).bySection.execution?.[1]?.label).toBe("style");
  });

  it("a layout cannot introduce a field", () => {
    // the schema says so outright; the validator refuses it, and rendering it
    // would show an input writing a value nothing describes
    const ghost: Json = {
      ...WITH_CONFIG,
      ui_schema: { sections: [{ id: "execution", fields: ["depth", "ghost"] }] },
    };
    expect(configFields(ghost).bySection.execution?.map((f) => f.path)).toEqual([
      "config.depth",
    ]);
  });

  it("a suite with no config_schema contributes nothing", () => {
    expect(configFields(MANIFEST)).toEqual({ bySection: {}, unplaced: [] });
  });

  it("its values live under config, so writePath reaches them like any field", () => {
    const next = writePath(WITH_CONFIG, "config.depth", 9);
    expect((next.config as Json).depth).toBe(9);
    // and clearing one removes it rather than writing null. An emptied parent
    // is removed too, so `config` is gone entirely once its last key is.
    expect(writePath(next, "config.depth", undefined).config).toBeUndefined();
  });
});

describe("clearing a field removes it", () => {
  it("an optional field cleared leaves the manifest", () => {
    // not `null`: the schema rejects null for most of these, so writing one
    // turns "I cleared this" into "this suite is invalid"
    const next = writePath(MANIFEST, "description", undefined);
    expect("description" in next).toBe(false);
  });

  it("a nested field cleared leaves its parent when the parent still has keys", () => {
    const next = writePath(MANIFEST, "execution.seed_policy", undefined);
    expect(next.execution).toEqual({ strategy: "matrix", repeats: 5 });
  });

  it("an emptied parent is removed too, not left as {}", () => {
    const one: Json = { id: "x", artifact: { renderer: "default" } };
    const next = writePath(one, "artifact.renderer", undefined);
    expect("artifact" in next).toBe(false);
  });
});

describe("advanced hides, it never drops", () => {
  it("basic mode renders a strict subset of advanced", () => {
    for (const section of [...SECTIONS.map((s) => s.fields), IDENTITY]) {
      const basic = section.filter((f) => !f.advanced).map((f) => f.path);
      const advanced = section.map((f) => f.path);
      expect(advanced).toEqual(expect.arrayContaining(basic));
    }
  });

  it("an advanced-only field survives a basic-mode edit", () => {
    // `execution.conditions` is advanced. A user in Basic changing `repeats`
    // must not lose the governance arms.
    const withArms = writePath(MANIFEST, "execution.conditions", [{ id: "governed" }]);
    const basicPaths = [...IDENTITY, ...SECTIONS.flatMap((s) => s.fields)]
      .filter((f) => !f.advanced)
      .map((f) => f.path);
    let edited = withArms;
    for (const path of basicPaths) {
      const value = readPath(edited, path);
      if (value !== undefined) edited = writePath(edited, path, value);
    }
    edited = writePath(edited, "execution.repeats", 3);
    expect(readPath(edited, "execution.conditions")).toEqual([{ id: "governed" }]);
  });
});

describe("the forms cover what a real suite actually uses", () => {
  it("every top-level key of the demo manifest is reachable or deliberately carried", () => {
    // Not "every schema field has a widget" — that would fail the day the
    // schema grows one. The check is that a key is either editable or KNOWN to
    // be carried through, so nothing is uneditable by accident.
    const rendered = new Set(renderedPaths().map((p) => p.split(".")[0]));
    const carried = new Set(["schema_version", "ui_schema", "config_schema",
                             "documentation_url", "origin"]);
    for (const key of Object.keys(MANIFEST)) {
      expect(
        rendered.has(key) || carried.has(key),
        `${key} is neither editable nor a known carried field`,
      ).toBe(true);
    }
  });
});
