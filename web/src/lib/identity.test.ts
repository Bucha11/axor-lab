import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IdentityError, login, refreshAccess, signup, storedRefresh } from "./identity";

/**
 * The identity client: it talks to the login service (not lab_server), it
 * surfaces failures as failures, and it rotates the refresh token it stores.
 */

const calls: { url: string; body: unknown }[] = [];

function respond(status: number, body: unknown) {
  return vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, body: init.body ? JSON.parse(init.body as string) : null });
    return {
      ok: status >= 200 && status < 300,
      status,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  });
}

const SESSION = {
  access_token: "acc1",
  refresh_token: "ref1",
  user: { user_id: "u1", email: "a@acme.io" },
  org: { org_id: "o1", role: "owner", tier: "team" },
};

const store = new Map<string, string>();

beforeEach(() => {
  calls.length = 0;
  store.clear();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("login and signup hit the identity service", () => {
  it("posts to /v1/login under the identity base", async () => {
    vi.stubGlobal("fetch", respond(200, SESSION));
    const session = await login("a@acme.io", "pw");
    expect(session.org.role).toBe("owner");
    expect(calls[0]!.url).toBe("/identity/v1/login");
    expect(calls[0]!.body).toEqual({ email: "a@acme.io", password: "pw" });
  });

  it("includes org_id when disambiguating", async () => {
    vi.stubGlobal("fetch", respond(200, SESSION));
    await login("a@acme.io", "pw", "o2");
    expect(calls[0]!.body).toEqual({ email: "a@acme.io", password: "pw", org_id: "o2" });
  });

  it("posts org_name on signup", async () => {
    vi.stubGlobal("fetch", respond(201, SESSION));
    await signup("a@acme.io", "pw", "Acme");
    expect(calls[0]!.url).toBe("/identity/v1/signup");
    expect(calls[0]!.body).toEqual({ email: "a@acme.io", password: "pw", org_name: "Acme" });
  });

  it("raises IdentityError with the server detail on failure", async () => {
    vi.stubGlobal("fetch", respond(401, { detail: "invalid email or password" }));
    await expect(login("a@acme.io", "bad")).rejects.toBeInstanceOf(IdentityError);
  });
});

describe("refresh rotation", () => {
  it("returns null with no stored refresh token", async () => {
    expect(await refreshAccess()).toBeNull();
  });

  it("exchanges and rotates the stored refresh token", async () => {
    sessionStorage.setItem("axor-lab-refresh-token", "ref1");
    vi.stubGlobal("fetch", respond(200, { ...SESSION, access_token: "acc2", refresh_token: "ref2" }));
    const next = await refreshAccess();
    expect(next).toBe("acc2");
    expect(storedRefresh()).toBe("ref2"); // rotated
    expect(calls[0]!.url).toBe("/identity/v1/refresh");
  });

  it("clears the refresh token when the exchange is rejected", async () => {
    sessionStorage.setItem("axor-lab-refresh-token", "stale");
    vi.stubGlobal("fetch", respond(401, { detail: "invalid refresh token" }));
    expect(await refreshAccess()).toBeNull();
    expect(storedRefresh()).toBeNull();
  });
});
