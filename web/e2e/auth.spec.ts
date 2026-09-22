import { expect, Page, test } from "@playwright/test";
import { json, SESSION, stubShell } from "./helpers";

async function gotoSecured(page: Page, guest = false): Promise<void> {
  await stubShell(page, { auth_required: true, guest });
  await page.goto("/");
}

async function fillLogin(page: Page, email = "ada@acme.io", pw = "correct horse"): Promise<void> {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(pw);
}

test.describe("the login gate", () => {
  test("a secured server shows the login screen", async ({ page }) => {
    await gotoSecured(page);
    await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
  });

  test("logging in enters the app", async ({ page }) => {
    await gotoSecured(page);
    await page.route("**/identity/v1/login", json(200, SESSION));
    await fillLogin(page);
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Log in" })).toHaveCount(0);
  });

  test("a wrong password shows an error and stays on login", async ({ page }) => {
    await gotoSecured(page);
    await page.route(
      "**/identity/v1/login",
      json(401, { detail: "invalid email or password" }),
    );
    await fillLogin(page, "ada@acme.io", "nope");
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByText("invalid email or password")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  });

  test("signing up creates a workspace and enters the app", async ({ page }) => {
    await gotoSecured(page);
    await page.route("**/identity/v1/signup", json(201, SESSION));
    await page.getByRole("link", { name: "Create a workspace" }).click();
    await expect(page.getByLabel("Organization name")).toBeVisible();
    await fillLogin(page);
    await page.getByLabel("Organization name").fill("Acme");
    await page.getByRole("button", { name: "Create workspace" }).click();
    await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
  });

  test("a guest can enter without an account when offered", async ({ page }) => {
    await gotoSecured(page, true);
    await page.route(
      "**/guest-session",
      json(201, { token: "guest-tok", workspace_id: "guest_1", expires_at: 9e9 }),
    );
    const guestBtn = page.getByRole("button", { name: "Try without an account" });
    await expect(guestBtn).toBeVisible();
    await guestBtn.click();
    await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
  });

  test("no guest button when the server does not offer it", async ({ page }) => {
    await gotoSecured(page, false);
    await expect(page.getByRole("button", { name: "Try without an account" })).toHaveCount(0);
  });

  test("an operator can paste a control token", async ({ page }) => {
    await gotoSecured(page);
    await page.getByRole("link", { name: "Use a control token instead" }).click();
    await page.getByLabel("Control token").fill("static-op-token");
    await page.getByRole("button", { name: "Use token" }).click();
    await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
  });

  test("logging out returns to the login screen", async ({ page }) => {
    await gotoSecured(page);
    await page.route("**/identity/v1/login", json(200, SESSION));
    await fillLogin(page);
    await page.getByRole("button", { name: "Log in" }).click();
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  });

  test("an expired guest session drops back to login", async ({ page }) => {
    await stubShell(page, { auth_required: true, guest: true });
    // the guest enters, but the very next screen fetch is unauthorized (expired)
    // and there is no refresh token — the app must return to the login screen
    await page.route(
      "**/guest-session",
      json(201, { token: "guest-tok", workspace_id: "guest_1", expires_at: 9e9 }),
    );
    await page.route("**/home", json(401, { error: "control token required" }));
    await page.route("**/identity/v1/refresh", json(401, { detail: "invalid refresh token" }));
    await page.goto("/");
    await page.getByRole("button", { name: "Try without an account" }).click();
    await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  });
});

/**
 * 402 is not a failure of the screen. It is an ANSWER: the request was
 * well-formed and authorised, and the plan does not include it. It is also the
 * only error in the app a user can FIX — and it rendered as a red sentence like
 * any other, with nothing saying where to go. `grep -rn 402 web/src` came back
 * empty across the whole client.
 */
test.describe("a plan refusal", () => {
  test("says it is the plan, and where the plans are", async ({ page }) => {
    await stubShell(page, { auth_required: false, guest: false });
    await page.route("**/runtimes", json(402, {
      error: "capability 'hosted_execution' is not in plan 'Free'",
    }));
    await page.goto("/#/integrations");

    await expect(page.getByText("Your plan does not include this.")).toBeVisible();
    await expect(page.getByText(/hosted_execution/)).toBeVisible();
    await expect(page.getByRole("link", { name: "Workspace" }).last())
      .toHaveAttribute("href", "#/workspace");
  });

  test("an ordinary failure is still an ordinary failure", async ({ page }) => {
    // the guard on the guard: a 500 must not start advertising plans
    await stubShell(page, { auth_required: false, guest: false });
    await page.route("**/runtimes", json(500, { error: "boom" }));
    await page.goto("/#/integrations");
    await expect(page.getByText("Could not load this screen.")).toBeVisible();
    await expect(page.getByText("Your plan does not include this.")).toHaveCount(0);
  });
});

/**
 * The plan catalog is a map of id -> plan, and `/billing/plans` used to send the
 * VALUES — so the id a client must quote back to /billing/checkout never left
 * the server and the screen fell back to the display name. Against any catalog
 * whose name differs from its id, Subscribe answered "unknown plan 'Team'".
 */
test.describe("choosing a plan", () => {
  const PLANS = [
    { plan_id: "free", name: "Free", price_usd: 0, max_suites: 3,
      max_artifacts: 10, max_hosted_runtimes: 0, capabilities: [] },
    { plan_id: "team_2026", name: "Team", price_usd: 99, max_suites: 25,
      max_artifacts: 200, max_hosted_runtimes: 2, capabilities: ["private_registry"] },
  ];

  async function openWorkspace(page: Page, posted: Record<string, unknown>[]) {
    await stubShell(page, { auth_required: false, guest: false });
    await page.route("**/workspaces/current", json(200, {
      id: "ws1", name: "Acme", role: "owner", is_admin: false, org: null,
      plan: PLANS[0], subscription: { plan_id: "free", status: "active" },
      created_at: 1_780_000_000,
    }));
    await page.route("**/billing/plans", json(200, { plans: PLANS }));
    await page.route("**/workspaces/current/members", json(200, { members: [] }));
    await page.route("**/workspaces", json(403, { error: "admin only" }));
    await page.route("**/billing/checkout", async (route) => {
      posted.push(route.request().postDataJSON() as Record<string, unknown>);
      return json(201, { session_id: "cs_1", checkout_url: "https://pay.example/cs_1",
                         plan_id: "team_2026" })(route);
    });
    await page.goto("/#/workspace");
  }

  test("Subscribe sends the plan's ID, not its display name", async ({ page }) => {
    const posted: Record<string, unknown>[] = [];
    await openWorkspace(page, posted);
    await expect(page.getByRole("heading", { name: "Team" })).toBeVisible();
    await page.getByRole("button", { name: "Subscribe" }).first().click();
    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0]).toEqual({ plan_id: "team_2026" });
  });

  test("the current plan is marked by id, so a real catalog marks one", async ({ page }) => {
    await openWorkspace(page, []);
    const free = page.locator(".card", { has: page.getByRole("heading", { name: "Free" }) });
    await expect(free.locator(".tag", { hasText: "current" })).toBeVisible();
    // and the plan you are on offers no Subscribe button
    await expect(free.getByRole("button", { name: "Subscribe" })).toHaveCount(0);
  });
});
