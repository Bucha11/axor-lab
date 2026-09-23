import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, currentToken, setToken, setUnauthorizedHandler } from "./api";

/** A 401 triggers the registered refresh hook once, then the request is retried
 * with the new token — the screen never sees the expiry. */

beforeEach(() => setToken("expired"));
afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

describe("transparent refresh on 401", () => {
  it("refreshes once and retries the request", async () => {
    let n = 0;
    const fetchMock = vi.fn(async () => {
      n += 1;
      const unauthorized = n === 1;
      return {
        ok: !unauthorized,
        status: unauthorized ? 401 : 200,
        text: async () => JSON.stringify({ ok: true }),
      } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    const refresh = vi.fn(async () => "fresh-token");
    setUnauthorizedHandler(refresh);

    await api.home();

    expect(refresh).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2); // original + retry
    expect(currentToken()).toBe("fresh-token");
  });

  it("gives up when refresh returns null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 401, text: async () => "{}" }) as unknown as Response),
    );
    setUnauthorizedHandler(vi.fn(async () => null));
    await expect(api.home()).rejects.toMatchObject({ status: 401 });
  });
});

/** The identity service ROTATES refresh tokens — each is single-use. A screen
 * fires several requests at once, so an expiry arrives as several parallel
 * 401s; each running its own refresh spent the same token twice, and the loser
 * logged the user out in the middle of a successful refresh. */
describe("refresh is single-flight", () => {
  function tokenGated() {
    // 401 for anything not carrying the fresh token
    return vi.fn(async (_url: string, init: RequestInit = {}) => {
      const auth = (init.headers as Record<string, string>)?.["Authorization"];
      const fresh = auth === "Bearer fresh-token";
      return {
        ok: fresh,
        status: fresh ? 200 : 401,
        text: async () => JSON.stringify(fresh ? { runs: [] } : { error: "expired" }),
      } as unknown as Response;
    });
  }

  it("parallel 401s share ONE refresh", async () => {
    vi.stubGlobal("fetch", tokenGated());
    let release: (value: string) => void = () => {};
    const refresh = vi.fn(
      () => new Promise<string | null>((resolve) => {
        release = resolve;
      }),
    );
    setUnauthorizedHandler(refresh);

    const pending = Promise.all([api.home(), api.runs(), api.evidence()]);
    // let all three requests come back 401 and queue on the refresh
    await new Promise((r) => setTimeout(r, 0));
    release("fresh-token");
    await pending;

    expect(refresh).toHaveBeenCalledOnce();
    expect(currentToken()).toBe("fresh-token");
  });

  it("a 401 that lands after another request refreshed retries without refreshing", async () => {
    vi.stubGlobal("fetch", tokenGated());
    const refresh = vi.fn(async () => "fresh-token");
    setUnauthorizedHandler(refresh);
    await api.home(); // refreshes once
    // a request that was sent with the OLD token and 401s now
    setToken("expired");
    const slow = api.runs();
    setToken("fresh-token"); // what the first refresh already installed
    await slow;
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("file downloads refresh like JSON reads", async () => {
    vi.stubGlobal("fetch", tokenGated());
    const refresh = vi.fn(async () => "fresh-token");
    setUnauthorizedHandler(refresh);
    await expect(api.artifactDownload("a1")).resolves.toBe(JSON.stringify({ runs: [] }));
    await expect(api.suiteYaml("s1")).resolves.toBeTypeOf("string");
    expect(refresh).toHaveBeenCalledOnce();
  });
});

describe("a body that is not JSON", () => {
  it("an error page surfaces as a status-bearing ApiError, not a SyntaxError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false, status: 502, text: async () => "<html>Bad Gateway</html>",
      }) as unknown as Response),
    );
    await expect(api.home()).rejects.toMatchObject({ status: 502 });
    await expect(api.home()).rejects.toThrow(/GET \/home failed \(502\)/);
  });

  it("a 200 that is not JSON says so", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true, status: 200, text: async () => "<!doctype html>",
      }) as unknown as Response),
    );
    await expect(api.home()).rejects.toThrow(/non-JSON/);
  });

  it("suiteYamlOf surfaces the server's error string, not the raw JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false, status: 422, text: async () => JSON.stringify({ error: "bad manifest" }),
      }) as unknown as Response),
    );
    await expect(api.suiteYamlOf({})).rejects.toThrow(/^bad manifest$/);
  });
});
