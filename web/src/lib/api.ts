/**
 * The screen API client — the ONE place this app knows an endpoint exists.
 *
 * ui-backend-contract.md: every screen renders a schema-conforming payload from
 * a NAMED endpoint. A component that builds its own URL is a second, unreviewed
 * copy of that table, and the two drift the first time a path changes.
 *
 * Two things this deliberately does NOT do:
 *
 *   - It computes nothing. No rates, no thresholds, no verdicts. The contract's
 *     rule is "rendered, never computed": every number a screen shows was
 *     produced by the backend and published in an artifact. A client that
 *     derived one would give the UI a second opinion about a cited figure.
 *   - It ships no fallback data. A failed request surfaces as an error, never
 *     as an empty list that reads like a healthy empty workspace.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** The control token gates every screen endpoint (see `_require_control`). */
let token: string | null = null;

export function setToken(value: string | null): void {
  token = value && value.length > 0 ? value : null;
}

export function currentToken(): string | null {
  return token;
}

/** An optional hook that tries to obtain a fresh token when a request comes
 * back 401 (identity login registers it to refresh an expired access token).
 * Returns the new token, or null if the caller must re-authenticate. Kept as a
 * registered callback so this module stays unaware of the identity service. */
let onUnauthorized: (() => Promise<string | null>) | null = null;

export function setUnauthorizedHandler(handler: (() => Promise<string | null>) | null): void {
  onUnauthorized = handler;
}

async function call<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  // an expired access token: refresh once, transparently, then retry
  if (response.status === 401 && !retried && onUnauthorized) {
    const refreshed = await onUnauthorized();
    if (refreshed) {
      setToken(refreshed);
      return call<T>(method, path, body, true);
    }
  }
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new ApiError(
      response.status,
      typeof payload?.error === "string" ? payload.error : `${method} ${path} failed`,
    );
  }
  return payload as T;
}

// ── payload shapes ───────────────────────────────────────────────────────────
// Mirrors of what the endpoints return. Where a field is a Lab-owned schema the
// type stays deliberately open (`Json`): the SCHEMA is the contract, and
// restating it here in TypeScript creates a second definition to keep in sync.

export type Json = Record<string, unknown>;

export interface SuiteCard {
  id: string;
  name: string;
  description?: string;
  origin?: string;
  tags?: string[];
  capabilities?: string[];
  available: boolean;
  reason?: string;
}

export interface HomePayload {
  onboarding_step: string;
  quick_actions: { id: string; label: string; endpoint: string }[];
  suites: SuiteCard[];
  counts: Record<string, number>;
  recent_runs: { run_id: string; state: string }[];
}

export interface RunRow {
  run_id: string;
  state: string;
  planned: number;
  completed: number;
  created_at?: number;
  updated_at?: number;
}

export interface RunReport {
  run_id: string;
  state: string;
  planned_trials: number;
  trials_by_status: Record<string, number>;
  coverage: { completed: number; planned: number };
  metric_coverage: Record<string, number>;
  aggregates: Json[];
  estimate?: Record<string, number>;
}

export interface TrialRecord {
  trial_id: string;
  status: string;
  scenario_id?: string;
  condition_id?: string;
  repeat_index?: number;
  metrics?: Record<string, number | string | boolean>;
  failure_reason?: string;
}

export interface TrialDetail {
  run_id: string;
  trial: TrialRecord;
  trace: Json | null;
}

export interface PlaygroundResult {
  mode: string;
  trial: TrialRecord | null;
  trace: Json | null;
  counted_in_a_run: boolean;
  evidence_cases: Json[];
}

export interface EvidenceRow {
  id: string;
  kind: string;
  title: string;
  status?: string;
  severity?: string;
  created?: string;
  trial_ref?: Json;
}

export interface RegressionRow {
  id: string;
  name: string;
  status?: string;
  created?: string;
  expectation?: string;
}

export interface ArtifactRow {
  artifact_id: string;
  created?: string;
  suite_id?: string;
}

export interface InvariantOutcome {
  regression_id: string;
  run_id: string;
  status: "passed" | "failed" | "error" | "skipped";
  detail: string;
  trials_checked: number;
  trials_failed: number;
  failing_trial_ids: string[];
}

export interface RuntimeRow {
  runtime_ref: string;
  agent_ref?: string;
  /** free-form display name for the connection — NOT a model the platform
   * calls. The connected agent runs its own inference; this only labels it. */
  runtime_label?: string;
  status?: string;
}

export interface RunResults {
  run_id: string;
  state: string;
  planned_trials: string[];
  trials: TrialRecord[];
  aggregates: Json[];
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  /** the parsed manifest, on a successful YAML validation. The client never
   * parses YAML itself — a second implementation would disagree with the
   * server's about `on`, `~` and dates, and the one that decides whether a run
   * starts is the server's. */
  suite?: Json;
}

export type CheckRow = {
  name: string;
  status: "ok" | "invalid" | "unverified";
  message: string;
};

export type CheckReport = {
  outcome: "ok" | "failure" | "validation" | "unverified" | "regression_differs";
  checks: CheckRow[];
  failed: string[];
  traces?: number;
  earned_bridge?: boolean;
};

export type HandoffPackage = {
  files: Record<string, string>;
  manifest: Json;
  config: Json;
  signed: boolean;
  condition_id: string;
  baseline_condition_id: string;
  regressions_carried: number;
  earned_bridge: boolean;
};

export interface Plan {
  name: string;
  price_usd?: number;
  max_suites: number | null;
  max_artifacts: number | null;
  max_hosted_runtimes: number | null;
  capabilities: string[];
}

export interface Subscription {
  plan_id: string;
  status: string;
}

/** A tenant. `is_admin` is the workspace's own standing on this server; `role`
 * is the CALLER's standing inside it, and only /workspaces/current carries it. */
export interface Workspace {
  id: string;
  name: string;
  plan: Plan;
  is_admin: boolean;
  org: string | null;
  subscription: Subscription;
  created_at: number;
  role?: string;
}

export interface MemberCount {
  role: string;
  count: number;
}

/** Who changed what. The compliance surface — admin only. */
export interface AuditEntry {
  at: number;
  actor_role: string;
  action: string;
  detail: string;
}

export interface RuntimeConnection {
  runtime_ref: string;
  ingest_key: string;
}

export interface Checkout {
  session_id: string;
  checkout_url: string;
  plan_id: string;
}

export interface ValidationOk {
  ok: boolean;
  errors: string[];
}

export interface ExperimentPlan {
  trials: string[];
  estimate: Json;
}

export interface SuitePlan {
  trials: string[];
  /** the arm ids actually planned, INCLUDING a synthesized ungoverned one for
   * a suite that declares no conditions. Naming them is the point: a preview
   * that guessed the arm produced trial ids no run would ever use. */
  conditions: string[];
  /** why a dispatch would be refused. A preview reports them rather than
   * failing — seeing the plan is how you find out the suite cannot run. */
  blockers: string[];
  estimate: Json;
}

// ── endpoints ────────────────────────────────────────────────────────────────

export const api = {
  /** Unauthenticated: whether the server requires a login at all (open/local
   * mode does not) and whether it offers anonymous guest sessions. */
  authStatus: () => call<{ auth_required: boolean; guest: boolean }>("GET", "/auth/status"),
  /** Start an anonymous, ephemeral hosted session — no registration. */
  guestSession: () =>
    call<{ token: string; workspace_id: string; expires_at: number }>(
      "POST",
      "/guest-session",
    ),

  home: () => call<HomePayload>("GET", "/home"),

  suites: () => call<{ suites: SuiteCard[] }>("GET", "/suites"),
  /** The registry a suite's `scenario_refs` resolve against — this workspace's
   * saved scenarios with the org's shared ones underneath. The Builder offers
   * these as the ref options: the field used to be free text against a registry
   * nothing filled, so any value typed there made the suite permanently
   * invalid ("scenario_ref 'x' resolves to nothing"). */
  registryScenarios: () =>
    call<{ scenarios: { name: string; task: string }[] }>("GET", "/scenarios"),
  registryScenario: (name: string) =>
    call<Json>("GET", `/scenarios/${encodeURIComponent(name)}`),
  saveScenario: (scenario: Json, manifests: Record<string, Json>) =>
    call<{ name: string }>("POST", "/scenarios", { scenario, manifests }),
  deleteScenario: (name: string) =>
    call<{ name: string; deleted: boolean }>(
      "DELETE", `/scenarios/${encodeURIComponent(name)}`,
    ),
  suite: (id: string) => call<Json>("GET", `/suites/${encodeURIComponent(id)}`),
  suiteYaml: async (id: string): Promise<string> => {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const response = await fetch(`/suites/${encodeURIComponent(id)}/yaml`, { headers });
    const text = await response.text();
    if (!response.ok) throw new ApiError(response.status, text || "yaml unavailable");
    return text;
  },
  /** Serialize the manifest the Builder is holding — the edited one, not the
   * stored one. There is exactly one YAML implementation and it is the
   * server's. */
  suiteYamlOf: async (suite: Json): Promise<string> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const response = await fetch("/suites/to-yaml", {
      method: "POST",
      headers,
      body: JSON.stringify({ suite }),
    });
    const text = await response.text();
    if (!response.ok) throw new ApiError(response.status, text || "yaml unavailable");
    return text;
  },
  /** Build a Control Plane handoff from a completed run. The DIRECTORY the CLI
   * writes and the file map returned here are the same bytes under the same
   * manifest — there is one implementation (`lab_service.handoff`). */
  exportHandoff: (runId: string) =>
    call<HandoffPackage>("POST", "/handoff/export", { run_id: runId }),
  /** Verify a handoff's file map. INTEGRITY, AUTHENTICITY and DERIVABILITY come
   * back as separate checks because they are separate claims. */
  verifyHandoff: (files: Record<string, string>, allowUnsigned: boolean) =>
    call<CheckReport>("POST", "/handoff/verify", {
      files,
      allow_unsigned: allowUnsigned,
    }),
  /** Verify a downloaded reproduction package offline — no server trusted. */
  verifyPackage: (pkg: Json, allowBare: boolean) =>
    call<CheckReport>("POST", "/verify/package", {
      package: pkg,
      allow_bare: allowBare,
    }),

  // ── workspace, plan, compliance (RFC §16 commercial half) ──────────────────
  /** The caller's own tenant, and the ROLE they hold inside it. */
  currentWorkspace: () => call<Workspace>("GET", "/workspaces/current"),
  /** Every tenant on this server. Admin only — a tenant must not enumerate others. */
  workspaces: () => call<{ workspaces: Workspace[] }>("GET", "/workspaces"),
  createWorkspace: (name: string, planId?: string) =>
    call<Workspace>("POST", "/workspaces", { name, plan: planId }),
  members: () => call<{ members: MemberCount[] }>("GET", "/workspaces/current/members"),
  /** Mint a member token at a role. The token is shown ONCE. */
  addMember: (role: string) =>
    call<{ role: string; token: string }>("POST", "/workspaces/current/members", { role }),
  audit: () => call<{ audit: AuditEntry[] }>("GET", "/workspaces/current/audit"),
  /** Grant a plan directly — the admin path, distinct from paying for one. */
  grantPlan: (planId: string, workspaceId?: string) =>
    call<{ workspace_id: string; subscription: Subscription }>(
      "POST", "/workspaces/current/plan", { plan_id: planId, workspace_id: workspaceId }),
  plans: () => call<{ plans: Plan[] }>("GET", "/billing/plans"),
  checkout: (planId: string) =>
    call<Checkout>("POST", "/billing/checkout", { plan_id: planId }),

  // ── hosted execution + org registry ────────────────────────────────────────
  /** Runtimes the PLATFORM provisions, as opposed to ones a customer connects. */
  hostedRuntimes: () =>
    call<{ hosted_runtimes: RuntimeRow[] }>("GET", "/hosted-runtimes"),
  provisionHostedRuntime: (label: string) =>
    call<RuntimeConnection>("POST", "/hosted-runtimes", { runtime_label: label }),
  /** The org's shared catalog — suites every workspace in the org can see. */
  registrySuites: () => call<{ suites: SuiteCard[] }>("GET", "/registry/suites"),
  registrySuite: (id: string) =>
    call<Json>("GET", `/registry/suites/${encodeURIComponent(id)}`),
  publishSuiteToOrg: (id: string) =>
    call<{ id: string; org: string }>("POST", `/suites/${encodeURIComponent(id)}/publish`, {}),

  // ── authoring aids ─────────────────────────────────────────────────────────
  /** Validate ONE scenario against the tool manifests it will run with.
   *
   * `manifests` is not optional in practice even though the endpoint defaults
   * it to `{}`: scenario semantics are largely ABOUT the tools — a declared
   * tool with no manifest, an injection with no untrusted field to land in, a
   * breach predicate with no WRITE/EXPORT/EXEC sink. Calling this without them
   * reported every one of those against every scenario, so the check was
   * pure false positives. Required here so a caller cannot omit them by
   * accident again; pass `{}` deliberately for a scenario that declares no
   * tools. */
  validateScenario: (scenario: Json, manifests: Record<string, Json>) =>
    call<ValidationOk>("POST", "/scenarios/validate", { scenario, manifests }),
  /** Expand an experiment into its planned trial units. A PLAN, not execution. */
  planExperiment: (experiment: Json) =>
    call<ExperimentPlan>("POST", "/experiments/plan", { experiment }),
  /** Expand a SUITE into the trial units a run would execute — through the
   * same planner a dispatch runs, so the preview and the run cannot disagree.
   *
   * Not `planExperiment`: that one plans an `experiment/v1`, and the Builder
   * was hand-building one from the manifest. It substituted the literal arm id
   * `"condition"` for a suite declaring none and ignored `scenario_refs`, so
   * the trial count was right and every trial id was wrong. */
  planSuite: (suite: Json) => call<SuitePlan>("POST", "/suites/plan", { suite }),

  // Three server endpoints deliberately have no client method:
  //
  //   GET  /workspaces/current/subscription — `currentWorkspace()` already
  //        carries both the subscription and the plan; a second call for the
  //        same two fields is a second place for them to disagree.
  //   GET  /runs/{id}/trials/{id}/trace — `trial()` embeds the trace already,
  //        and the Trial screen renders it from there.
  //   POST /runs/{id}/aggregates — the runner posts aggregates; Lab RENDERS
  //        them and does not compute them (ui-backend-contract §3). A button
  //        that overwrote a published figure would give the UI a second opinion
  //        about a number someone has already cited.

  /** The second funnel: a production trace becomes a bundle a policy can be
   * tested against. Nothing comes back until the incident REPLAYS under its own
   * recorded condition. */
  importIncident: (body: {
    trace: Json; scenario: Json; manifests: Json; condition: Json;
  }) => call<{
    bundle_id: string; trace_id: string; replay_status: string;
    bundle: Json; traces: Json[]; files: Record<string, string>;
  }>("POST", "/incidents/import", body),

  validateSuite: (suite: Json) =>
    call<ValidationResult>("POST", "/suites/validate", { suite }),
  validateSuiteYaml: (yaml: string) =>
    call<ValidationResult>("POST", "/suites/validate-yaml", { yaml }),
  createSuite: (suite: Json) => call<{ id: string }>("POST", "/suites", { suite }),
  saveSuite: (id: string, suite: Json) =>
    call<{ id: string }>("PUT", `/suites/${encodeURIComponent(id)}`, { suite }),
  deleteSuite: (id: string) =>
    call<{ id: string; deleted: boolean }>("DELETE", `/suites/${encodeURIComponent(id)}`),
  /** Bind a connected agent (a runtime_ref from Integrations) to the STORED
   * suite and start a run. The server resolves the suite saved-first, so what
   * runs is what Save wrote. */
  dispatchSuite: (id: string, runtimeRef: string) =>
    call<{ run_id: string; state: string; planned_trials: string[] }>(
      "POST",
      `/suites/${encodeURIComponent(id)}/dispatch`,
      { runtime_ref: runtimeRef },
    ),

  playground: (request: { suite_id?: string; suite?: Json; scenario?: string; seed?: string }) =>
    call<PlaygroundResult>("POST", "/playground/trial", request),

  runtimes: () => call<{ runtimes: RuntimeRow[] }>("GET", "/runtimes"),
  connectRuntime: (runtimeLabel: string, agentRef?: string) =>
    call<{ runtime_ref: string; ingest_key: string }>("POST", "/runtimes/connect", {
      runtime_label: runtimeLabel,
      agent_ref: agentRef,
    }),

  runs: () => call<{ runs: RunRow[] }>("GET", "/runs"),
  runResults: (runId: string) =>
    call<RunResults>("GET", `/runs/${encodeURIComponent(runId)}/results`),
  runReport: (runId: string) =>
    call<RunReport>("GET", `/runs/${encodeURIComponent(runId)}/report`),
  cancelRun: (runId: string) =>
    call<{ run_id: string; state: string }>(
      "POST", `/runs/${encodeURIComponent(runId)}/cancel`, {}),
  confirmRun: (runId: string) =>
    call<{ run_id: string; state: string }>(
      "POST", `/runs/${encodeURIComponent(runId)}/confirm`, {}),
  trial: (runId: string, trialId: string) =>
    call<TrialDetail>(
      "GET",
      `/runs/${encodeURIComponent(runId)}/trials/${encodeURIComponent(trialId)}`,
    ),

  evidence: () => call<{ evidence_cases: EvidenceRow[] }>("GET", "/evidence"),
  evidenceCase: (id: string) => call<Json>("GET", `/evidence/${encodeURIComponent(id)}`),
  createEvidence: (evidenceCase: Json) =>
    call<{ id: string }>("POST", "/evidence", { evidence_case: evidenceCase }),

  regressions: () => call<{ regressions: RegressionRow[] }>("GET", "/regressions"),
  regression: (id: string) => call<Json>("GET", `/regressions/${encodeURIComponent(id)}`),
  createRegression: (regression: Json) =>
    call<{ id: string }>("POST", "/regressions", { regression }),
  runRegression: (id: string, runId: string) =>
    call<InvariantOutcome>("POST", `/regressions/${encodeURIComponent(id)}/run`, {
      run_id: runId,
    }),

  artifacts: () => call<{ artifacts: ArtifactRow[] }>("GET", "/artifacts"),
  artifact: (id: string) => call<Json>("GET", `/artifacts/${encodeURIComponent(id)}`),
};
