/**
 * Client for the axor-identity login service.
 *
 * This is a SEPARATE service from lab_server: a human logs in here (email +
 * password), gets an access token, and that token is what every lab_server
 * request then carries (lab_server verifies it against the identity JWKS). The
 * base URL is configured with VITE_IDENTITY_URL; it defaults to `/identity`, so
 * a reverse proxy can mount the identity service beside the Lab on one origin.
 *
 * Access tokens are short-lived; the refresh token (also held here, in
 * sessionStorage) is exchanged for a new one when a request comes back 401.
 */

// `||`, not `??`: a Docker build passes the build arg through as an EMPTY
// string when it is unset, and `??` kept "" — every identity call then went to
// `/v1/login` on the Lab's own origin, which has no such route.
const IDENTITY_BASE = (import.meta.env.VITE_IDENTITY_URL || "/identity").replace(/\/$/, "");
const REFRESH_KEY = "axor-lab-refresh-token";

export interface Session {
  access_token: string;
  refresh_token: string;
  user: { user_id: string; email: string };
  org: { org_id: string; role: string; tier: string };
}

export class IdentityError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${IDENTITY_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new IdentityError(0, "cannot reach the identity service");
  }
  const text = await response.text();
  // A body that is not JSON — a proxy's 502 page, or the SPA's index.html when
  // nothing routes /identity — used to throw a bare SyntaxError, which Login
  // flattened to "login failed" with no hint that the SERVICE was missing.
  let payload: unknown = {};
  let parsed = true;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      parsed = false;
    }
  }
  if (!response.ok) {
    const detail = (payload as { detail?: unknown })?.detail;
    throw new IdentityError(
      response.status,
      typeof detail === "string" ? detail : `login failed (${response.status})`,
    );
  }
  if (!parsed) {
    throw new IdentityError(
      response.status,
      "the identity service answered with something that is not JSON — is it deployed at this address?",
    );
  }
  return payload as T;
}

export async function login(email: string, password: string, orgId?: string): Promise<Session> {
  const body: Record<string, string> = { email, password };
  if (orgId) body.org_id = orgId;
  return post<Session>("/v1/login", body);
}

export async function signup(email: string, password: string, orgName: string): Promise<Session> {
  // the verification link it mails opens the Lab again
  return post<Session>("/v1/signup", { email, password, org_name: orgName, return_to: here() });
}

/** This page, absolute (one identity service may serve the Lab and the control
 * plane, so a relative way back would land on the other one). */
export function here(): string | undefined {
  // no page (a test, a worker): the identity service's default way back
  if (typeof window === "undefined") return undefined;
  return window.location.origin + window.location.pathname + window.location.hash;
}

/** Whether the identity service sends mail (reset links, verification). */
export async function emailAvailable(): Promise<boolean> {
  try {
    const response = await fetch(`${IDENTITY_BASE}/v1/email`);
    if (!response.ok) return false;
    return Boolean(((await response.json()) as { available?: boolean }).available);
  } catch {
    return false;
  }
}

export async function forgotPassword(email: string): Promise<{ detail: string }> {
  return post<{ detail: string }>("/v1/password/forgot", { email, return_to: here() });
}

export async function resetPassword(token: string, password: string): Promise<unknown> {
  return post<unknown>("/v1/password/reset", { token, password });
}

export async function verifyEmail(token: string): Promise<{ email: string }> {
  return post<{ email: string }>("/v1/email/verify", { token });
}

export function rememberRefresh(token: string | null): void {
  if (token) sessionStorage.setItem(REFRESH_KEY, token);
  else sessionStorage.removeItem(REFRESH_KEY);
}

export function storedRefresh(): string | null {
  return sessionStorage.getItem(REFRESH_KEY);
}

/** Exchange the stored refresh token for a fresh access token, rotating the
 * refresh token. Returns the new access token, or null if there is no valid
 * refresh token (the user must log in again). */
export async function refreshAccess(): Promise<string | null> {
  const refresh = storedRefresh();
  if (!refresh) return null;
  try {
    const next = await post<Session>("/v1/refresh", { refresh_token: refresh });
    rememberRefresh(next.refresh_token);
    return next.access_token;
  } catch {
    rememberRefresh(null);
    return null;
  }
}

/** Revoke the stored refresh token at the identity service, best-effort.
 *
 * Logging out only forgot it locally, so the refresh token stayed valid for its
 * whole lifetime — anyone who had copied it out of sessionStorage could keep
 * minting access tokens after the user believed they had signed out. A failure
 * here must not keep the user logged in, so it is swallowed; the local copy is
 * dropped either way. */
export async function logout(): Promise<void> {
  const refresh = storedRefresh();
  rememberRefresh(null);
  if (!refresh) return;
  try {
    await post<unknown>("/v1/logout", { refresh_token: refresh });
  } catch {
    /* best-effort: the session is over locally regardless */
  }
}

// ── sign in with GitHub / Google ─────────────────────────────────────────────
export interface OAuthProvider {
  name: string;
  label: string;
}

/** The providers the identity service has set up; empty when none, or when
 * the service predates the route. */
export async function oauthProviders(): Promise<OAuthProvider[]> {
  try {
    const response = await fetch(`${IDENTITY_BASE}/v1/oauth/providers`);
    if (!response.ok) return [];
    const body = (await response.json()) as { providers?: OAuthProvider[] };
    return Array.isArray(body.providers) ? body.providers : [];
  } catch {
    return [];
  }
}

/** Where to send the browser to sign in with `provider`. The identity service
 * may live on another origin (one service for the Lab and the control plane),
 * so the way back is absolute; it must be one of its AXOR_IDENTITY_RETURN_ORIGINS. */
export function oauthStartUrl(provider: string): string {
  const back = here() ?? "/";
  return `${IDENTITY_BASE}/v1/oauth/${encodeURIComponent(provider)}/start?return_to=${encodeURIComponent(back)}`;
}

/** The query keys a provider sign-in or an e-mailed link brings back. */
export const AUTH_RETURN = /[?&]axor_(login|login_error|verify|reset)=/;

/** What a provider sign-in or an e-mailed link left in the address bar,
 * removed from it so a reload or a copied link does not carry it. */
export function takeAuthReturn(): {
  code?: string; error?: string; verify?: string; reset?: string;
} | null {
  const params = new URLSearchParams(window.location.search);
  const got = {
    code: params.get("axor_login") ?? undefined,
    error: params.get("axor_login_error") ?? undefined,
    verify: params.get("axor_verify") ?? undefined,
    reset: params.get("axor_reset") ?? undefined,
  };
  if (!got.code && !got.error && !got.verify && !got.reset) return null;
  for (const k of ["axor_login", "axor_login_error", "axor_verify", "axor_reset"]) params.delete(k);
  const query = params.toString();
  window.history.replaceState(
    null, "", window.location.pathname + (query ? `?${query}` : "") + window.location.hash);
  return got;
}

/** Trade the one-time code from the redirect for a session. */
export async function oauthExchange(code: string): Promise<Session & { created: boolean }> {
  return post<Session & { created: boolean }>("/v1/oauth/exchange", { code });
}
