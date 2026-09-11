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
