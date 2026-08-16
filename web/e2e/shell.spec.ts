import { expect, test } from "@playwright/test";
import { stubScreens, stubShell } from "./helpers";

// In OPEN (local) mode the app renders with no login. This walks the primary
// navigation and asserts each screen renders its heading against empty — but
// valid — payloads. It doubles as the open-mode-needs-no-registration story.
const SCREENS: { link: string; heading: string }[] = [
  { link: "Suites", heading: "Suites" },
  { link: "Runs", heading: "Runs" },
  { link: "Evidence", heading: "Evidence" },
  { link: "Regressions", heading: "Regressions" },
  { link: "Artifacts", heading: "Artifacts" },
  { link: "Playground", heading: "Playground" },
  { link: "Integrations", heading: "Integrations" },
];

test.describe("app shell in open mode", () => {
  test.beforeEach(async ({ page }) => {
    await stubShell(page, { auth_required: false, guest: false });
    await stubScreens(page);
    await page.goto("/");
  });

  test("renders with no login gate and no log-out", async ({ page }) => {
    await expect(page.getByRole("link", { name: "Suites" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Log in" })).toHaveCount(0);
    // open mode carries no credential, so there is nothing to log out of
    await expect(page.getByRole("button", { name: "Log out" })).toHaveCount(0);
  });

  for (const { link, heading } of SCREENS) {
    test(`navigates to ${link}`, async ({ page }) => {
      await page.getByRole("link", { name: link, exact: true }).click();
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    });
  }
});
