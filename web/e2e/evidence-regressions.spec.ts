import { expect, test } from "@playwright/test";
import { json, stubShell } from "./helpers";

// The EVIDENCE and REGRESSIONS screens, driven against per-test page.route
// stubs. Every test opens in OPEN mode (auth_required:false) so the shell
// renders with no login gate, then mocks only the endpoint under test. The
// payload shapes mirror the api.ts contract exactly — a wrong-shaped mock
// crashes the screen and blanks the app, so the fields asserted below are the
// ones the screen source actually reads.

const OPEN = { auth_required: false, guest: false };

// Surface any React render crash from a mis-shaped payload as a test failure
// instead of a silent blank page.
test.beforeEach(async ({ page }) => {
  page.on("pageerror", (err) => {
    // eslint-disable-next-line no-console
    console.error("pageerror:", err.message);
  });
});

test.describe("Evidence", () => {
  test("list renders a row per EvidenceCase from GET /evidence", async ({ page }) => {
    await stubShell(page, OPEN);
    await page.route(
      "**/evidence",
      json(200, {
        evidence_cases: [
          { id: "ev_1", kind: "latency", title: "Latency spike on step 3", severity: "high" },
          { id: "ev_2", kind: "budget", title: "Budget overflow in planner" },
        ],
      }),
    );
    await page.goto("/#/evidence");

    await expect(page.getByRole("heading", { name: "Evidence", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Latency spike on step 3" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Budget overflow in planner" })).toBeVisible();
    // severity + kind tags render as text
    await expect(page.getByText("high", { exact: true })).toBeVisible();
    await expect(page.getByText("latency", { exact: true })).toBeVisible();
  });

  test("empty list shows the empty state", async ({ page }) => {
    await stubShell(page, OPEN);
    await page.route("**/evidence", json(200, { evidence_cases: [] }));
    await page.goto("/#/evidence");

    await expect(page.getByRole("heading", { name: "Evidence", exact: true })).toBeVisible();
    await expect(page.getByText("No cases yet. Curate one from a trial.")).toBeVisible();
  });

  test("detail renders the case from GET /evidence/:id", async ({ page }) => {
    await stubShell(page, OPEN);
    await page.route(
      "**/evidence/ev_1",
      json(200, {
        id: "ev_1",
        kind: "latency",
        title: "Latency spike on step 3",
        severity: "high",
        summary: "The planner stalled waiting on a slow tool call.",
        metrics: { latency_ms: 8200 },
        timeline: [
          { seq: 1, actor: "planner", label: "issued tool call", highlight: true },
          { seq: 2, actor: "tool", label: "responded late", note: "8.2s" },
        ],
      }),
    );
    await page.goto("/#/evidence/ev_1");

    await expect(
      page.getByRole("heading", { name: "Latency spike on step 3" }),
    ).toBeVisible();
    await expect(
      page.getByText("The planner stalled waiting on a slow tool call."),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Metrics" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
    await expect(page.getByText("issued tool call")).toBeVisible();
  });
});

test.describe("Regressions", () => {
  test("list renders a row per regression from GET /regressions", async ({ page }) => {
    await stubShell(page, OPEN);
    await page.route(
      "**/regressions",
      json(200, {
        regressions: [
          { id: "reg_1", name: "p95 latency under 5s", expectation: "stays fast" },
          { id: "reg_2", name: "no budget overflow" },
        ],
      }),
    );
    await page.goto("/#/regressions");

    await expect(page.getByRole("heading", { name: "Regressions", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "p95 latency under 5s" })).toBeVisible();
    await expect(page.getByRole("link", { name: "no budget overflow" })).toBeVisible();
    await expect(page.getByText("reg_1")).toBeVisible();
  });

  test("empty list shows the empty state", async ({ page }) => {
    await stubShell(page, OPEN);
    await page.route("**/regressions", json(200, { regressions: [] }));
    await page.goto("/#/regressions");

    await expect(page.getByRole("heading", { name: "Regressions", exact: true })).toBeVisible();
    await expect(page.getByText("Nothing pinned yet.")).toBeVisible();
  });

  test("detail renders the invariant from GET /regressions/:id", async ({ page }) => {
    await stubShell(page, OPEN);
    await page.route(
      "**/regressions/reg_1",
      json(200, {
        id: "reg_1",
        name: "p95 latency under 5s",
        rule: { metric: "latency_ms", op: "lt", value: 5000 },
        expectation: "The suite stays within its latency budget.",
      }),
    );
    await page.goto("/#/regressions/reg_1");

    await expect(page.getByRole("heading", { name: "p95 latency under 5s" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Rule" })).toBeVisible();
    await expect(
      page.getByText("The suite stays within its latency budget."),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Check against a run" })).toBeVisible();
  });

  test("running the invariant shows the outcome from POST /regressions/:id/run", async ({ page }) => {
    await stubShell(page, OPEN);
    await page.route(
      "**/regressions/reg_1",
      json(200, {
        id: "reg_1",
        name: "p95 latency under 5s",
        rule: { metric: "latency_ms", op: "lt", value: 5000 },
      }),
    );
    // POST the run — must be a well-formed InvariantOutcome or the outcome
    // block crashes on outcome.failing_trial_ids.map.
    await page.route("**/regressions/reg_1/run", (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      return json(200, {
        regression_id: "reg_1",
        run_id: "run_42",
        status: "failed",
        detail: "2 of 10 trials breached the 5s latency budget.",
        trials_checked: 10,
        trials_failed: 2,
        failing_trial_ids: ["trial_a", "trial_b"],
      })(route);
    });
    await page.goto("/#/regressions/reg_1");

    await expect(page.getByRole("heading", { name: "p95 latency under 5s" })).toBeVisible();

    await page.getByLabel("Run id").fill("run_42");
    await page.getByRole("button", { name: "Run the invariant" }).click();

    // the outcome word (status), the detail prose, and the failing trial ids
    await expect(page.getByText("failed", { exact: true })).toBeVisible();
    await expect(
      page.getByText("2 of 10 trials breached the 5s latency budget."),
    ).toBeVisible();
    await expect(page.getByText("10 trial(s) checked, 2 failing")).toBeVisible();
    await expect(page.getByText("trial_a")).toBeVisible();
    await expect(page.getByText("trial_b")).toBeVisible();
  });
});
