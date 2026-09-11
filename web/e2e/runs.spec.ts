import { expect, Page, test } from "@playwright/test";
import { json, stubShell } from "./helpers";

// The RUNS screens: the runs list, the run report, and a single trial. Every
// backend endpoint is stubbed per test with the EXACT payload shape the screen
// reads (RunRow / RunReport / RunResults / TrialDetail from src/lib/api.ts) — a
// wrong shape crashes the screen and blanks the app. The app runs in OPEN mode
// (auth_required:false) so no login gate stands between goto and the screen.
//
// RunReport also opens an SSE stream to /runs/:id/events via useRunEvents; it is
// deliberately left unmocked — no events arrive, `progress` stays null, and the
// screen falls back to the loaded report state, which is what these tests read.

/** Surface any uncaught screen error instead of debugging a blank page. */
function trap(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

async function openMode(page: Page): Promise<void> {
  await stubShell(page, { auth_required: false, guest: false });
}

const NOW = Math.floor(Date.now() / 1000);

test.describe("runs list", () => {
  test("renders a row per run from GET /runs", async ({ page }) => {
    const errors = trap(page);
    await openMode(page);
    await page.route(
      "**/runs",
      json(200, {
        runs: [
          {
            run_id: "run_alpha",
            state: "completed",
            planned: 5,
            completed: 5,
            created_at: NOW - 600,
            updated_at: NOW - 90,
          },
          {
            run_id: "run_beta",
            state: "running",
            planned: 8,
            completed: 3,
            created_at: NOW - 120,
            updated_at: NOW - 30,
          },
          {
            run_id: "run_gamma",
            state: "awaiting_confirmation",
            planned: 4,
            completed: 0,
            created_at: NOW - 20,
            updated_at: NOW - 20,
          },
        ],
      }),
    );
    await page.goto("/#/runs");

    await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();

    // one navigable link per run
    await expect(page.getByRole("link", { name: "run_alpha", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "run_beta", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "run_gamma", exact: true })).toBeVisible();

    // completed/planned counts rendered from the payload
    await expect(page.getByText("5/5")).toBeVisible();
    await expect(page.getByText("3/8")).toBeVisible();
    await expect(page.getByText("0/4")).toBeVisible();

    // status badges reflect each run's `state` field verbatim
    await expect(page.getByText("completed", { exact: true })).toBeVisible();
    await expect(page.getByText("running", { exact: true })).toBeVisible();
    await expect(page.getByText("awaiting_confirmation", { exact: true })).toBeVisible();

    // the age helper turns updated_at into a relative "…ago"
    await expect(page.getByText(/ago/).first()).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("shows the empty state when there are no runs", async ({ page }) => {
    await openMode(page);
    await page.route("**/runs", json(200, { runs: [] }));
    await page.goto("/#/runs");

    await expect(page.getByRole("heading", { name: "Runs", exact: true })).toBeVisible();
    await expect(
      page.getByText(
        "No runs yet. A connected runtime claims an assignment and pushes traces back.",
      ),
    ).toBeVisible();
    // no run rows are rendered (nav links live outside the list)
    await expect(page.locator("ul.rows")).toHaveCount(0);
  });
});

// A run report and its results, keyed by run id. RunReport blocks on Loading
// until BOTH /report and /results resolve, so both are always stubbed.
const RUN_ID = "run_alpha";

async function stubReport(page: Page): Promise<void> {
  await page.route(
    `**/runs/${RUN_ID}/report`,
    json(200, {
      run_id: RUN_ID,
      state: "completed",
      planned_trials: 5,
      trials_by_status: { completed: 4, failed: 1 },
      coverage: { completed: 4, planned: 5 },
      metric_coverage: { latency_ms: 4, cost_usd: 3 },
      aggregates: [
        {
          metric: "latency_ms",
          condition_id: "control",
          estimate: 123.456,
          n: 4,
          interval: { method: "bootstrap", low: 100.1, high: 150.2 },
          test: { name: "welch", vs: "baseline", p: 0.032, status: "significant" },
        },
      ],
      estimate: {},
    }),
  );
  await page.route(
    `**/runs/${RUN_ID}/results`,
    json(200, {
      run_id: RUN_ID,
      state: "completed",
      planned_trials: ["t1", "t2", "t3", "t4", "t5"],
      trials: [
        { trial_id: "t1", status: "completed" },
        { trial_id: "t2", status: "failed" },
      ],
      aggregates: [],
    }),
  );
}

test.describe("run report", () => {
  test("renders coverage, metrics, aggregates and trials", async ({ page }) => {
    const errors = trap(page);
    await openMode(page);
    await stubReport(page);
    await page.goto(`/#/runs/${RUN_ID}`);

    await expect(page.getByRole("heading", { name: `Run ${RUN_ID}` })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Coverage" })).toBeVisible();

    // coverage: 5 planned, 4 completed -> one trial did not complete
    await expect(
      page.getByText(
        "1 planned trial(s) did not complete. Aggregates below are over the completed subset.",
      ),
    ).toBeVisible();
    // the failed count from trials_by_status surfaces as a coverage stat tile
    await expect(page.locator(".stat-label", { hasText: /^failed$/ })).toBeVisible();

    // metrics measured, each over the completed subset (4)
    await expect(page.getByRole("heading", { name: "Metrics measured" })).toBeVisible();
    // latency_ms also appears in the aggregates table below, so scope to the list
    await expect(page.locator("li", { hasText: "4/4 trial(s)" }).getByText("latency_ms")).toBeVisible();
    await expect(page.getByText("cost_usd", { exact: true })).toBeVisible();
    await expect(page.getByText("4/4 trial(s)")).toBeVisible();
    await expect(page.getByText("3/4 trial(s)")).toBeVisible();

    // aggregates table: values rendered (never computed) from the stored row
    await expect(page.getByRole("heading", { name: "Aggregates" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "control", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "123.456" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "bootstrap [100.100, 150.200]" })).toBeVisible();
    await expect(
      page.getByRole("cell", { name: "welch vs baseline: p=0.032 (significant)" }),
    ).toBeVisible();

    // trials list from GET /runs/:id/results
    await expect(page.getByRole("heading", { name: "Trials", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "t1", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "t2", exact: true })).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("clicking a run in the list opens its report", async ({ page }) => {
    const errors = trap(page);
    await openMode(page);
    await page.route(
      "**/runs",
      json(200, {
        runs: [
          {
            run_id: RUN_ID,
            state: "completed",
            planned: 5,
            completed: 4,
            updated_at: NOW - 45,
          },
        ],
      }),
    );
    await stubReport(page);

    await page.goto("/#/runs");
    await page.getByRole("link", { name: RUN_ID, exact: true }).click();

    await expect(page.getByRole("heading", { name: `Run ${RUN_ID}` })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Coverage" })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("empty metric coverage and no aggregates show their empty states", async ({ page }) => {
    const errors = trap(page);
    await openMode(page);
    await page.route(
      `**/runs/${RUN_ID}/report`,
      json(200, {
        run_id: RUN_ID,
        state: "completed",
        planned_trials: 3,
        trials_by_status: { completed: 3 },
        coverage: { completed: 3, planned: 3 },
        metric_coverage: {},
        aggregates: [],
        estimate: {},
      }),
    );
    await page.route(
      `**/runs/${RUN_ID}/results`,
      json(200, {
        run_id: RUN_ID,
        state: "completed",
        planned_trials: ["t1", "t2", "t3"],
        trials: [],
        aggregates: [],
      }),
    );
    await page.goto(`/#/runs/${RUN_ID}`);

    await expect(page.getByRole("heading", { name: `Run ${RUN_ID}` })).toBeVisible();
    await expect(page.getByText("No completed trial recorded a metric.")).toBeVisible();
    await expect(
      page.getByText("The backend stored no aggregate for this run.", { exact: false }),
    ).toBeVisible();
    // a fully-completed run shows no "did not complete" note
    await expect(page.getByText("did not complete")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
});

test.describe("trial screen", () => {
  test("renders metrics and a timeline from the trial trace", async ({ page }) => {
    const errors = trap(page);
    await openMode(page);
    await page.route(
      `**/runs/${RUN_ID}/trials/t1`,
      json(200, {
        run_id: RUN_ID,
        trial: {
          trial_id: "t1",
          status: "completed",
          scenario_id: "sc-1",
          condition_id: "control",
          metrics: { latency_ms: 120, passed: true },
        },
        trace: {
          events: [
            { seq: 1, type: "tool_call", tool: "search" },
            { seq: 2, type: "gate_decision", decision: { verdict: "ALLOW", gate: "policy" } },
          ],
          values: [{ label: "reward", amount: 1 }],
        },
      }),
    );
    await page.goto(`/#/runs/${RUN_ID}/trials/t1`);

    await expect(page.getByRole("heading", { name: "Trial", exact: true })).toBeVisible();
    // breadcrumb back to the run, plus the trial id
    await expect(page.getByRole("link", { name: RUN_ID, exact: true })).toBeVisible();
    await expect(page.getByText("t1", { exact: false })).toBeVisible();
    // status badge from trial.status
    await expect(page.getByText("completed", { exact: true })).toBeVisible();

    // metrics rendered as stat tiles
    await expect(page.getByRole("heading", { name: "Metrics", exact: true })).toBeVisible();
    await expect(page.getByText("latency_ms", { exact: true })).toBeVisible();
    await expect(page.getByText("120", { exact: true })).toBeVisible();
    await expect(page.getByText("passed", { exact: true })).toBeVisible();

    // timeline: event kinds derive from the event `type` (underscores -> spaces)
    await expect(page.getByRole("heading", { name: "Timeline", exact: true })).toBeVisible();
    await expect(page.getByText("tool call")).toBeVisible();
    await expect(page.getByText("gate decision")).toBeVisible();
    await expect(page.getByText("search", { exact: true })).toBeVisible();

    // value ledger only present when a trace arrived
    await expect(page.getByRole("heading", { name: "Value ledger", exact: true })).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("a failed trial with no trace shows the failure empty state", async ({ page }) => {
    const errors = trap(page);
    await openMode(page);
    await page.route(
      `**/runs/${RUN_ID}/trials/t2`,
      json(200, {
        run_id: RUN_ID,
        trial: {
          trial_id: "t2",
          status: "failed",
          failure_reason: "runtime timed out",
        },
        trace: null,
      }),
    );
    await page.goto(`/#/runs/${RUN_ID}/trials/t2`);

    await expect(page.getByRole("heading", { name: "Trial", exact: true })).toBeVisible();
    await expect(page.getByText("failed", { exact: true })).toBeVisible();
    await expect(
      page.getByText("This trial failed and produced no usable trace.", { exact: false }),
    ).toBeVisible();
    await expect(page.getByText("runtime timed out", { exact: false })).toBeVisible();
    // no trace -> no value ledger section
    await expect(page.getByRole("heading", { name: "Value ledger" })).toHaveCount(0);
    expect(errors).toEqual([]);
  });
});
