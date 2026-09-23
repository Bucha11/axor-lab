import { expect, test } from "@playwright/test";
import { json, stubShell } from "./helpers";

// Suite card actions. Delete asks first (window.confirm) — Playwright
// auto-DISMISSES dialogs, so each test answers the dialog explicitly.

const OPEN = { auth_required: false, guest: false };
const SUITE = {
  id: "suite-mine", name: "Mine", description: "a workspace suite",
  available: true, origin: "workspace", capabilities: [],
};

async function stubSuites(page: import("@playwright/test").Page) {
  await stubShell(page, OPEN);
  await page.route("**/registry/suites", json(403, { error: "private_registry not in plan" }));
}

test("deleting a suite asks first, and a dismissed dialog deletes nothing", async ({ page }) => {
  await stubSuites(page);
  const deletes: string[] = [];
  await page.route("**/suites", json(200, { suites: [SUITE] }));
  await page.route("**/suites/suite-mine", (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    deletes.push("DELETE");
    return json(200, { id: "suite-mine", deleted: true })(route);
  });
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });
  await page.goto("/#/suites");
  await page.getByRole("button", { name: "Delete" }).click();
  await expect.poll(() => dialogs.length).toBe(1);
  expect(dialogs[0]).toContain("suite-mine");
  expect(deletes).toEqual([]);
});

test("a refused delete is shown", async ({ page }) => {
  await stubSuites(page);
  await page.route("**/suites", json(200, { suites: [SUITE] }));
  await page.route("**/suites/suite-mine", (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    return json(403, { error: "deleting a suite requires editor" })(route);
  });
  page.on("dialog", (dialog) => void dialog.accept());
  await page.goto("/#/suites");
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("deleting a suite requires editor")).toBeVisible();
  // publish is not styled as a destructive action
  await expect(page.getByRole("button", { name: "Publish to org" })).toHaveClass("item-add");
});
