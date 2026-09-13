import { expect, test } from "@playwright/test";
import { json, stubShell } from "./helpers";

// E2E coverage for HOME, ARTIFACTS, PLAYGROUND and INTEGRATIONS. Each test runs
// in OPEN mode (auth_required:false) and stubs only the endpoints its screen
// actually calls, matching the exact response shapes in src/lib/api.ts. A
// wrong-shaped payload blanks the screen, so payloads mirror HomePayload /
// ArtifactRow / PlaygroundResult / RuntimeRow precisely.

test.beforeEach(async ({ page }) => {
  // Surface any React render crash instead of a silent blank screen.
  page.on("pageerror", (err) => console.log("PAGEERROR:", err.message));
  await stubShell(page, { auth_required: false, guest: false });
});

// ── HOME ─────────────────────────────────────────────────────────────────────

test.describe("Home", () => {
  const HOME = {
    onboarding_step: "run_a_suite",
    quick_actions: [
      { id: "connect_runtime", label: "Set up an integration", endpoint: "/runtimes/connect" },
      // an id the UI has no route for — Home filters it out
      { id: "mystery_action", label: "Should be hidden", endpoint: "/x" },
    ],
    suites: [
      {
        id: "s1",
        name: "Refund Policy Suite",
        description: "Checks refund flows",
        available: true,
        capabilities: ["tools"],
      },
    ],
    counts: { connected_runtimes: 2, invariants: 7 },
    recent_runs: [
      { run_id: "run_abc", state: "completed" },
      { run_id: "run_def", state: "running" },
    ],
  };

  test("renders a populated home: step, quick actions, suites, counts, recent runs", async ({
    page,
  }) => {
    await page.route("**/home", json(200, HOME));
    await page.goto("/#/");

    // heading + onboarding step (STEP_COPY["run_a_suite"].title)
    await expect(page.getByRole("heading", { name: "Axor Lab" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Run a suite" })).toBeVisible();

    // suites section
    await expect(page.getByRole("heading", { name: "Refund Policy Suite" })).toBeVisible();

    // counts, rendered from Object.entries with underscores turned to spaces
    await expect(page.getByText("connected runtimes", { exact: true })).toBeVisible();
    await expect(page.getByText("2", { exact: true })).toBeVisible();
    await expect(page.getByText("invariants", { exact: true })).toBeVisible();
    await expect(page.getByText("7", { exact: true })).toBeVisible();

    // recent runs
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
    await expect(page.getByRole("link", { name: "run_abc" })).toBeVisible();
    await expect(page.getByRole("link", { name: "run_def" })).toBeVisible();

    // a quick action with a known id is rendered and clickable; the unknown id is not
    const quick = page.getByRole("button", { name: "Set up an integration" });
    await expect(quick).toBeVisible();
    await expect(quick).toBeEnabled();
    await expect(page.getByRole("button", { name: "Should be hidden" })).toHaveCount(0);
  });

  test("a quick action navigates to its screen", async ({ page }) => {
    await page.route("**/home", json(200, HOME));
    await page.route("**/runtimes", json(200, { runtimes: [] }));
    await page.goto("/#/");

    await page.getByRole("button", { name: "Set up an integration" }).click();

    // connect_runtime -> /integrations
    await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();
  });
});

// ── ARTIFACTS ────────────────────────────────────────────────────────────────

test.describe("Artifacts", () => {
  test("lists artifacts from GET /artifacts", async ({ page }) => {
    await page.route(
      "**/artifacts",
      json(200, {
        artifacts: [
          { artifact_id: "art_1", created: "2026-01-01", suite_id: "s1" },
          { artifact_id: "art_2", created: "2026-02-02", suite_id: "s2" },
        ],
      }),
    );
    await page.goto("/#/artifacts");

    await expect(page.getByRole("heading", { name: "Artifacts" })).toBeVisible();
    await expect(page.getByRole("link", { name: "art_1" })).toBeVisible();
    await expect(page.getByRole("link", { name: "art_2" })).toBeVisible();
  });

  test("shows an empty state when there are no artifacts", async ({ page }) => {
    await page.route("**/artifacts", json(200, { artifacts: [] }));
    await page.goto("/#/artifacts");

    await expect(page.getByRole("heading", { name: "Artifacts" })).toBeVisible();
    await expect(page.getByText("No artifacts yet.")).toBeVisible();
  });

  test("renders artifact detail from GET /artifacts/:id", async ({ page }) => {
    await page.route(
      "**/artifacts/art_1",
      json(200, {
        created: "2026-01-01",
        suite: { id: "s1", name: "Refund Suite" },
        bundle: {
          trials: [{ status: "completed" }, { status: "completed" }, { status: "failed" }],
          aggregates: [
            { metric: "accuracy", condition_id: "baseline", estimate: 0.9, n: 10 },
          ],
        },
        reproduce: { command: "axor reproduce art_1", reproducibility: "bit-exact" },
      }),
    );
    await page.goto("/#/artifacts/art_1");

    await expect(page.getByRole("heading", { name: "Artifact art_1" })).toBeVisible();
    await expect(page.getByText("Refund Suite (s1)")).toBeVisible();

    // trial status tally (byStatus) -> Stat labels + values
    await expect(page.getByText("completed", { exact: true })).toBeVisible();
    await expect(page.getByText("failed", { exact: true })).toBeVisible();

    // metrics table
    await expect(page.getByRole("heading", { name: "Metrics" })).toBeVisible();
    await expect(page.getByText("accuracy")).toBeVisible();
    await expect(page.getByText("0.900")).toBeVisible();

    // reproduce command
    await expect(page.getByText("axor reproduce art_1")).toBeVisible();
  });

  // ── EXPORT ─────────────────────────────────────────────────────────────
  //
  // This screen could render an artifact and not give it to anyone: no
  // download, and no `publish` anywhere in the client at all, so the only way
  // out of the hosted face was a shell.

  const ARTIFACT = {
    created: "2026-01-01",
    artifact_id: "art_1",
    suite: { id: "s1", name: "Refund Suite" },
    bundle: { trials: [{ status: "completed" }], aggregates: [] },
  };

  test("the artifact and its reproduction package download", async ({ page }) => {
    await page.route("**/artifacts/art_1", json(200, ARTIFACT));
    await page.route("**/artifacts/art_1/download", json(200, ARTIFACT));
    await page.route("**/artifacts/art_1/package",
      json(200, { bundle: ARTIFACT.bundle, traces: [] }));
    await page.goto("/#/artifacts/art_1");

    for (const [label, name] of [
      ["Download artifact", "art_1.json"],
      ["Download reproduction package", "art_1-package.json"],
    ]) {
      const saved = page.waitForEvent("download");
      await page.getByRole("button", { name: label }).click();
      expect((await saved).suggestedFilename()).toBe(name);
    }
  });

  test("the run downloads in the shapes a paper needs", async ({ page }) => {
    // The door that was missing entirely: every other export here is JSON, and
    // nobody pastes a bundle into a results section.
    await page.route("**/artifacts/art_1", json(200, ARTIFACT));
    const asked: string[] = [];
    await page.route("**/artifacts/art_1/report*", async (route) => {
      const format = new URL(route.request().url()).searchParams.get("format") ?? "";
      asked.push(format);
      return route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: `rendered ${format}`,
      });
    });
    await page.goto("/#/artifacts/art_1");
    for (const [label, name, format] of [
      ["Results + Methods (.md)", "art_1-report.md", "md"],
      ["Table (.tex)", "art_1-report.tex", "tex"],
      ["Citation (.bib)", "art_1-report.bib", "bib"],
    ]) {
      const saved = page.waitForEvent("download");
      await page.getByRole("button", { name: label }).click();
      expect((await saved).suggestedFilename()).toBe(name);
      expect(asked).toContain(format);
    }
    // and the screen says the thing no author would think to check
    await expect(page.getByText(/DERIVED from the traces or merely/)).toBeVisible();
  });

  test("publishing locally says what it did NOT claim", async ({ page }) => {
    // a local mint proves replay and deliberately does not assert the
    // aggregates — only a server that recomputes them from the traces may
    const posted: Record<string, unknown>[] = [];
    await page.route("**/artifacts/art_1", json(200, ARTIFACT));
    await page.route("**/artifacts/art_1/publish", async (route) => {
      posted.push(route.request().postDataJSON() as Record<string, unknown>);
      return json(201, {
        publication_id: "e_abc123",
        origin: "local",
        aggregates_not_claimed: 6,
        publication: { claims: [{ kind: "exactly_replayable" }] },
      })(route);
    });
    await page.goto("/#/artifacts/art_1");

    await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();
    await page.getByLabel("Question it answers").fill("Does governance contain it?");
    await page.getByLabel("Visibility").selectOption("public");
    await page.getByRole("button", { name: "Publish" }).click();

    await expect(page.getByText("e_abc123")).toBeVisible();
    await expect(page.getByText(/6 aggregate\(s\) not published as claims/)).toBeVisible();
    expect(posted[0]).toMatchObject({
      question: "Does governance contain it?", visibility: "public",
    });
    expect(posted[0]).not.toHaveProperty("server");
  });

  test("a server publish carries its acceptance receipt", async ({ page }) => {
    await page.route("**/artifacts/art_1", json(200, ARTIFACT));
    await page.route("**/artifacts/art_1/publish", json(201, {
      publication_id: "e_srv",
      origin: "server",
      url: "https://lab.example/e/e_srv",
      acceptance: { server_id: "lab.example", integrity: "hash_verified" },
      acceptance_is_signed: true,
    }));
    await page.goto("/#/artifacts/art_1");
    await page.getByLabel("Question it answers").fill("q");
    await page.getByLabel("Server (optional)").fill("https://lab.example");
    await page.getByRole("button", { name: "Publish" }).click();

    await expect(page.getByText("e_srv")).toBeVisible();
    await expect(page.getByText("Acceptance receipt (signed)")).toBeVisible();
    await expect(page.getByRole("link", { name: "open" })).toHaveAttribute(
      "href", "https://lab.example/e/e_srv");
  });

  test("a publication says WHICH tier its statistics landed in", async ({ page }) => {
    // A server recomputes what it can derive from the traces and re-applies the
    // estimator to the rest. A latency mean is published and claims nothing —
    // and a reader who is not told that reads it as recomputed.
    await page.route("**/artifacts/art_1", json(200, ARTIFACT));
    await page.route("**/artifacts/art_1/publish", json(201, {
      publication_id: "e_reported",
      origin: "server",
      statistics_integrity: "self_reported",
      acceptance: { server_id: "lab.example" },
      acceptance_is_signed: false,
    }));
    await page.goto("/#/artifacts/art_1");
    await page.getByLabel("Question it answers").fill("How slow is it?");
    await page.getByLabel("Server (optional)").fill("https://lab.example");
    await page.getByRole("button", { name: "Publish" }).click();
    await expect(page.locator(".tag", { hasText: /^self_reported$/ })).toBeVisible();
    // and the screen must SAY what the weaker tier checked, before the click
    await expect(page.getByText(/the arithmetic is checked/)).toBeVisible();
  });

  test("a refused publish shows the reason", async ({ page }) => {
    await page.route("**/artifacts/art_1", json(200, ARTIFACT));
    await page.route("**/artifacts/art_1/publish", json(422, {
      error: "this artifact's recorded verdicts do not recompute",
    }));
    await page.goto("/#/artifacts/art_1");
    await page.getByLabel("Question it answers").fill("q");
    await page.getByRole("button", { name: "Publish" }).click();
    await expect(page.locator(".errors")).toContainText("do not recompute");
  });

  test("the list shows what this workspace published", async ({ page }) => {
    await page.route("**/artifacts", json(200, { artifacts: [] }));
    await page.route("**/publications", json(200, {
      publications: [
        { publication_id: "e_one", question: "Does it hold?", origin: "server",
          visibility: "public", claims: 3, created: "2026-03-03",
          statistics_integrity: "recomputed_from_traces" },
      ],
    }));
    await page.goto("/#/artifacts");
    await expect(page.getByRole("heading", { name: "Published" })).toBeVisible();
    await expect(page.getByText("e_one")).toBeVisible();
    await expect(page.getByText("Does it hold?")).toBeVisible();
    await expect(page.getByText(/3 claim\(s\)/)).toBeVisible();
    await expect(page.getByText(/recomputed_from_traces/)).toBeVisible();
  });

  test("no publications, no section", async ({ page }) => {
    await page.route("**/artifacts", json(200, { artifacts: [] }));
    await page.route("**/publications", json(200, { publications: [] }));
    await page.goto("/#/artifacts");
    await expect(page.getByRole("heading", { name: "Artifacts" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Published" })).toHaveCount(0);
  });
});

// ── PLAYGROUND ───────────────────────────────────────────────────────────────

test.describe("Playground", () => {
  test("fills the form and runs a single trial via POST /playground/trial", async ({ page }) => {
    // Playground fetches the suite catalog on mount.
    await page.route(
      "**/suites",
      json(200, {
        suites: [
          { id: "s1", name: "Refund Policy Suite", available: true },
          { id: "s2", name: "Escalation Suite", available: true },
        ],
      }),
    );

    const result = {
      mode: "trial",
      trial: { trial_id: "t1", status: "completed", metrics: { score: 1 } },
      trace: null,
      counted_in_a_run: false,
      evidence_cases: [],
    };
    await page.route("**/playground/trial", json(200, result));

    await page.goto("/#/playground");
    await expect(page.getByRole("heading", { name: "Playground" })).toBeVisible();

    // fill the form
    await page.getByLabel("Suite").selectOption("s2");
    await page.getByLabel("Scenario (optional)").fill("refund-01");
    await page.getByLabel("Seed (optional)").fill("s000");

    const request = page.waitForRequest(
      (req) => req.url().includes("/playground/trial") && req.method() === "POST",
    );
    await page.getByRole("button", { name: "Run one trial" }).click();
    await request;

    // the trial result renders
    await expect(page.getByRole("heading", { name: "Trial" })).toBeVisible();
    await expect(page.getByText("not counted in a run")).toBeVisible();
    await expect(page.getByText('"trial_id": "t1"')).toBeVisible();
  });
});

// ── INTEGRATIONS ─────────────────────────────────────────────────────────────

test.describe("Integrations", () => {
  test("lists connected runtimes from GET /runtimes", async ({ page }) => {
    await page.route(
      "**/runtimes",
      json(200, {
        runtimes: [
          {
            runtime_ref: "rt_1",
            runtime_label: "support-bot v3",
            agent_ref: "agent-1",
            status: "connected",
          },
        ],
      }),
    );
    await page.goto("/#/integrations");

    await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();
    await expect(page.getByText("rt_1")).toBeVisible();
    await expect(page.getByText("support-bot v3")).toBeVisible();
    await expect(page.getByText("connected", { exact: true })).toBeVisible();
  });

  test("connecting a runtime posts to /runtimes/connect and shows the issued key note", async ({
    page,
  }) => {
    await page.route("**/runtimes", json(200, { runtimes: [] }));
    await page.route(
      "**/runtimes/connect",
      json(200, { runtime_ref: "rt_new", ingest_key: "ik_secret_123" }),
    );

    await page.goto("/#/integrations");
    await expect(page.getByText("No runtime connected.")).toBeVisible();

    // the Connect button is disabled until a label is entered
    const connect = page.getByRole("button", { name: "Connect a runtime" });
    await expect(connect).toBeDisabled();

    await page.getByLabel("Runtime label").fill("support-bot v3");
    await expect(connect).toBeEnabled();

    const request = page.waitForRequest(
      (req) => req.url().includes("/runtimes/connect") && req.method() === "POST",
    );
    await connect.click();
    await request;

    // the one-time ingest-key note appears
    await expect(page.getByText(/Ingest key issued/)).toBeVisible();
  });
});
