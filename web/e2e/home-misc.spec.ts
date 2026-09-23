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
    // the launch list reads /suites (saved suites layered over the built-ins)
    await page.route("**/suites", json(200, { suites: HOME.suites }));
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

  test("a server's relative link opens on THAT server, with its write token sent", async ({
    page,
  }) => {
    // the publish server answers `/e/{id}` — a path on itself. Taken as-is it
    // resolved against the Lab's origin and 404'd.
    const posted: Record<string, unknown>[] = [];
    await page.route("**/artifacts/art_1", json(200, ARTIFACT));
    await page.route("**/artifacts/art_1/publish", async (route) => {
      posted.push(route.request().postDataJSON() as Record<string, unknown>);
      return json(201, { publication_id: "e_rel", origin: "server", url: "/e/e_rel" })(route);
    });
    await page.goto("/#/artifacts/art_1");
    await page.getByLabel("Question it answers").fill("q");
    await page.getByLabel("Server (optional)").fill("https://pub.example:8443");
    await page.getByLabel("Server write token (optional)").fill("w-token");
    await page.getByRole("button", { name: "Publish" }).click();
    await expect(page.getByRole("link", { name: "open" })).toHaveAttribute(
      "href", "https://pub.example:8443/e/e_rel");
    expect(posted[0]).toMatchObject({ server: "https://pub.example:8443", token: "w-token" });
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

    // the key ITSELF is shown — once — with a way to copy it. The screen used
    // to say "issued, shown once" and never render the value
    const key = page.getByTestId("ingest-key");
    await expect(key.getByText("ik_secret_123", { exact: true })).toBeVisible();
    await expect(key.getByRole("button", { name: "Copy" })).toBeVisible();
    await expect(key.getByText(/Shown once/)).toBeVisible();
  });

  test("a failed connect says why, and the button cannot double-mint", async ({ page }) => {
    await page.route("**/runtimes", json(200, { runtimes: [] }));
    let posts = 0;
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/runtimes/connect", async (route) => {
      posts += 1;
      await gate;
      return json(402, { error: "max_runtimes reached" })(route);
    });
    await page.goto("/#/integrations");
    await page.getByLabel("Runtime label").fill("bot");
    await page.getByRole("button", { name: "Connect a runtime" }).click();
    // in flight: disabled, so a second click cannot mint a second key
    await expect(page.getByRole("button", { name: "Connecting…" })).toBeDisabled();
    release();
    await expect(page.getByText("max_runtimes reached")).toBeVisible();
    expect(posts).toBe(1);
  });

  test("a hosted runtime is listed once, in the hosted section", async ({ page }) => {
    await page.route("**/runtimes", json(200, {
      runtimes: [
        { runtime_ref: "rt_mine", runtime_label: "mine" },
        { runtime_ref: "rt_pool", runtime_label: "pool", hosted: true },
      ],
    }));
    await page.route("**/hosted-runtimes", json(200, {
      hosted_runtimes: [{ runtime_ref: "rt_pool", runtime_label: "pool", hosted: true }],
    }));
    await page.goto("/#/integrations");
    await expect(page.getByText("rt_mine")).toHaveCount(1);
    await expect(page.getByText("rt_pool")).toHaveCount(1);
  });

  test("a provisioned hosted runtime shows its key", async ({ page }) => {
    await page.route("**/runtimes", json(200, { runtimes: [] }));
    await page.route("**/hosted-runtimes", async (route) =>
      route.request().method() === "POST"
        ? json(201, { runtime_ref: "rt_h", ingest_key: "ik_hosted_9" })(route)
        : json(200, { hosted_runtimes: [] })(route));
    await page.goto("/#/integrations");
    await page.getByLabel("Pool label").fill("eu-pool-1");
    await page.getByRole("button", { name: "Provision a hosted runtime" }).click();
    await expect(
      page.getByTestId("hosted-ingest-key").getByText("ik_hosted_9", { exact: true }),
    ).toBeVisible();
  });
});

// ── SCENARIOS ────────────────────────────────────────────────────────────────

test.describe("Scenarios", () => {
  test("list, save, open, share and delete", async ({ page }) => {
    const saved: Record<string, unknown>[] = [];
    let names = [{ name: "existing", task: "Old task." }];
    await page.route("**/scenarios", async (route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as { scenario: { name: string } };
        saved.push(body);
        names = [...names, { name: body.scenario.name, task: "Record a note." }];
        return json(201, { name: body.scenario.name })(route);
      }
      return json(200, { scenarios: names })(route);
    });
    await page.route("**/scenarios/validate", json(200, { ok: true, errors: [] }));
    await page.route("**/scenarios/my-scenario", async (route) => {
      if (route.request().method() === "DELETE") {
        names = names.filter((n) => n.name !== "my-scenario");
        return json(200, { name: "my-scenario", deleted: true })(route);
      }
      return json(200, { name: "my-scenario", task: "Record a note." })(route);
    });
    await page.route("**/scenarios/my-scenario/publish", json(201, {
      name: "my-scenario", org: "acme",
    }));

    await page.goto("/");
    await page.getByRole("link", { name: "Scenarios", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Scenarios" })).toBeVisible();
    await expect(page.getByRole("link", { name: "existing" })).toBeVisible();

    await page.getByRole("button", { name: "Start from a template" }).click();
    await page.getByRole("button", { name: "Validate" }).click();
    await expect(page.locator(".tag", { hasText: "valid" })).toBeVisible();
    await page.getByRole("button", { name: "Save scenario" }).click();

    // saved WITH its manifests, and opened
    await expect(page.getByRole("heading", { name: "Scenario my-scenario" })).toBeVisible();
    expect(saved[0]).toHaveProperty("manifests.note");
    // the nav keeps the section lit on a detail page
    await expect(page.getByRole("link", { name: "Scenarios", exact: true })).toHaveClass(
      /active/);

    await page.getByRole("button", { name: "Share with the org" }).click();
    await expect(page.getByText(/with org/)).toContainText("acme");

    await page.getByRole("button", { name: "Delete this workspace's copy" }).click();
    await expect(page.getByRole("heading", { name: "Scenarios" })).toBeVisible();
    await expect(page.getByRole("link", { name: "my-scenario" })).toHaveCount(0);
  });

  test("a save the server refuses says why and stays on the form", async ({ page }) => {
    await page.route("**/scenarios", async (route) =>
      route.request().method() === "POST"
        ? json(422, { error: "tool 'note' has no manifest" })(route)
        : json(200, { scenarios: [] })(route));
    await page.goto("/#/scenarios");
    await page.getByLabel("Scenario JSON").fill('{"scenario": {"name": "x"}}');
    await page.getByRole("button", { name: "Save scenario" }).click();
    await expect(page.getByText("tool 'note' has no manifest")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Scenarios" })).toBeVisible();
  });
});

// ── ORG REGISTRY + PUBLICATIONS ──────────────────────────────────────────────

test.describe("read-only detail routes", () => {
  test("an org-registry suite opens read-only", async ({ page }) => {
    await page.route("**/registry/suites/shared", json(200, {
      id: "shared", name: "Shared Suite", description: "From a colleague",
      scenarios: [{ name: "s1" }],
    }));
    await page.goto("/#/registry/suites/shared");
    await expect(page.getByRole("heading", { name: "Shared Suite" })).toBeVisible();
    await expect(page.getByText("org registry")).toBeVisible();
    await expect(page.getByRole("link", { name: "Suites", exact: true })).toHaveClass(/active/);
  });

  test("a publication card opens its document", async ({ page }) => {
    await page.route("**/artifacts", json(200, { artifacts: [] }));
    await page.route("**/publications", json(200, {
      publications: [{ publication_id: "e_one", question: "Does it hold?", claims: 1 }],
    }));
    await page.route("**/publications/e_one", json(200, {
      publication_id: "e_one", question: "Does it hold?", origin: "local",
      claims: [{ kind: "exactly_replayable", text: "DENY on trace t1", support_ref: "t1" }],
      limitations: ["one seed"],
    }));
    await page.goto("/#/artifacts");
    await page.getByRole("link", { name: "e_one" }).click();
    await expect(page.getByRole("heading", { name: "Publication e_one" })).toBeVisible();
    await expect(page.getByText("DENY on trace t1", { exact: true })).toBeVisible();
    await expect(page.getByText("one seed", { exact: true })).toBeVisible();
  });
});
