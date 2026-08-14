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

const IDENTITY_BASE = (import.meta.env.VITE_IDENTITY_URL ?? "/identity").replace(/\/$/, "");
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
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail =
      typeof payload?.detail === "string" ? payload.detail : `login failed (${response.status})`;
    throw new IdentityError(response.status, detail);
  }
  return payload as T;
}

export async function login(email: string, password: string, orgId?: string): Promise<Session> {
  const body: Record<string, string> = { email, password };
  if (orgId) body.org_id = orgId;
  return post<Session>("/v1/login", body);
}

export async function signup(email: string, password: string, orgName: string): Promise<Session> {
  return post<Session>("/v1/signup", { email, password, org_name: orgName });
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
