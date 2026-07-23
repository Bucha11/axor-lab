import { useApp } from "./store";

// Typed clients for the two lab_server backends. Vite dev-proxies:
//   /api, /e   -> publications/catalog server (python -m lab_server, :8000)
//   /jobs-api  -> runtime-jobs server (--runtime-port 8010), prefix stripped
const JOBS_BASE = "/jobs-api";

// ── publications surface ────────────────────────────────────────────────────

export interface ProvenanceAxes {
  origin: string; // "local" (self-reported upload) | "lab" (lab-executed)
  integrity: string; // "hash_verified" | "signed"
  reproductions: { count: number; verified: number; unverified: number; kinds: string[] };
}

export interface PublicationSummary {
  publication_id: string;
  question: string;
  url: string;
  license?: string;
  provenance: ProvenanceAxes;
}

export interface Claim {
  kind: string; // "exactly_replayable" | "statistically_reproducible"
  text: string;
  [k: string]: unknown;
}

export interface Publication {
  schema_version: string;
  publication_id: string;
  bundle_ref: string;
  question: string;
  immutable: boolean;
  origin: string;
  integrity: string;
  claims: Claim[];
  limitations: string[];
  license: string;
  visibility: string;
  statistics_integrity?: string;
  provenance: ProvenanceAxes; // appended by GET /api/publications/{id}
}

export interface GateDecision {
  verdict: "ALLOW" | "DENY" | string;
  gate?: string;
  reason?: string;
  driving_value_id?: string | null;
  driving_unresolved?: string;
  [k: string]: unknown;
}

export interface TraceEvent {
  seq: number;
  node?: string;
  type: string; // "tool_result" | "tool_call_intent" | "gate_decision" | …
  tool?: string;
  call_id?: string;
  arg_bindings?: Record<string, unknown>;
  produces_value_ids?: string[];
  decision?: GateDecision;
  [k: string]: unknown;
}

export interface Trace {
  schema_version?: string;
  trace_id: string;
  trial?: {
    run_id?: string;
    scenario_id?: string;
    condition_id?: string;
    seed?: number;
    repeat_index?: number;
  };
  producer?: Record<string, unknown>;
  events: TraceEvent[];
  values?: unknown;
}

export interface StatTest {
  name: string;
  vs?: string;
  p?: number;
  status?: string; // "conclusive" | "inconclusive"
  reason?: string;
  paired_n?: number;
  effective_n?: number;
  [k: string]: unknown;
}

export interface Aggregate {
  metric: string;
  condition_id: string;
  estimate: number;
  interval: { method: string; low: number; high: number };
  n: number;
  unit_of_analysis?: string;
  comparison_design?: string;
  test?: StatTest;
  [k: string]: unknown;
}

export interface Bundle {
  bundle_id?: string;
  created?: string;
  scenarios?: Record<string, unknown>[];
  conditions?: Record<string, unknown>[];
  environment?: {
    kernel_version?: string;
    model?: { provider?: string; id?: string; [k: string]: unknown };
    [k: string]: unknown;
  };
  trials?: Record<string, unknown>[];
  aggregates: Aggregate[];
  [k: string]: unknown;
}

// GET /api/publications/{id}/bundle — the versioned reproduction package.
export interface ReproductionPackage {
  schema_version: string;
  publication: Omit<Publication, "provenance">;
  bundle: Bundle;
  traces: Trace[];
  receipt: Record<string, unknown>;
  acceptance: Record<string, unknown>;
}

// ── runtime-jobs surface ────────────────────────────────────────────────────

export interface RuntimeInfo {
  runtime_ref: string;
  agent_ref: string | null;
  model: string;
  status: string;
}

export interface ConnectResult {
  runtime_ref: string;
  ingest_key: string;
}

export interface PlanEstimate {
  trials: number;
  scenarios: number;
  conditions: number;
  repeats: number;
}

export interface PlanResult {
  trials: string[];
  estimate: PlanEstimate;
}

export interface CreateRunResult {
  run_id: string;
  state: string;
  estimate: Record<string, unknown>;
}

export interface TrialStatus {
  trial_id: string;
  status: "pending" | "completed" | "failed" | string;
  attempt: number;
  superseded: number;
  events: number;
  has_trace: boolean;
}

// The connected_runtime lifecycle (runtime_jobs.py):
//   validating -> waiting_for_runtime -> running -> receiving_traces
//   -> analyzing -> completed   (+ awaiting_confirmation before start)
export interface RunResults {
  run_id: string;
  state: string;
  planned_trials: string[];
  estimate: Record<string, number>;
  trials: TrialStatus[];
  traces: Trace[];
  aggregates: Aggregate[];
}

export interface ValidateResult {
  ok: boolean;
  errors: string[];
}

// ── wrap surface (POST /wrap/*, axor-wrap engine behind the jobs server) ────

export type EffectClass = "READ" | "WRITE" | "EXPORT" | "EXEC";

export interface WrapEffectGuess {
  default_class: EffectClass | "UNKNOWN";
  confidence: "high" | "medium" | "low" | string;
  reason: string;
  driving_args: string[];
  untrusted_fields: string[];
}

// one statically detected tool candidate (axor_wrap.detect.DetectedTool + guess)
export interface WrapDetectedTool {
  id: string;
  source: string; // "<file>:<line> <detector kind>"
  description: string;
  args_schema: Record<string, unknown>;
  framework: string; // langchain | mcp | anthropic | implicit
  schema_confidence: "high" | "low" | string;
  guess: WrapEffectGuess;
}

// what the human-reviewed classification posts back to /wrap/manifests
export interface WrapReviewedTool {
  id: string;
  source: string;
  description: string;
  args_schema: Record<string, unknown>;
  framework: string;
  schema_confidence: string;
  effect: {
    default_class: EffectClass;
    driving_args: string[];
    untrusted_fields: string[];
    sensitive_fields: string[];
  };
}

export interface WrapManifestsResult {
  manifests: Record<string, unknown>[]; // tool-manifest/v1, validated server-side
  governance_yaml: string;
  wrap: {
    generated_by: string;
    manifest_schema: string;
    tools: number;
    egress_sinks: string[];
    untrusted_sources: string[];
    sensitive_sources: string[];
    driving_args: Record<string, string[]>;
  };
}

// ── plumbing ────────────────────────────────────────────────────────────────

async function j<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = "";
    try {
      const body = (await resp.json()) as { error?: string };
      detail = body.error ?? "";
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${resp.status}${detail ? ` — ${detail}` : ""}`);
  }
  return resp.json() as Promise<T>;
}

// The runtime-jobs CONTROL surface may be token-gated (--control-token). The
// token is read from the store at call time; when the server runs open it is
// empty and the header is omitted.
function jf(path: string, init: RequestInit = {}): Promise<Response> {
  const token = useApp.getState().controlToken;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(JOBS_BASE + path, { ...init, headers });
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  // ── publications ──────────────────────────────────────────────────────────
  listPublications: () =>
    fetch("/api/publications").then((r) =>
      j<{ publications: PublicationSummary[] }>(r).then((b) => b.publications),
    ),
  getPublication: (publicationId: string) =>
    fetch(`/api/publications/${encodeURIComponent(publicationId)}`).then((r) =>
      j<Publication>(r),
    ),
  getBundle: (publicationId: string) =>
    fetch(`/api/publications/${encodeURIComponent(publicationId)}/bundle`).then((r) =>
      j<ReproductionPackage>(r),
    ),

  // ── runtime jobs (control surface) ────────────────────────────────────────
  connectRuntime: (model: string, agentRef?: string) =>
    jf("/runtimes/connect", post({ model, agent_ref: agentRef || null })).then((r) =>
      j<ConnectResult>(r),
    ),
  listRuntimes: () =>
    jf("/runtimes").then((r) =>
      j<{ runtimes: RuntimeInfo[] }>(r).then((b) => b.runtimes),
    ),
  validateScenario: (
    scenario: Record<string, unknown>,
    manifests: Record<string, Record<string, unknown>>,
  ) =>
    jf("/scenarios/validate", post({ scenario, manifests })).then((r) =>
      j<ValidateResult>(r),
    ),
  planExperiment: (experiment: Record<string, unknown>) =>
    jf("/experiments/plan", post({ experiment })).then((r) => j<PlanResult>(r)),
  createRun: (
    runtimeRef: string,
    experiment: Record<string, unknown>,
    plannedTrials: string[],
    estimate?: Record<string, unknown>,
  ) =>
    jf(
      "/runs",
      post({
        runtime_ref: runtimeRef,
        experiment,
        planned_trials: plannedTrials,
        ...(estimate ? { estimate } : {}),
      }),
    ).then((r) => j<CreateRunResult>(r)),
  confirmRun: (runId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/confirm`, post({})).then((r) =>
      j<{ run_id: string; state: string }>(r),
    ),
  runState: (runId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}`).then((r) =>
      j<{ run_id: string; state: string }>(r),
    ),
  runResults: (runId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/results`).then((r) => j<RunResults>(r)),
  trialTrace: (runId: string, trialId: string) =>
    jf(
      `/runs/${encodeURIComponent(runId)}/trials/${encodeURIComponent(trialId)}/trace`,
    ).then((r) => j<Trace>(r)),

  // ── wrap flow (upload agent code → scan → reviewed manifests) ─────────────
  wrapScan: (files: { path: string; content: string }[]) =>
    jf("/wrap/scan", post({ files })).then((r) =>
      j<{ tools: WrapDetectedTool[] }>(r).then((b) => b.tools),
    ),
  wrapManifests: (tools: WrapReviewedTool[]) =>
    jf("/wrap/manifests", post({ tools })).then((r) => j<WrapManifestsResult>(r)),
};
