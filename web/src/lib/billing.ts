/**
 * Buying a plan. Billing lives in axor-identity and is shared with the Control
 * Plane: one org, one plan, both products (the CP runs this same flow). The Lab
 * server takes an identity org's plan from the tier in its login, so paying
 * here changes what the Lab gates on once the session refreshes. The server
 * never sees a card — checkout is Paddle's page, reached through
 * /identity/v1/billing/pay on this origin, which sends the payer back here with
 * ?billing=<outcome>.
 */
import { currentToken, setToken } from "./api";
import { IDENTITY_BASE, refreshAccess } from "./identity";

export const BUYABLE_TIERS = ["team", "security"];

export interface BillingConfig {
  enabled: boolean;
  environment?: string;
  tiers?: string[];
}

export interface BillingStatus {
  enabled: boolean;
  org_id: string;
  tier: string;
  token_tier: string;
  subscription: {
    status: string;
    tier: string;
    current_period_end: string | null;
    scheduled_change: string | null;
  } | null;
  is_admin: boolean;
  can_manage: boolean;
}

async function call<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const headers: Record<string, string> = {};
  const token = currentToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${IDENTITY_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401 && !retried) {
    const next = await refreshAccess();
    if (next) {
      setToken(next);
      return call<T>(method, path, body, true);
    }
  }
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    /* not JSON: an unproxied /identity answers with the SPA's HTML */
  }
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : `billing request failed (${response.status})`);
  }
  if (payload === null) throw new Error("billing is not reachable at this address");
  return payload as T;
}

export const billing = {
  /** Public. `{enabled: false}` — or an error — means billing is off here. */
  config: () => call<BillingConfig>("GET", "/v1/billing/config"),
  status: () => call<BillingStatus>("GET", "/v1/billing/subscription"),
  checkout: (tier: string, returnUrl: string) =>
    call<{ transaction_id: string; checkout_url: string }>("POST", "/v1/billing/checkout", {
      tier,
      return_url: returnUrl,
    }),
  portal: () => call<{ url: string }>("POST", "/v1/billing/portal"),
};

// A plan picked before signing in — from a landing page's ?plan= link —
// survives the sign-up and starts the checkout right after it.
const PENDING_KEY = "axor.pendingPlan";
const OUTCOME_KEY = "axor.billingOutcome";

function stored(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the user picks the plan again */
  }
}

export function pendingPlan(): string | null {
  const plan = stored(PENDING_KEY);
  return plan && BUYABLE_TIERS.includes(plan) ? plan : null;
}

/** The outcome of the checkout the payer just came back from, read once. */
export function takeOutcome(): string | null {
  const outcome = stored(OUTCOME_KEY);
  store(OUTCOME_KEY, null);
  return outcome;
}

export async function startCheckout(tier: string): Promise<void> {
  store(PENDING_KEY, null);
  const back = `${window.location.origin}${window.location.pathname}#/workspace`;
  const { checkout_url } = await billing.checkout(tier, back);
  window.location.assign(checkout_url);
}

export async function openPortal(): Promise<void> {
  window.location.assign((await billing.portal()).url);
}

/**
 * Consume `?billing=` (back from checkout) and `?plan=` (from a landing page)
 * once, at load. Returns true when either was present. After a successful
 * payment the webhook can land a moment after the redirect, so the session is
 * refreshed until the new plan shows up — then every Lab request carries it.
 */
export async function consumeBillingParams(): Promise<boolean> {
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("billing");
  const plan = params.get("plan");
  if (!outcome && !plan) return false;
  if (plan && BUYABLE_TIERS.includes(plan)) store(PENDING_KEY, plan);
  if (outcome) store(OUTCOME_KEY, outcome);
  params.delete("billing");
  params.delete("plan");
  const query = params.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`,
  );
  if (outcome === "success" && currentToken()) {
    for (let attempt = 0; attempt < 6; attempt++) {
      const next = await refreshAccess();
      if (next) setToken(next);
      const status = await billing.status().catch(() => null);
      if (status && status.tier !== "community" && status.token_tier === status.tier) break;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
  return true;
}
