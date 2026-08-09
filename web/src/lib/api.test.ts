import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { ApiError, api, currentToken, setToken } from "./api";

/**
 * The client is the ONE place this app knows an endpoint exists. These tests
 * pin the two properties that make that worth having: the paths match
 * ui-backend-contract.md, and a failure surfaces as a failure.
 */

const calls: { url: string; init: RequestInit }[] = [];

function respond(status: number, body: unknown) {
  return vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
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

describe("the endpoints match the contract table", () => {
  it("names the documented paths", async () => {
    vi.stubGlobal("fetch", respond(200, {}));
    await api.home();
    await api.suites();
    await api.suite("budget");
    await api.runReport("r1");
    await api.trial("r1", "t1");
    await api.evidence();
    await api.regressions();
    await api.artifacts();
    await api.playground({ suite_id: "budget" });
    expect(calls.map((c) => `${c.init.method} ${c.url}`)).toEqual([
      "GET /home",
      "GET /suites",
      "GET /suites/budget",
      "GET /runs/r1/report",
      "GET /runs/r1/trials/t1",
      "GET /evidence",
      "GET /regressions",
      "GET /artifacts",
      "POST /playground/trial",
    ]);
  });

  it("encodes an id that would otherwise change the path", async () => {
    vi.stubGlobal("fetch", respond(200, {}));
    // trial ids are `sha256:...` and evidence ids can carry a slash-free but
    // colon-heavy shape; an unencoded one silently addresses a different route
    await api.trial("r1", "sha256:abc/def");
    expect(calls[0]!.url).toBe("/runs/r1/trials/sha256%3Aabc%2Fdef");
  });
});

describe("the run + suite lifecycle endpoints exist and use the right verbs", () => {
  it("maps create/delete/list/cancel/confirm to their documented routes", async () => {
    vi.stubGlobal("fetch", respond(200, {}));
    await api.createSuite({ id: "s" });
    await api.deleteSuite("s");
    await api.runs();
    await api.cancelRun("r1");
    await api.confirmRun("r1");
    expect(calls.map((c) => `${c.init.method} ${c.url}`)).toEqual([
      "POST /suites",
      "DELETE /suites/s",
      "GET /runs",
      "POST /runs/r1/cancel",
      "POST /runs/r1/confirm",
    ]);
  });
});

describe("the control token", () => {
  it("is sent when set and absent when not", async () => {
    vi.stubGlobal("fetch", respond(200, {}));
    await api.home();
    expect((calls[0]!.init.headers as Record<string, string>).Authorization).toBeUndefined();
    setToken("secret");
    await api.home();
    expect((calls[1]!.init.headers as Record<string, string>).Authorization).toBe(
      "Bearer secret",
    );
    expect(currentToken()).toBe("secret");
  });

  it("treats an empty string as no token", () => {
    setToken("");
    expect(currentToken()).toBeNull();
  });
});

describe("failures surface as failures", () => {
  it("raises with the server's own message", async () => {
    vi.stubGlobal("fetch", respond(404, { error: "no regression 'x'" }));
    await expect(api.regression("x")).rejects.toThrow("no regression 'x'");
  });

  it("carries the status so a screen can tell 401 from 500", async () => {
    vi.stubGlobal("fetch", respond(401, { error: "control token required" }));
    await expect(api.home()).rejects.toBeInstanceOf(ApiError);
    await api.home().catch((exc: ApiError) => expect(exc.status).toBe(401));
  });

  it("never resolves to an empty list on error", async () => {
    // a screen rendering [] on a failed request tells the user their workspace
    // is empty, which is a worse lie than "this did not load"
    vi.stubGlobal("fetch", respond(500, {}));
    await expect(api.evidence()).rejects.toBeTruthy();
  });
});
