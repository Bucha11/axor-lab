import type { Page, Route } from "@playwright/test";

/** JSON responder for page.route. */
export function json(status: number, body: unknown) {
  return (route: Route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
}

/** A minimal /home payload — enough that the Home screen renders without error
 * after the login gate is passed. The shape mirrors HomePayload loosely; the
 * screen tolerates empty collections. */
export const EMPTY_HOME = {
  onboarding_step: "",
  quick_actions: [],
  suites: [],
  counts: {},
  recent_runs: [],
};

/** A fake identity session, as /identity/v1/{login,signup} returns it. */
export const SESSION = {
  access_token: "acc-token",
  refresh_token: "ref-token",
  token_type: "Bearer",
  expires_in: 900,
  user: { user_id: "usr_1", email: "ada@acme.io" },
  org: { org_id: "org_1", role: "owner", tier: "community" },
};

/** Stub /auth/status and a lenient /home so the app shell can render. Pass the
 * auth posture the server should report. */
export async function stubShell(
  page: Page,
  auth: { auth_required: boolean; guest: boolean },
): Promise<void> {
  await page.route("**/auth/status", json(200, auth));
  await page.route("**/home", json(200, EMPTY_HOME));
}

/** Empty-but-valid payloads for the list screens, so navigation can visit each
 * one without a live backend. */
export async function stubScreens(page: Page): Promise<void> {
  await page.route("**/suites", json(200, { suites: [] }));
  await page.route("**/runs", json(200, { runs: [] }));
  await page.route("**/runtimes", json(200, { runtimes: [] }));
  await page.route("**/evidence", json(200, { evidence_cases: [] }));
  await page.route("**/regressions", json(200, { regressions: [] }));
  await page.route("**/artifacts", json(200, { artifacts: [] }));
  // the commercial half: a workspace, its plan catalog and its members. Empty
  // but VALID, so navigation can reach the screen without a live backend.
  await page.route("**/workspaces/current", json(200, {
    id: "ws_test", name: "Test workspace",
    plan: { name: "free", max_suites: 3, max_artifacts: 10, max_hosted_runtimes: 0,
            capabilities: [] },
    is_admin: false, org: null,
    subscription: { plan_id: "free", status: "active" },
    created_at: 0, role: "viewer",
  }));
  await page.route("**/workspaces", json(403, { error: "listing workspaces requires admin" }));
  await page.route("**/workspaces/current/members", json(200, { members: [] }));
  await page.route("**/billing/plans", json(200, { plans: [] }));
  await page.route("**/hosted-runtimes", json(403, { error: "hosted_execution not in plan" }));
  await page.route("**/registry/suites", json(403, { error: "private_registry not in plan" }));
}
