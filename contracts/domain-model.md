# Axor Lab — Domain Model (v1)

The canonical entities. Trace/event, tool-manifest, and kernel policy/config identity are **axor-core-shared** (one source of truth for the whole platform); scenario, predicate, experiment, condition, bundle, publication are **Lab-owned**. See architecture-boundary.md.

The canonical entities. Every schema, API, and UI label uses these names and no synonyms. (Terminology for run modes is fixed separately: ungoverned / governed / compare; never undefended/bare in Lab UI — "undefended" survives only as the AgentDojo condition term.)

```
RuntimeRef           a Lab-registered runtime (own axlab_ credential); may carry external_refs.control_plane_runtime_id (identity mapping, not one shared record) — see agent-connection.md
AgentRef             a logical agent identity — may run in several runtimes, change models over time
TraceSource          where a Run's traces come from: runtime | import | demo | offline_runner
AgentSnapshot        the fingerprint/model/version/config actually used in ONE Run
  └ ToolManifest[]   (axor-core-shared) the tools it can call — Lab consumes, does not own

ScenarioVersion      an executable world + criterion (task, inputs, tools, fixtures, injection, predicates)
BenchVersion         an ordered set of ScenarioVersions + report config

Condition            a versioned (enforcement, kernel, policy) object with a config_hash
Experiment           a question bound to: a Bench (or Scenario set) × a set of Conditions × repeats
                     + experiment type (benchmark | game)

Run                  one execution of an Experiment on one AgentArtifact, in one run mode
  └ Trial[]          one (scenario × condition × seed × repeat_index)
      └ Trace        the recorded events + value ledger for that trial (trace/v1)
          └ Decision[]   the gate verdicts inside the trace

EvidenceCase         a view over one Trial's Trace: injection → provenance → gated call → verdict
                     (three modes: observed-ungoverned | counterfactual-policy-replay | observed-governed-twin)
RegressionCase       a pinned (Trace, expected verdict); future versions must surface any change from it

Bundle               everything reproducible for a Run (bundle/v1): scenarios, conditions, manifests,
                     environment, trials, aggregates, hashes, optional signature
Publication          the immutable public record of a Bundle (publication/v1)
  └ Reproduction[]   independent re-runs, each typed: exact_replay | fresh_live | changed_model | changed_kernel
```

## Relationships that matter

- A **Trial** references exactly one **Condition** by id; the Condition decides whether gates enforced. This is why a Condition must be a versioned object — the Trace's verdicts are only meaningful against a known kernel+policy.
- A **Trace**'s value ledger is what makes an **EvidenceCase** possible: a tool-call argument binds to a `value_id`, whose `sources`/`labels` are the provenance. No ledger → no EvidenceCase → Lab degrades to an eval dashboard.
- An **Aggregate** (in the Bundle) names its `unit_of_analysis`; per statistics.md this is the Run/Trial, never a round.
- A **Claim** on a Publication is typed `exactly_replayable` or `statistically_reproducible` (claims.md); a behavioral delta can only be the latter.
- **Provenance is three axes** on a Publication: origin (where it ran) × integrity (unsigned/hash/signed) × reproductions (typed list). Never collapsed to one status.

## The vertical slice these entities must support

```
Fixture (Scenario.fixtures + injection)
  → untrusted value minted into the Trace ledger (value.labels=[untrusted_derived], sources=[external_read])
  → agent tool-call intent (event.arg_bindings maps recipient → that value_id)
  → gate Decision (reads the value's provenance → DENY, projection=untrusted-derived)
  → EvidenceCase (renders the chain from the Trace)
  → replay (recompute the Decision over the frozen Trace → identical)
  → RegressionCase (pin Trace + expected verdict)
  → Bundle (hash it all) → Publication (exact claim + limitations)
```

When this path runs locally on simulated tools, is expressed entirely by the schemas above, and reproduces from the Bundle, the core of Lab exists. Everything else (cloud code, endpoint governance, multi-agent games, population scale) is built outward from this spine.
