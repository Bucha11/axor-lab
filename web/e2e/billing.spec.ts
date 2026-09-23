// Buying and managing a plan from the Lab when the org is billed through
// axor-identity (the hosted service). The e2e stack has no identity service,
// so /identity/* is stubbed per test: this exercises the UI flow — the same one
// the Control Plane runs — not Paddle or identity itself.
import { expect, Page, test } from "@playwright/test";
import { json, SESSION, stubScreens, stubShell } from "./helpers";

const CONFIG = { enabled: true, environment: "sandbox", client_token: "ct", tiers: ["team", "security"] };

function plan(plan_id: string, name: string, price_usd: number) {
  return {
    plan_id, name, price_usd, price_period: "month",
    max_suites: null, max_artifacts: null, max_hosted_runtimes: 1, capabilities: [],
  };
}

const PLANS = {
  plans: [plan("free", "Community", 0), plan("team", "Team Workspace", 299),
          plan("security", "Security Workspace", 1500), plan("enterprise", "Enterprise", 30000)],
};

function orgWorkspace(planId: string) {
  return {
    id: "org_1", name: "Acme", org: "org_1", role: "owner", is_admin: false, created_at: 0,
    plan: { name: planId, max_suites: 3, max_artifacts: 10, max_hosted_runtimes: 0, capabilities: [] },
    subscription: { plan_id: planId, status: "active" },
  };
}

function status(tier: string, sub: object | null = null, canManage = false) {
  return {
    enabled: true, org_id: "org_1", tier, token_tier: tier, subscription: sub,
    is_admin: true, can_manage: canManage,
  };
}

async function setup(page: Page, planId = "free") {
  await stubShell(page, { auth_required: true, guest: false });
  await stubScreens(page);
  await page.route("**/workspaces/current", json(200, orgWorkspace(planId)));
  await page.route("**/billing/plans", json(200, PLANS));
  await page.route("**/identity/v1/billing/config", json(200, CONFIG));
  await page.route("**/identity/v1/login", json(200, SESSION));
  await page.route("**/identity/v1/signup", json(201, SESSION));
}

async function logIn(page: Page) {
  await page.getByLabel("Email").fill("ada@acme.io");
  await page.getByLabel("Password").fill("correct horse");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
}

test.describe("plan & billing (identity)", () => {
  test("a free org subscribes from Workspace through identity checkout", async ({ page }) => {
    await setup(page);
    await page.route("**/identity/v1/billing/subscription", json(200, status("community")));
    let sent: { tier: string; return_url: string } | null = null;
    await page.route("**/identity/v1/billing/checkout", async (route) => {
      sent = route.request().postDataJSON();
      await json(201, { transaction_id: "txn_1", checkout_url: "/identity/v1/billing/pay?_ptxn=txn_1" })(route);
    });
    await page.route("**/identity/v1/billing/pay?_ptxn=txn_1", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<p>paddle checkout</p>" }));
    await page.goto("/#/workspace");
    await logIn(page);
    await expect(page.getByText("One plan covers the Lab and the Control Plane.")).toBeVisible();
    // only the self-serve rungs are offered; Enterprise is contracted
    await expect(page.getByRole("button", { name: "Subscribe" })).toHaveCount(2);
    await page.getByRole("button", { name: "Subscribe" }).first().click();
    await expect(page.getByText("paddle checkout")).toBeVisible();
    expect(sent!.tier).toBe("team");
    expect(sent!.return_url).toMatch(/#\/workspace$/);
  });

  test("a landing ?plan= link survives sign-up, then opens checkout", async ({ page }) => {
    await setup(page);
    await page.route("**/identity/v1/billing/subscription", json(200, status("community")));
    let tier = "";
    await page.route("**/identity/v1/billing/checkout", async (route) => {
      tier = route.request().postDataJSON().tier;
      await json(201, { transaction_id: "txn_2", checkout_url: "/identity/v1/billing/pay?_ptxn=txn_2" })(route);
    });
    await page.route("**/identity/v1/billing/pay?_ptxn=txn_2", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<p>paddle checkout</p>" }));
    await page.goto("/?plan=security");
    await page.getByRole("link", { name: "Create a workspace" }).click();
    await page.getByLabel("Email").fill("ada@acme.io");
    await page.getByLabel("Password").fill("correct horse");
    await page.getByLabel("Organization name").fill("Acme");
    await page.getByRole("button", { name: "Create workspace" }).click();
    await expect(page.getByText("paddle checkout")).toBeVisible();
    expect(tier).toBe("security");
  });

  test("back from a paid checkout: the plan, the outcome, and the portal", async ({ page }) => {
    await setup(page, "team");
    const sub = { status: "active", tier: "team", current_period_end: "2026-10-23T00:00:00Z", scheduled_change: null };
    await page.route("**/identity/v1/billing/subscription", json(200, status("team", sub, true)));
    await page.route("**/identity/v1/refresh", json(200, { ...SESSION, org: { ...SESSION.org, tier: "team" } }));
    await page.route("**/identity/v1/billing/portal", json(200, { url: "/portal-stub" }));
    await page.route("**/portal-stub", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<p>customer portal</p>" }));
    await page.goto("/#/workspace");
    await logIn(page);
    await page.goto("/?billing=success#/workspace");
    await expect(page.getByText("Payment received — your plan is active.")).toBeVisible();
    await expect(page.getByText(/Subscription active · renews 2026-10-23/)).toBeVisible();
    // a subscribed org changes plan in the portal, not with a second checkout
    await expect(page.getByRole("button", { name: "Subscribe" })).toHaveCount(0);
    await page.getByRole("button", { name: /Manage billing/ }).click();
    await expect(page.getByText("customer portal")).toBeVisible();
  });

  test("with billing off the Lab's own plan flow is unchanged", async ({ page }) => {
    await setup(page);
    await page.route("**/identity/v1/billing/config", json(200, { enabled: false }));
    await page.goto("/#/workspace");
    await logIn(page);
    await expect(page.getByRole("heading", { name: "Plans" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Billing" })).toHaveCount(0);
  });
});
