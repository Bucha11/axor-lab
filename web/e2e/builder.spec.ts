import { expect, Page, Route, test } from "@playwright/test";
import { json, stubShell } from "./helpers";

/**
 * E2E for the SUITES LIST (src/screens/Suites.tsx) and the SUITE BUILDER
 * (src/screens/Builder.tsx). Everything the screens fetch is stubbed with
 * page.route, matching the exact response shapes in src/lib/api.ts — a
 * wrong-shaped mock crashes the screen and blanks the app.
 *
 * All tests run in OPEN mode (auth_required:false) so there is no login gate,
 * and use hash routing (goto("/#/suites…")).
 */

const OPEN = { auth_required: false, guest: false };

/** A realistic, schema-shaped manifest that populates the Builder's Basic form:
 * identity (id/name/description/capabilities), a simulated-tools toggle, one
 * scenario, execution and artifact sections. */
const MANIFEST = {
  id: "suite-alpha",
  name: "Alpha Suite",
  version: "1",
  description: "An example suite for tests.",
  tags: ["demo"],
  capabilities: ["governance"],
  environment: {
    simulation: { enabled: true },
    // a real manifest: per-scenario validation is largely ABOUT the tools, so a
    // fixture with none cannot show whether the client sends them
    tools: [
      {
        schema_version: "tool-manifest/v1",
        id: "note",
        args_schema: { type: "object" },
        effect: { default_class: "READ", driving_args: [] },
        side_effecting: false,
      },
    ],
  },
  scenarios: [
    {
      schema_version: "scenario/v1",
      name: "scn-1",
      task: "do a thing",
      inputs: {},
      tools: [],
      fixtures: {},
      task_success: { event: "final_output" },
    },
  ],
  execution: { strategy: "matrix", repeats: 2, seed_policy: "fixed" },
  evaluation: { metrics: [], aggregations: [] },
  artifact: { include_traces: true, sections: ["overview"] },
};

const SUITE_CARDS = [
  { id: "suite-alpha", name: "Alpha Suite", description: "The alpha one.", origin: "workspace", available: true },
  { id: "suite-beta", name: "Beta Suite", description: "The beta one.", origin: "builtin", available: true },
  {
    id: "suite-gamma",
    name: "Gamma Suite",
    description: "Not shipped yet.",
    origin: "builtin",
    available: false,
    reason: "multi-agent execution not available",
  },
];

const RUNTIMES = [
  { runtime_ref: "rt-1", runtime_label: "My Agent", status: "connected" },
  { runtime_ref: "rt-2", runtime_label: "Other Agent", status: "connected" },
];

const YAML_TEXT = "id: suite-alpha\nname: Alpha Suite\ndescription: An example suite for tests.\n";

interface Opts {
  suites?: unknown[];
  /** the ORG's shared catalog — a different list from `suites`. */
  orgSuites?: unknown[];
  manifest?: Record<string, unknown>;
  runtimes?: unknown[];
  registryScenarios?: { name: string; task: string }[];
  validate?: { ok: boolean; errors: string[]; suite?: unknown };
  yaml?: string;
  createdId?: string;
  plan?: {
    trials: string[];
    conditions: string[];
    blockers: string[];
    estimate?: Record<string, number>;
  };
}

/** Register every endpoint the two screens touch. The generic `**​/suites/*`
 * handler is registered BEFORE the single-segment specials (validate, to-yaml,
 * blank) so those, added later, take precedence for their exact paths. */
async function routes(page: Page, opts: Opts = {}): Promise<void> {
  const manifest = opts.manifest ?? MANIFEST;
  await stubShell(page, OPEN);

  await page.route("**/runtimes", json(200, { runtimes: opts.runtimes ?? [] }));
  // the registry `scenario_refs` resolve against
  await page.route("**/scenarios", json(200, {
    scenarios: opts.registryScenarios ?? [],
  }));


  // dispatch is two-segment — no overlap with the generic single-segment handler
  await page.route("**/suites/*/dispatch", json(200, {
    run_id: "run-7",
    state: "running",
    planned_trials: ["t1", "t2"],
  }));

  // generic suite-by-id: GET returns the manifest, PUT (save) / DELETE ack
  await page.route("**/suites/*", (route: Route) => {
    const method = route.request().method();
    if (method === "PUT") return json(200, { id: manifest.id })(route);
    if (method === "DELETE") return json(200, { id: manifest.id, deleted: true })(route);
    return json(200, manifest)(route);
  });

  // single-segment specials — registered after the generic so they win
  await page.route("**/suites/validate", json(200, opts.validate ?? { ok: true, errors: [] }));
  await page.route("**/suites/to-yaml", (route: Route) =>
    route.fulfill({ status: 200, contentType: "text/plain", body: opts.yaml ?? YAML_TEXT }),
  );
  await page.route("**/suites/blank", json(200, MANIFEST));

  // list (GET) + create (POST) share the /suites path — split by method
  await page.route("**/suites", (route: Route) => {
    if (route.request().method() === "POST") {
      return json(200, { id: opts.createdId ?? "suite-new" })(route);
    }
    return json(200, { suites: opts.suites ?? [] })(route);
  });

  // authoring aids. `scenarios/validate` echoes back WHICH tool manifests it
  // was given, so a test can assert the client sent them rather than trusting
  // an ok:true that a manifest-less call would also produce.
  await page.route("**/scenarios/validate", async (route: Route) => {
    const body = route.request().postDataJSON() as {
      scenario?: Record<string, unknown>;
      manifests?: Record<string, unknown>;
    };
    const manifests = Object.keys(body.manifests ?? {});
    return json(200, manifests.length > 0
      ? { ok: true, errors: [] }
      : {
          ok: false,
          errors: [`[validating] tool 'note' has no manifest in the bundle`],
        })(route);
  });
  await page.route("**/suites/plan", json(200, opts.plan ?? {
    trials: ["scn-1:ungoverned:0", "scn-1:ungoverned:1"],
    conditions: ["ungoverned"],
    blockers: [],
    estimate: { trials: 2, scenarios: 1, conditions: 1, repeats: 2 },
  }));

  // LAST, so it wins: `**/suites` matches `/registry/suites` too, and without
  // this the workspace's own cards render again under the org catalog — every
  // by-name assertion then hits two elements. Playwright gives precedence to the
  // most recently registered route.
  await page.route("**/registry/suites", json(200, { suites: opts.orgSuites ?? [] }));
}

/** Fail loud on an uncaught render error — a wrong mock shape throws
 * "Cannot convert undefined or null to object" and blanks the app. */
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  return errors;
}

test.describe("Suites list", () => {
  test("renders the suites from GET /suites", async ({ page }) => {
    const errs = watchErrors(page);
    await routes(page, { suites: SUITE_CARDS });
    await page.goto("/#/suites");

    await expect(page.getByRole("heading", { name: "Suites", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Alpha Suite" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Beta Suite" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Gamma Suite" })).toBeVisible();
    // the not-yet-available suite is labelled and shows its reason
    await expect(page.getByText("Not yet")).toBeVisible();
    await expect(page.getByText("multi-agent execution not available")).toBeVisible();
    expect(errs).toEqual([]);
  });

  test("an empty list renders no suite cards", async ({ page }) => {
    await routes(page, { suites: [] });
    await page.goto("/#/suites");

    await expect(page.getByRole("heading", { name: "Suites", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "+ New suite" })).toBeVisible();
    await expect(page.locator(".grid .card")).toHaveCount(0);
  });

  test("clicking an available suite opens the Builder", async ({ page }) => {
    await routes(page, { suites: SUITE_CARDS });
    await page.goto("/#/suites");
    await page.getByRole("heading", { name: "Alpha Suite" }).click();
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
  });
});

test.describe("Creating a suite", () => {
  test("the New suite button creates and navigates into the Builder", async ({ page }) => {
    await routes(page, { suites: [] });
    await page.goto("/#/suites");
    await page.getByRole("button", { name: "+ New suite" }).click();
    // POST /suites → navigate(/suites/<id>) → Builder loads the new manifest
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Suite", exact: true })).toBeVisible();
  });
});

test.describe("Suite Builder", () => {
  test("opening a suite renders the Builder with fields populated", async ({ page }) => {
    const errs = watchErrors(page);
    await routes(page, { runtimes: RUNTIMES });
    await page.goto("/#/suites/suite-alpha");

    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    // scope the identity fields to the "Suite" card — scenario items also have a
    // "Name" field, and the wrapping <label> pollutes accessible names
    const suiteCard = page.locator(".card", {
      has: page.getByRole("heading", { name: "Suite", exact: true }),
    });
    await expect(suiteCard.getByLabel("Suite id", { exact: true })).toHaveValue("suite-alpha");
    await expect(suiteCard.getByLabel("Name", { exact: true })).toHaveValue("Alpha Suite");
    // Description is the only textarea in the Suite card (getByLabel does not
    // associate a <label>-wrapped textarea reliably)
    await expect(suiteCard.locator("textarea")).toHaveValue("An example suite for tests.");
    // capabilities chip for a value in the manifest renders selected
    await expect(suiteCard.locator("button.chip", { hasText: /^governance$/ })).toHaveClass(
      /chip-on/,
    );
    expect(errs).toEqual([]);
  });

  test("editing text fields updates Builder state", async ({ page }) => {
    await routes(page);
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    const suiteCard = page.locator(".card", {
      has: page.getByRole("heading", { name: "Suite", exact: true }),
    });

    const id = suiteCard.getByLabel("Suite id", { exact: true });
    await id.fill("edited-suite");
    await expect(id).toHaveValue("edited-suite");

    const desc = suiteCard.locator("textarea");
    await desc.fill("A rewritten description.");
    await expect(desc).toHaveValue("A rewritten description.");
  });

  test("an added scenario references a tool the suite declares", async ({ page }) => {
    // The old blank seeded `tools: []` and an unevaluable `task_success`, so
    // "+ Add" reliably invalidated the suite — and the item form (name, task)
    // gave no way to fix either. What the button writes is asserted through
    // what Save posts, because that is the document the server sees.
    const saved: Record<string, unknown>[] = [];
    await routes(page);
    await page.route("**/suites/suite-alpha", async (route: Route) => {
      if (route.request().method() !== "PUT") return json(200, MANIFEST)(route);
      saved.push(route.request().postDataJSON() as Record<string, unknown>);
      return json(200, { id: "suite-alpha" })(route);
    });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();

    const scenarios = page.locator(".card", {
      has: page.getByRole("heading", { name: "Scenarios" }),
    });
    await scenarios.getByRole("button", { name: "+ Add" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("saved")).toBeVisible();

    const posted = (saved[0].suite as Record<string, unknown>);
    const added = (posted.scenarios as Record<string, unknown>[])[1];
    expect(added.tools).toEqual([{ $ref: "note" }]);
    expect(added.task_success).toEqual({ event: "tool_call", tool: "note" });
  });

  test("adding and removing a scenario updates the list", async ({ page }) => {
    await routes(page);
    await page.goto("/#/suites/suite-alpha");
    const scenarios = page.locator(".card", {
      has: page.getByRole("heading", { name: "Scenarios", exact: true }),
    });
    await expect(scenarios.getByRole("button", { name: "Remove" })).toHaveCount(1);

    await scenarios.getByRole("button", { name: "+ Add" }).click();
    await expect(scenarios.getByRole("button", { name: "Remove" })).toHaveCount(2);

    await scenarios.getByRole("button", { name: "Remove" }).first().click();
    await expect(scenarios.getByRole("button", { name: "Remove" })).toHaveCount(1);
  });

  test("toggling the simulated-tools checkbox updates state", async ({ page }) => {
    await routes(page);
    await page.goto("/#/suites/suite-alpha");
    const env = page.locator(".card", {
      has: page.getByRole("heading", { name: "Environment & Tools", exact: true }),
    });
    const toggle = env.getByRole("checkbox");
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await expect(toggle).not.toBeChecked();
    await toggle.check();
    await expect(toggle).toBeChecked();
  });

  test("toggling a capability chip updates its selected state", async ({ page }) => {
    await routes(page);
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    const provenance = page.locator("button.chip", { hasText: /^provenance$/ });
    await expect(provenance).not.toHaveClass(/chip-on/);
    await provenance.click();
    await expect(provenance).toHaveClass(/chip-on/);
  });

  test("Validate shows a valid result", async ({ page }) => {
    await routes(page, { validate: { ok: true, errors: [] } });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    await page.getByRole("button", { name: "Validate" }).click();
    await expect(page.getByText("valid", { exact: true })).toBeVisible();
  });

  test("Validate shows the schema errors", async ({ page }) => {
    await routes(page, {
      validate: { ok: false, errors: ["scenarios: at least one is required"] },
    });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    await page.getByRole("button", { name: "Validate" }).click();
    await expect(page.getByText("1 error(s)")).toBeVisible();
    await expect(page.getByText("scenarios: at least one is required")).toBeVisible();
  });

  test("Save persists and shows the saved confirmation", async ({ page }) => {
    const errs = watchErrors(page);
    await routes(page, { validate: { ok: true, errors: [] } });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("saved", { exact: true })).toBeVisible();
    expect(errs).toEqual([]);
  });

  test("YAML mode renders the server-serialized YAML", async ({ page }) => {
    await routes(page, { yaml: YAML_TEXT });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    await page.getByRole("button", { name: "YAML", exact: true }).click();
    await expect(page.locator("textarea.yaml")).toHaveValue(YAML_TEXT);
  });

  test("Check scenarios sends the suite's tool manifests", async ({ page }) => {
    // Per-scenario validation is largely about the tools. Sending the scenario
    // alone reported "tool X has no manifest in the bundle" for every tool of
    // every scenario — the button was pure false positives on suites that
    // validate perfectly. The stub answers ok only when the manifests arrive.
    const posted: Record<string, unknown>[] = [];
    await routes(page);
    await page.route("**/scenarios/validate", async (route: Route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      posted.push(body);
      return json(200, { ok: true, errors: [] })(route);
    });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();

    await page.getByRole("button", { name: "Check scenarios" }).click();
    await expect(page.getByText("Every scenario validates on its own.")).toBeVisible();
    expect(posted).toHaveLength(1);
    expect(Object.keys(posted[0].manifests as Record<string, unknown>)).toEqual(["note"]);
  });

  test("Check scenarios reports a real per-scenario failure", async ({ page }) => {
    await routes(page);
    await page.route("**/scenarios/validate", json(200, {
      ok: false,
      errors: ["[validating] no fixture places $injection into an untrusted field"],
    }));
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();

    await page.getByRole("button", { name: "Check scenarios" }).click();
    // named by SCENARIO — the whole reason this exists next to whole-suite
    // validation, which cannot say which one broke
    await expect(page.locator(".errors")).toContainText("scn-1");
    await expect(page.locator(".errors")).toContainText("untrusted field");
  });

  test("Preview plan names the arms the run will name", async ({ page }) => {
    // It used to hand-build an experiment/v1 from the manifest and plan THAT,
    // which invented the arm id "condition" for a suite declaring none. The
    // preview now goes to /suites/plan — the same planner a dispatch runs.
    const posted: Record<string, unknown>[] = [];
    await routes(page);
    await page.route("**/suites/plan", async (route: Route) => {
      posted.push(route.request().postDataJSON() as Record<string, unknown>);
      return json(200, {
        trials: ["scn-1:ungoverned:0", "scn-1:ungoverned:1"],
        conditions: ["ungoverned"],
        blockers: [],
        estimate: { trials: 2 },
      })(route);
    });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();

    await page.getByRole("button", { name: "Preview plan" }).click();
    await expect(page.getByText(/2 trial unit\(s\)/)).toBeVisible();
    await expect(page.getByText(/1 arm\(s\)/)).toBeVisible();
    await expect(page.locator("code", { hasText: "ungoverned" })).toBeVisible();
    // the whole manifest goes over, not a hand-assembled experiment
    expect((posted[0].suite as Record<string, unknown>).id).toBe("suite-alpha");
  });

  test("Preview plan surfaces why a dispatch would be refused", async ({ page }) => {
    // seeing the plan is exactly how you find out the run is impossible — the
    // preview reports the blocker instead of failing
    await routes(page, {
      plan: {
        trials: ["scn-1:ungoverned:0"],
        conditions: ["ungoverned"],
        blockers: ["topology 'swarm' is accepted and validated but not yet executable"],
      },
    });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();

    await page.getByRole("button", { name: "Preview plan" }).click();
    await expect(page.getByText(/1 trial unit\(s\)/)).toBeVisible();
    await expect(page.locator(".errors")).toContainText("not yet executable");
  });

  test("Scenario refs offer the registry's names, not a blank text box", async ({ page }) => {
    // As free text the field could only break a suite: nothing filled the
    // registry, so any value validated as "scenario_ref 'x' resolves to
    // nothing". The chips are the names that actually resolve.
    await routes(page, {
      registryScenarios: [
        { name: "shared-note", task: "Record a note." },
        { name: "shared-transfer", task: "Move money." },
      ],
    });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    await page.getByRole("button", { name: "Advanced" }).click();

    const scenarios = page.locator(".card", {
      has: page.getByRole("heading", { name: "Scenarios" }),
    });
    await expect(scenarios.locator("button.chip", { hasText: /^shared-note$/ })).toBeVisible();
    const chip = scenarios.locator("button.chip", { hasText: /^shared-transfer$/ });
    await expect(chip).not.toHaveClass(/chip-on/);
    await chip.click();
    await expect(chip).toHaveClass(/chip-on/);
  });

  test("an empty registry leaves the field usable rather than blocking", async ({ page }) => {
    await routes(page, { registryScenarios: [] });
    await page.goto("/#/suites/suite-alpha");
    await expect(page.getByRole("heading", { name: "Suite Builder" })).toBeVisible();
    await page.getByRole("button", { name: "Advanced" }).click();
    // no options, but the typed-extra input is still there — and a name nothing
    // answers to is refused by the validator, which is the honest place
    const scenarios = page.locator(".card", {
      has: page.getByRole("heading", { name: "Scenarios" }),
    });
    await expect(scenarios.locator("input.chip-input")).toBeVisible();
  });

  test("Run panel lists runtimes and dispatching starts a run", async ({ page }) => {
    const errs = watchErrors(page);
    await routes(page, { runtimes: RUNTIMES, validate: { ok: true, errors: [] } });
    // the run report the dispatch navigates to
    await page.route("**/runs/*/report", json(200, {
      run_id: "run-7",
      state: "running",
      planned_trials: 2,
      trials_by_status: {},
      coverage: { completed: 0, planned: 2 },
      metric_coverage: {},
      aggregates: [],
    }));
    await page.route("**/runs/*/results", json(200, {
      run_id: "run-7",
      state: "running",
      planned_trials: [],
      trials: [],
      aggregates: [],
    }));
    await page.route("**/runs/*/events", (route) => route.abort());

    await page.goto("/#/suites/suite-alpha");
    const runCard = page.locator(".card", {
      has: page.getByRole("heading", { name: "Run", exact: true }),
    });
    await expect(runCard.getByRole("combobox")).toBeVisible();
    await runCard.getByRole("combobox").selectOption("rt-1");
    await runCard.getByRole("button", { name: "Save & run" }).click();

    // dispatch → navigate(/runs/run-7) → the Run Report renders
    await expect(page.getByRole("heading", { name: "Run run-7" })).toBeVisible();
    expect(errs).toEqual([]);
  });
});
