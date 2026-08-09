import { describe, expect, it } from "vitest";
import type { Json } from "./api";
import { IDENTITY, SECTIONS, readPath, renderedPaths, writePath } from "./sections";

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

  it("basic mode shows no raw JSON editor at all", () => {
    // JSON stays available — in Advanced and YAML. Basic is the mode for
    // someone who does not want to hand-balance brackets.
    const basic = [...IDENTITY, ...SECTIONS.flatMap((section) => section.fields)]
      .filter((field) => !field.advanced);
    expect(basic.filter((field) => field.widget === "json")).toEqual([]);
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
