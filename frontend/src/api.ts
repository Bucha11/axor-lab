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

// ── incidents surface (Control Plane → Lab cross-link) ──────────────────────

// the axor-lab-incident/v1 envelope the Control Plane's "Open in Lab" ships
export interface IncidentPackage {
  schema_version: string; // "axor-lab-incident/v1"
  trace: Trace;
  scenario: { name?: string; task?: string; [k: string]: unknown };
  manifests: Record<string, unknown>[];
  condition: {
    id?: string;
    kernel?: string;
    enforcement?: string;
    policy?: Record<string, unknown>;
    config_hash?: string;
    [k: string]: unknown;
  };
  source?: { product?: string; run_id?: string; url?: string };
  // the producer's honest per-gate replay-fidelity statement: the reference
  // taint_floor verdict reproduces faithfully; content gates (ssrf, value_policy)
  // are not reproducible because the Control Plane records observations, not bodies
  replay_fidelity?: {
    backend?: string;
    recorded_kernel?: string;
    reproducible_gates?: string[];
    not_reproducible_gates?: string[];
    note?: string;
  };
}

// POST /api/incidents → 201
export interface IncidentImportResult {
  incident_id: string;
  trace_id: string;
  replay: string; // "match"
  url: string; // "/i/{incident_id}" (UI route #/i/{incident_id})
}

// one side of the 422 replay-mismatch detail: verdict cores, recorded vs recomputed
export interface VerdictCore {
  verdict?: string;
  gate?: string;
  driving_value_id?: string | null;
}

export interface ReplayMismatchDetail {
  status: string; // "mismatch" | "malformed_trace" | …
  recorded_verdicts: VerdictCore[];
  recomputed_verdicts: VerdictCore[];
}

// thrown by importIncident so the UI can show the divergence honestly
export class IncidentImportError extends Error {
  status: number;
  replay?: ReplayMismatchDetail;
  constructor(status: number, message: string, replay?: ReplayMismatchDetail) {
    super(message);
    this.status = status;
    this.replay = replay;
  }
}

export interface IncidentSummary {
  incident_id: string;
  trace_id: string;
  scenario_id: string;
  source?: { product?: string; run_id?: string; url?: string } | null;
  imported_at: string;
  approved?: boolean; // set on the hosted listing once an approval is recorded
  pinned?: boolean; // set once the incident is pinned into the regression corpus
}

export interface RegressionPin {
  trace_id: string;
  incident_id: string;
  side: string; // "must_block" | "must_pass"
  expected_verdict: string;
  expected_sequence: string[];
  pinned_at: string;
}

export interface RegressionReport {
  rows: {
    trace_id: string;
    incident_id?: string;
    side: string;
    outcome: string; // held | passed | regressed | escaped | skipped
    status?: string;
    expected?: unknown;
    actual?: unknown;
  }[];
  held: number;
  passed: number;
  regressed: number;
  escaped: number;
  skipped: number;
  safe_to_ship: boolean;
}

// ── workspace entitlement + paid Security features ──────────────────────────
export interface LicenseStatus {
  active: boolean;
  organization?: string;
  workspace_tier?: string; // "community" | "team" | "security"
  modules?: { private_lab: boolean; control_plane: boolean };
  governed_node_ceiling?: number;
  self_hosted_runner?: boolean;
  expires_at?: string;
}

export interface AuditEntry {
  seq: number;
  ts: string;
  action: string;
  actor: string;
  target?: string;
  detail?: Record<string, unknown>;
}

export interface ApprovalResult {
  incident_id: string;
  approved: boolean;
  approval: AuditEntry;
}

export interface ComplianceReport {
  schema_version: string;
  window: { since: string | null; until: string | null };
  generated_at: string;
  action_counts: Record<string, number>;
  total_events: number;
  incidents: {
    incident_id: string;
    trace_id?: string;
    imported_at?: string;
    approved: boolean;
    approvals: { actor?: string; ts?: string; note?: string }[];
  }[];
}

// GET /api/incidents/{id} — the accepted envelope + the built bundle
export interface Incident extends IncidentPackage {
  incident_id: string;
  imported_at: string;
  bundle: Bundle;
}

// GET /api/traces/{trace_id} — where does this trace live?
export interface TraceResolution {
  trace_id: string;
  publications: string[];
  incidents: string[];
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

// The REAL experiment menu (GET /catalog), served from the code that owns the
// scenarios. The builder used to invent its own suite list client-side with
// fabricated scenario ids, so nothing it composed could run.
export interface CatalogScenario {
  name: string;
  task: string;
}

export interface CatalogSuite {
  id: string;
  label: string;
  source: string;
  scenarios: CatalogScenario[];
}

export interface Catalog {
  suites: CatalogSuite[];
  conditions: { id: string; policy: Record<string, unknown> | null; baseline: boolean }[];
  kernel: string;
  // the production governor, when the server has it installed. Absent on an
  // older server, so every read of it must tolerate undefined.
  real_kernel?: { available: boolean; version: string | null };
  agent: { ref: string; deterministic: boolean; note: string };
  repeats: { default: number; max: number; min_powered: number };
}

export interface ComposeResult {
  document: Record<string, unknown>;
  planned_trials: string[];
  estimate: { trials: number; scenarios: number; conditions: number; repeats: number };
}

export interface ReplayUploadReport {
  // "reproduced" every trace matched · "diverged" recomputed verdicts differ (or
  // a trace is malformed) · "not_attempted" the pinned kernel is absent here, so
  // nothing was replayed — which is NOT the same as verdicts differing.
  outcome: "reproduced" | "diverged" | "not_attempted";
  bit_identical: boolean;
  traces: number;
  decisions: number;
  deny: number;
  allow: number;
  statuses: { trace_id: string; status: string; verdicts: string[] }[];
  claim: string;
}

export interface RunTraceEntry {
  trace_id: string;
  scenario_id: string;
  condition_id: string;
  seed: string;
  repeat_index: number | null;
  verdicts: string[];
  denied: boolean;
}

export interface RegressionPinBody {
  trace_id: string;
  trace_ref: string;
  expected_verdict: string;
  expected_sequence: string[];
}

export interface RunPinResult {
  pin: RegressionPinBody;
  scenario_id: string;
  condition_id: string;
  kernel: string[];
}

export interface CpExportResult {
  config: Record<string, unknown>;
  production_todo: string;
  // whether the deployed config's advantage is statistically EARNED, not merely
  // observed — the two must never be presented the same way
  earned_bridge: boolean;
}

export interface CpExportTree {
  // every file of the export directory, keyed by its relative path
  files: Record<string, string>;
  manifest: Record<string, unknown> & { author?: string; signature?: string };
  // false means genuinely unsigned — integrity and derivability only, with
  // nothing saying WHO vouches for it. It is never quietly true.
  signed: boolean;
  signature_note: string;
  file_count: number;
}

export interface ComposeSpec {
  suite: string;
  conditions?: string[];
  repeats?: number;
  // which of the declared conditions actually run. "ungoverned" is the run with
  // no gate in it at all — governance is something you add here, so it has to be
  // possible to leave out.
  run_mode?: "compare" | "ungoverned" | "governed";
  // repin every condition to the installed axor-core governor instead of the
  // stdlib reference kernel — baseline included, so the compare isolates
  // enforcement rather than mixing in a kernel change.
  real_kernel?: boolean;
}

// A run the SERVER executed (POST /runs/local) rather than a runtime. It arrives
// already completed, so there is no progress to follow — `executed: "local"` is
// the honest record of who ran it.
export interface LocalRunResult {
  run_id: string;
  state: string;
  trials: number;
  aggregates: number;
  missingness: string;
  by_status: Record<string, number>;
  executed: string;
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

// GET /runs/{id}/bundle — a publishable bundle assembled from a COMPLETED run.
// traces are keyed by trace_id, the exact shape POST /api/publications expects.
export interface RunBundle {
  bundle: Bundle;
  traces: Record<string, Trace>;
}

// POST /api/publications → 201
export interface PublishResult {
  publication_id: string;
  url: string; // "/e/{id}" (UI route #/e/{id})
  integrity: string; // "hash_verified" | "signed"
  acceptance?: Record<string, unknown>;
}

// thrown by publishBundle so the UI can show 402/replay-mismatch honestly. The
// publish handshake rejects with the SERVER's own reason: a replay mismatch, a
// content-hash failure, a 402 entitlement message, or a 409 takedown.
export class PublishError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
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

export interface BenchRates { base: number; governed: number; unmapped: number }
export interface BenchRow {
  suite: string;
  utility: BenchRates;
  asr: BenchRates;
  denials: number;
  denied_tasks: string[];
  by_gate: Record<string, number>;
  reference_denials: string;
  note: string;
}
export interface BenchSuite {
  suite: string;
  note: string;
  user_tasks: number;
  injection_tasks: number;
  tools: string[];
  secret_candidates: string[];
  default_secrets: string[];
  egress_sinks: Record<string, string[]>;
  untrusted_sources: string[];
  reference_denials: string;
}
export interface BenchmarkEntry {
  benchmark: string;
  title: string;
  source: string;
  description: string;
  suites: BenchSuite[];
}
export interface SweepReport {
  suite: string;
  baseline: { utility: BenchRates; asr: BenchRates; denials: number };
  rows: SweepRow[];
  combined: { utility: BenchRates; asr: BenchRates; denials: number };
}
export interface SweepRow {
  source: string;
  utility: BenchRates;
  asr: BenchRates;
  denials: number;
  cost_pp: number;
}

// ── incident RECONSTRUCTION (the pre-Axor path) ─────────────────────────────
//
// The sibling of importIncident, and deliberately a different thing. An Axor
// trace/v1 carries the value ledger and arg_bindings a verdict is computed
// from, so it REPLAYS exactly. A LangSmith / OTel / application-log export
// carries neither, so nothing can be replayed from it — it is read to AUTHOR a
// scenario, which is then run under Axor for a genuine trace.
export interface ReconstructionFinding {
  kind: string;   // task | untrusted_source | injection | sink | linked_value
  detail: string;
  where: string;
  confidence: string;
}

export interface ObservedCall {
  index: number;
  tool: string;
  args: Record<string, unknown>;
  result: unknown;
}

export interface Reconstruction {
  fidelity: string;  // always "heuristic_attribution" — never presented as sound
  scenario: Record<string, unknown>;
  manifests: Record<string, unknown>[];
  observed_calls: ObservedCall[];
  findings: ReconstructionFinding[];
  unresolved: string[];
  note: string;
}


export const api = {
  // ── the governance benchmark ──────────────────────────────────────────────
  benchIndex: () =>
    jf("/benchmarks").then((r) =>
      j<{ default: string; benchmarks: BenchmarkEntry[] }>(r),
    ),
  benchRun: (body: {
    benchmark?: string;
    suites?: string[];
    allowlist?: boolean;
    confidentiality?: boolean;
    secrets?: Record<string, string[]>;
  }) =>
    jf("/benchmarks/run", post(body)).then((r) =>
      j<{ benchmark: string; source: string; rows: BenchRow[] }>(r),
    ),
  benchSweep: (suite: string, allowlist = false, benchmark?: string) =>
    jf("/benchmarks/sweep", post({ suite, allowlist, benchmark })).then((r) =>
      j<SweepReport>(r),
    ),

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

  // ── incidents (Control Plane → Lab) ──────────────────────────────────────
  // POST is write-token-gated (--write-token on the publications server); the
  // token comes from the store like the runtime-jobs control token does.
  // Draft a scenario from a NON-Axor recording. Nothing is stored, nothing is
  // executed and no verdict is produced — the run happens later, from the
  // CONFIRMED draft, through the ordinary local-run path below.
  reconstructIncident: (trace: unknown, name?: string) =>
    jf("/incidents/reconstruct", post({ trace, name })).then((r) => j<Reconstruction>(r)),
  runReconstructed: (
    scenario: Record<string, unknown>,
    manifests: Record<string, unknown>[],
    governed: boolean,
    repeats: number,
  ) =>
    jf("/runs/local", post({
      reconstructed: { scenario, manifests, repeats, governed },
    })).then((r) => j<LocalRunResult>(r)),

  importIncident: async (pkg: IncidentPackage): Promise<IncidentImportResult> => {
    const token = useApp.getState().writeToken;
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch("/api/incidents", {
      method: "POST", headers, body: JSON.stringify(pkg),
    });
    let body: { error?: string; replay?: ReplayMismatchDetail } & Partial<IncidentImportResult> = {};
    try {
      body = (await r.json()) as typeof body;
    } catch {
      /* non-JSON error body */
    }
    if (!r.ok) {
      throw new IncidentImportError(r.status, body.error ?? `${r.status}`, body.replay);
    }
    return body as IncidentImportResult;
  },
  listIncidents: () =>
    fetch("/api/incidents").then((r) =>
      j<{ incidents: IncidentSummary[] }>(r).then((b) => b.incidents),
    ),
  getIncident: (incidentId: string) =>
    fetch(`/api/incidents/${encodeURIComponent(incidentId)}`).then((r) => j<Incident>(r)),
  resolveTrace: (traceId: string) =>
    fetch(`/api/traces/${encodeURIComponent(traceId)}`).then((r) => j<TraceResolution>(r)),

  // ── workspace entitlement + paid Security features ────────────────────────
  licenseStatus: () =>
    fetch("/api/license/status").then((r) => j<LicenseStatus>(r)),
  auditLog: () =>
    fetch("/api/audit").then((r) => j<{ events: AuditEntry[] }>(r).then((b) => b.events)),
  complianceReport: () =>
    fetch("/api/compliance/report").then((r) => j<ComplianceReport>(r)),
  // approve is a WRITE — it carries the write token like importIncident
  approveIncident: async (
    incidentId: string, approver: string, note: string,
  ): Promise<ApprovalResult> => {
    const token = useApp.getState().writeToken;
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}/approve`, {
      method: "POST", headers, body: JSON.stringify({ approver, note }),
    });
    return j<ApprovalResult>(r);
  },
  // pin the incident's verdict into the regression corpus (write token)
  pinIncident: async (incidentId: string): Promise<{ pinned: boolean; pin: RegressionPin }> => {
    const token = useApp.getState().writeToken;
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}/pin`, {
      method: "POST", headers, body: "{}",
    });
    return j<{ pinned: boolean; pin: RegressionPin }>(r);
  },
  regressionCorpus: () =>
    fetch("/api/regression").then((r) => j<{ pins: RegressionPin[] }>(r).then((b) => b.pins)),
  runRegression: () =>
    fetch("/api/regression/run", { method: "POST" }).then((r) => j<RegressionReport>(r)),

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
  // Execute an experiment IN THE SERVER and land it as a completed run. No
  // runtime, no provider, no CLI — the bundled example is offline (scripted
  // agent, reference kernel, simulated tools), so this is the shortest honest
  // path to a first result. Omit `experiment` to run the bundled example.
  // The server refuses anything that would need a live model (409) or exceed its
  // trial ceiling (413); a run that costs money stays with the operator's CLI.
  runLocal: (experiment?: Record<string, unknown>) =>
    jf("/runs/local", post(experiment ? { experiment } : {})).then((r) =>
      j<LocalRunResult>(r),
    ),
  // One round trip for the builder's Run button: compose the selection, run it.
  runComposed: (spec: ComposeSpec) =>
    jf("/runs/local", post({ compose: spec })).then((r) => j<LocalRunResult>(r)),
  catalog: () => jf("/catalog").then((r) => j<Catalog>(r)),
  // Replay someone's bundle server-side. `outcome` separates the two answers a
  // bare bit_identical conflates: verdicts that differ, versus a bundle pinned to
  // a kernel this server does not have and therefore never replayed at all.
  replayUpload: (bundle: Record<string, unknown>, traces: unknown) =>
    jf("/replay", post({ bundle, traces })).then((r) => j<ReplayUploadReport>(r)),

  // ── working over YOUR OWN run, before (or without) publishing it ───────────
  // The chooser: which trace, and which ones were denied. Finding the one
  // interesting trace in a 60-trace run otherwise means opening each.
  runTraces: (runId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/traces`).then((r) =>
      j<{ traces: RunTraceEntry[] }>(r)),
  // The same EvidenceCase the CLI and the published page render. Investigation
  // is what decides whether a run is worth publishing, so it cannot require
  // publishing first.
  runEvidence: (runId: string, traceId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(traceId)}`)
      .then((r) => j<Record<string, unknown>>(r)),
  // Pin one of your own traces as a regression case. Web pinning used to need an
  // imported production incident to exist first.
  pinRunTrace: (runId: string, traceId: string, expected?: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/pin`,
       post({ trace_id: traceId, ...(expected ? { expected } : {}) }))
      .then((r) => j<RunPinResult>(r)),
  // The Lab → Control Plane handoff, which had no web path at all.
  cpExport: (runId: string, regressions?: RegressionPinBody[], conditionId?: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/cp-export`, post({
      ...(regressions?.length ? { regressions } : {}),
      ...(conditionId ? { condition_id: conditionId } : {}),
    })).then((r) => j<CpExportResult>(r)),
  // The whole export TREE, and — when the server has a Control Plane configured —
  // its manifest signed by CP's vault. The vault signs and never surrenders, so
  // no key touches this server or your browser. No vault → an honestly UNSIGNED
  // tree, never one that quietly claims an authority it does not have.
  cpExportTree: (
    runId: string, regressions?: RegressionPinBody[],
    signing?: { operator: string; key_id: string }, conditionId?: string,
  ) =>
    jf(`/runs/${encodeURIComponent(runId)}/cp-export`, post({
      tree: true,
      ...(regressions?.length ? { regressions } : {}),
      ...(conditionId ? { condition_id: conditionId } : {}),
      ...(signing ? { signing } : {}),
    })).then((r) => j<CpExportTree>(r)),
  // Compose without running — the advanced level shows the .axl this produces.
  composeExperiment: (spec: ComposeSpec) =>
    jf("/experiments/compose", post(spec)).then((r) => j<ComposeResult>(r)),
  runState: (runId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}`).then((r) =>
      j<{ run_id: string; state: string }>(r),
    ),
  runResults: (runId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/results`).then((r) => j<RunResults>(r)),
  // assemble the publishable bundle from a completed run (control-token gated,
  // like the other /runs/* control-surface reads)
  runBundle: (runId: string) =>
    jf(`/runs/${encodeURIComponent(runId)}/bundle`).then((r) => j<RunBundle>(r)),
  // publish an assembled bundle — the server RE-VERIFIES (content hashes +
  // bit-identical replay + statistical recomputation) before minting. Write-token
  // gated on the publications server, like importIncident.
  publishBundle: async (
    bundle: Bundle,
    traces: Record<string, Trace>,
    question: string,
    visibility: "public" | "unlisted" = "unlisted",
  ): Promise<PublishResult> => {
    const token = useApp.getState().writeToken;
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch("/api/publications", {
      method: "POST",
      headers,
      body: JSON.stringify({ bundle, traces, question, visibility }),
    });
    let body: { error?: string } & Partial<PublishResult> = {};
    try {
      body = (await r.json()) as typeof body;
    } catch {
      /* non-JSON error body */
    }
    if (!r.ok) {
      throw new PublishError(r.status, body.error ?? `${r.status}`);
    }
    return body as PublishResult;
  },
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
