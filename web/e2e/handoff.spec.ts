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
  await page.route("**/home", json(200, { ...{
    onboarding_step: "", quick_actions: [], suites: [], counts: {},
  }, recent_runs: [RUN] }));
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

  test("an export the evidence does not earn says so", async ({ page }) => {
    await openHandoff(page);
    await page.route("**/handoff/export", json(409, { error: "no enforcing condition" }));
    await page.getByRole("button", { name: "Export handoff" }).click();
    await expect(page.getByText(/no enforcing condition/)).toBeVisible();
  });
});
