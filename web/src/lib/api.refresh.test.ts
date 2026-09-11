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
