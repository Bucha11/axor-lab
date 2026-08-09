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

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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
  model?: string;
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

// ── endpoints ────────────────────────────────────────────────────────────────

export const api = {
  home: () => call<HomePayload>("GET", "/home"),

  suites: () => call<{ suites: SuiteCard[] }>("GET", "/suites"),
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
  connectRuntime: (model: string, agentRef?: string) =>
    call<{ runtime_ref: string; ingest_key: string }>("POST", "/runtimes/connect", {
      model,
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
