import { expect, test } from "@playwright/test";
import { json, stubScreens, stubShell } from "./helpers";

/**
 * The Control Plane handoff, driven from the web.
 *
 * Before the service layer this screen could not exist: producing or checking a
 * handoff lived inside an argparse handler, so it required a shell. These tests
 * cover the part a user touches — that the buttons reach the endpoints, and
 * that the three guarantees stay visibly SEPARATE rather than collapsing into
 * one green tick.
 */
const RUN = { run_id: "r_demo", state: "completed" };

const PACKAGE = {
  files: { "cp-deploy.json": "{}", "manifest.json": "{}" },
  manifest: {},
  config: {},
  signed: false,
  condition_id: "governed",
  baseline_condition_id: "baseline",
  regressions_carried: 2,
  earned_bridge: true,
};

async function openHandoff(page: import("@playwright/test").Page) {
  await stubShell(page, { auth_required: false, guest: false });
  await stubScreens(page);
  // EVERY run, from /runs — not Home's recent_runs, which is the last five
  await page.route("**/runs", json(200, { runs: [{ ...RUN, planned: 1, completed: 1 }] }));
  await page.goto("/");
  await page.getByRole("link", { name: "Handoff", exact: true }).click();
}

test.describe("Control Plane handoff", () => {
  test("exports from a run and shows what carries over", async ({ page }) => {
    await openHandoff(page);
    await page.route("**/handoff/export", json(200, PACKAGE));
    await page.getByRole("button", { name: "Export handoff" }).click();
    await expect(page.getByText("governed", { exact: true })).toBeVisible();
    // an unsigned package must SAY it proves nothing about who built it
    await expect(page.getByText(/Unsigned/)).toBeVisible();
    await expect(page.getByText(/Earned bridge/)).toBeVisible();
  });

  test("verification reports each guarantee separately", async ({ page }) => {
    await openHandoff(page);
    await page.route("**/handoff/export", json(200, PACKAGE));
    await page.route("**/handoff/verify", json(200, {
      outcome: "unverified",
      failed: ["authenticity"],
      checks: [
        { name: "integrity", status: "ok", message: "manifest INTEGRITY OK (2 files)" },
        { name: "semantic_refs", status: "ok", message: "manifest semantic refs OK" },
        { name: "authenticity", status: "unverified", message: "UNVERIFIED — unsigned manifest" },
        { name: "derivability", status: "ok", message: "RECOMPUTED OK" },
      ],
    }));
    await page.getByRole("button", { name: "Export handoff" }).click();
    await page.getByRole("button", { name: "Verify this handoff" }).click();
    // integrity passed and authenticity did NOT — the screen must not merge them
    await expect(page.getByText("integrity", { exact: true })).toBeVisible();
    await expect(page.getByText("authenticity", { exact: true })).toBeVisible();
    await expect(page.getByText("unverified").first()).toBeVisible();
  });

  test("the handoff downloads as the directory it is", async ({ page }) => {
    // The CLI writes a tree and `verify-cp-export` checks a tree. The web used
    // to hand over the same bytes as one nested JSON of 160 files — a shape
    // only one of the two faces could verify.
    await openHandoff(page);
    let asked: Record<string, unknown> | null = null;
    await page.route("**/handoff/export", async (route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      if (body.format !== "zip") return json(200, PACKAGE)(route);
      asked = body;
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        headers: { "Content-Disposition": 'attachment; filename="governed-handoff.zip"' },
        body: Buffer.from("PK\u0005\u0006" + "\u0000".repeat(18), "binary"),
      });
    });
    await page.getByRole("button", { name: "Export handoff" }).click();

    const saved = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download handoff (.zip)" }).click();
    expect((await saved).suggestedFilename()).toBe("r_demo-handoff.zip");
    expect(asked).toMatchObject({ run_id: "r_demo", format: "zip" });
  });

  test("the file map is offered as what it is", async ({ page }) => {
    await openHandoff(page);
    await page.route("**/handoff/export", json(200, PACKAGE));
    await page.getByRole("button", { name: "Export handoff" }).click();
    const saved = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download file map (JSON)" }).click();
    expect((await saved).suggestedFilename()).toBe("r_demo-handoff.json");
  });

  test("an export the evidence does not earn says so", async ({ page }) => {
    await openHandoff(page);
    await page.route("**/handoff/export", json(409, { error: "no enforcing condition" }));
    await page.getByRole("button", { name: "Export handoff" }).click();
    await expect(page.getByText(/no enforcing condition/)).toBeVisible();
  });

  test("an old run can be handed off: the list is every run, not the last five", async ({
    page,
  }) => {
    await stubShell(page, { auth_required: false, guest: false });
    await stubScreens(page);
    const runs = Array.from({ length: 7 }, (_, i) => ({
      run_id: `r_${i}`, state: "completed", planned: 1, completed: 1,
    }));
    await page.route("**/runs", json(200, { runs }));
    await page.goto("/#/handoff");
    await expect(page.getByText("r_6", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Export handoff" })).toHaveCount(7);
  });

  test("an incident import shows what came back, since nothing is stored", async ({ page }) => {
    await openHandoff(page);
    await page.route("**/incidents/import", json(201, {
      bundle_id: "b_inc", trace_id: "t_inc", replay_status: "match",
      bundle: { trials: [{}, {}] },
      traces: [{}],
      files: { "bundle.json": "{}", "traces/t_inc.json": "{}" },
    }));
    await page.getByLabel("Incident JSON").fill(
      '{"trace": {}, "scenario": {}, "manifests": {}, "condition": {}}');
    await page.getByRole("button", { name: "Import incident" }).click();
    const result = page.getByTestId("incident-result");
    await expect(result.getByText("b_inc")).toBeVisible();
    await expect(result.getByText("t_inc", { exact: true })).toBeVisible();
    await expect(result.getByText("traces/t_inc.json")).toBeVisible();
    await expect(result.getByRole("button", { name: /Download bundle/ })).toBeVisible();
  });

  test("a bare package is accepted only when the reader says so", async ({ page }) => {
    await openHandoff(page);
    const posted: Record<string, unknown>[] = [];
    await page.route("**/verify/package", async (route) => {
      posted.push(route.request().postDataJSON() as Record<string, unknown>);
      return json(200, { outcome: "ok", checks: [], failed: [] })(route);
    });
    await page.getByLabel("Package JSON").fill('{"bundle": {}, "traces": {}}');
    await page.getByRole("button", { name: "Verify package" }).click();
    await expect.poll(() => posted.length).toBe(1);
    // OFF by default — it used to be sent as true on every click
    expect(posted[0]).toMatchObject({ allow_bare: false });
    await page.getByLabel("Accept a bare package").check();
    await page.getByRole("button", { name: "Verify package" }).click();
    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toMatchObject({ allow_bare: true });
  });
});
