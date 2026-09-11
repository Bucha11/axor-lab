# Axor Lab — Domain Model (v1)

The canonical entities. Trace/event, tool-manifest, and kernel policy/config identity are **axor-core-shared** (one source of truth for the whole platform); suite, scenario, predicate, experiment, condition, bundle, artifact, evidence-case, regression, publication are **Lab-owned**. See architecture-boundary.md.

The canonical entities. Every schema, API, and UI label uses these names and no synonyms. (Terminology for run modes is fixed separately: ungoverned / governed / compare; never undefended/bare in Lab UI — "undefended" survives only as the AgentDojo condition term.)

## The platform spine (Suite Platform RFC §3)

```
Experiment Suite → Experiment Run → Trials → Observations/Traces → EvidenceCases → Regressions → Artifacts
```

**Governance is a capability, not a stage of this chain.** A Suite opts into it by declaring `governance` in `capabilities` and supplying `execution.conditions`; a Suite that declares neither runs single-arm with no kernel involved, and that is the common case. Everything the governance capability owns — Condition, gate Decision, verdict replay, the paired comparison and the Control Plane bridge — hangs off that opt-in and is marked below.

```
Suite                the executable specification of an experiment, and the CORE abstraction (suite/v1).
                     Scenarios, agents, environment/tools, execution strategy, evaluators, metrics,
                     aggregations, artifact layout, regression rules. ONE portable manifest — the
                     Builder's Basic/Advanced/YAML modes all edit this same document.
RuntimeRef           a connected execution environment (the Axor adapter that runs the agent + pushes traces)
AgentRef             a logical agent identity — may run in several runtimes, change models over time
TraceSource          where a Run's traces come from: runtime | import | demo | offline_runner
                     (there is deliberately no `endpoint` member — Lab never calls an agent out;
                     see the Suite Platform integration plan §4.6)
AgentSnapshot        the fingerprint/model/version/config actually used in ONE Run
  └ ToolManifest[]   (axor-core-shared) the tools it can call — Lab consumes, does not own

ScenarioVersion      an executable world + criterion (task, inputs, tools, fixtures, predicates).
                     `task_success` is required — a scenario that cannot say what success means is a
                     prompt, not an experiment. `injection` + `violation` are OPTIONAL and travel
                     together: they describe an ATTACK scenario. A budget/performance/reliability
                     scenario has neither.
BenchVersion         an ordered set of ScenarioVersions + report config

Evaluator            a per-trial judgement declared by the Suite: a typed predicate, a trial metric,
                     or a Suite SDK hook. Produces a named value metrics and regressions can read.
Metric               a named quantity the Suite reports. Per-trial metrics are recorded ON the trial
                     (duration_ms, steps, tokens, cost_usd, + any suite-defined key); an UNMEASURED
                     metric is absent, never 0.
Aggregation          how a Metric is summarised across trials (fn, unit_of_analysis, interval, test).
                     A comparison TEST is something a Suite asks for here — never a platform default.

Condition            [governance] a versioned (enforcement, kernel, policy) object with a config_hash
Experiment           a question bound to: a Bench (or Scenario set) × repeats × an agent,
                     resolved from a Suite (suite_ref) + experiment type (benchmark | game).
                     Conditions are OPTIONAL: none = a single-arm run (execute and observe);
                     one = one pinned configuration; ≥2 = a comparison.

Run                  one execution of an Experiment on one AgentArtifact
  └ Trial[]          one (scenario × condition × seed × repeat_index), carrying its own metrics
      └ Trace        the recorded events + value ledger for that trial (trace/v1)
          └ Decision[]   [governance] the gate verdicts inside the trace

EvidenceCase         a curated investigation of ONE Trial (evidence-case/v1). GENERIC: latency_spike,
                     hallucination, planner_failure, budget_overflow, secret_leakage, … `kind` is an
                     open string so a suite can register its own. A view over the recorded trace — it
                     never carries its own copy of the events, so it cannot drift from what ran.
  └ governance{}     [governance] the injection → provenance → gated call → verdict chain, plus the
                     three modes (observed-ungoverned | counterfactual-policy-replay |
                     observed-governed-twin). One kind of case among many, not the definition.
Regression           an EXECUTABLE INVARIANT derived from a Trial (regression/v1). Four rule kinds:
                     predicate | metric_threshold | evaluator_outcome | [governance] verdict_sequence.
                     `expectation` is prose for humans and is never evaluated; `rule` is what runs.

Bundle               everything reproducible for a Run (bundle/v1): scenarios, conditions, manifests,
                     environment, trials + their metrics, aggregates, hashes, optional signature
Artifact             the portable output and the USER-FACING noun (artifact/v1) — the UI says Artifact,
                     never "bundle". WRAPS a bundle/v1 body verbatim and adds suite, agent identity,
                     EvidenceCases, regressions, reproduce instructions. Wrapping (not flattening) is
                     what keeps every already-published bundle hash valid.
Publication          the immutable public record of a Bundle (publication/v1)
  └ Reproduction[]   independent re-runs, each typed: exact_replay | fresh_live | changed_model | changed_kernel
```

Entries marked **[governance]** exist only for a Suite that opted into the governance capability.

## Relationships that matter

- A **Suite** is the one editable document. Every Builder field must have a home in `suite/v1`, or Basic mode owns state the YAML mode cannot see — the modes would silently disagree about what the experiment is.
- A **Trial** in a governed run references exactly one **Condition** by id; the Condition decides whether gates enforced. This is why a Condition must be a versioned object — the Trace's verdicts are only meaningful against a known kernel+policy. In a single-arm run there is no Condition and no verdict to be meaningful about.
- A **Trace**'s value ledger is what makes a *governance* **EvidenceCase** possible: a tool-call argument binds to a `value_id`, whose `sources`/`labels` are the provenance. No ledger → no provenance chain. Other EvidenceCase kinds (latency, budget, planner failure) read the trace's events and the trial's metrics instead, and stay available without a ledger.
- An **Aggregate** (in the Bundle) names its `unit_of_analysis`; per statistics.md this is the Run/Trial, never a round. It is RECOMPUTED from the trials and their metrics, never trusted from an uploader.
- A **Regression**'s `rule` is the only executable part. `expectation` is prose and is never parsed; where the two disagree, `rule` is what ran. A `metric_threshold` rule over an ABSENT metric is an `error` outcome, never a pass — an unmeasured latency is not a fast one.
- An **Artifact** embeds its Bundle rather than restating it, so `content_hash(artifact.bundle)` equals the hash that Bundle always had and no published hash is invalidated by the migration.
- A **Claim** on a Publication is typed `exactly_replayable` or `statistically_reproducible` (claims.md); a behavioral delta can only be the latter.
- **Provenance is three axes** on a Publication: origin (where it ran) × integrity (unsigned/hash/signed) × reproductions (typed list). Never collapsed to one status.

## The two slices these entities must support

**The platform slice — no governance anywhere in it.** This is the primary workflow (RFC §15) and the one the schemas used to make unrepresentable:

```
Suite (scenarios + agent + execution + evaluators/metrics)
  → Run → Trial[] (each recording duration_ms / steps / tokens / cost_usd)
  → Trace (the recorded events)
  → EvidenceCase (kind=latency_spike — a view over one trial, no ledger needed)
  → Regression (metric_threshold: duration_ms p95 < 10000)
  → Artifact (suite + trials + metrics + evidence + regressions + reproduce)
```

**The governance slice** — everything above still holds, plus the capability's own chain:

```
Fixture (Scenario.fixtures + injection)
  → untrusted value minted into the Trace ledger (value.labels=[untrusted_derived], sources=[external_read])
  → agent tool-call intent (event.arg_bindings maps recipient → that value_id)
  → gate Decision (reads the value's provenance → DENY, projection=untrusted-derived)
  → EvidenceCase (kind=prompt_injection, governance.chain renders it from the Trace)
  → replay (recompute the Decision over the frozen Trace → identical)
  → Regression (rule kind=verdict_sequence: pin Trace + expected verdict sequence)
  → Bundle (hash it all) → Artifact / Publication (exact claim + limitations)
```

Both paths run locally on simulated tools, are expressed entirely by the schemas above, and reproduce from the Bundle. The governance slice is the one the repo implements end-to-end today; the platform slice is what the schemas now permit and the runner is being opened up to (see `docs/spec-suite-platform/INTEGRATION_PLAN.md`, Phase 2). Everything else (cloud code, multi-agent topologies, population scale) is built outward from this spine.
