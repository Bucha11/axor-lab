import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, setToken } from "./api";

/** The open-mode probe and the guest-session mint hit the documented paths. */

const calls: { url: string; method?: string }[] = [];

function respond(status: number, body: unknown) {
  return vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method });
    return {
      ok: status >= 200 && status < 300,
      status,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  });
}

beforeEach(() => {
  calls.length = 0;
  setToken(null);
});
afterEach(() => vi.unstubAllGlobals());

describe("open-mode + guest endpoints", () => {
  it("GET /auth/status", async () => {
    vi.stubGlobal("fetch", respond(200, { auth_required: false, guest: true }));
    const s = await api.authStatus();
    expect(s.auth_required).toBe(false);
    expect(s.guest).toBe(true);
    expect(calls[0]).toEqual({ url: "/auth/status", method: "GET" });
  });

  it("POST /guest-session", async () => {
    vi.stubGlobal("fetch", respond(201, { token: "g", workspace_id: "guest_1", expires_at: 1 }));
    const g = await api.guestSession();
    expect(g.token).toBe("g");
    expect(calls[0]).toEqual({ url: "/guest-session", method: "POST" });
  });
});
