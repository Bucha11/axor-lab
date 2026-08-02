# Axor Lab RFC --- Experiment Suite Platform

Version: 0.1 (Concept Draft)

# 1. Vision

Axor Lab is a reproducible experiment platform for AI agents.

It is **not** primarily: - a governance product, - an A/B testing
framework, - a benchmark collection.

Instead, it is a platform for designing, executing, investigating and
reproducing arbitrary experiment suites.

Governance is an optional capability that experiment suites may use.

------------------------------------------------------------------------

# 2. Product Thesis

Users should be able to:

-   bring an agent;
-   bring or author an experiment suite;
-   execute experiments;
-   inspect any trial;
-   create EvidenceCases;
-   preserve regressions;
-   export reproducible artifacts.

Every other capability builds on top of this workflow.

------------------------------------------------------------------------

# 3. Core Domain Model

``` text
Experiment Suite
        ↓
Experiment Run
        ↓
Trials
        ↓
Observations / Traces
        ↓
EvidenceCases
        ↓
Regressions
        ↓
Artifacts
```

Definitions:

-   Experiment Suite --- executable specification of an experiment.
-   Experiment Run --- one execution of a suite.
-   Trial --- smallest independent execution.
-   Observation --- events collected during execution.
-   EvidenceCase --- curated investigation of a trial.
-   Regression --- executable invariant derived from a trial.
-   Artifact --- portable, reproducible output.

------------------------------------------------------------------------

# 4. Design Principles

1.  Bring your own agent.
2.  Bring your own suite.
3.  Bring your own traces.
4.  Reproducibility is first-class.
5.  Evidence is extracted from trials.
6.  Regressions are executable.
7.  Governance is optional.
8.  Multi-agent is a natural extension.

------------------------------------------------------------------------

# 5. Inputs

An experiment may consume:

-   live agent execution;
-   remote endpoint;
-   uploaded traces;
-   recorded bundles;
-   simulated environments.

Execution is only one producer of observations.

------------------------------------------------------------------------

# 6. Experiment Suite

A suite defines:

-   scenarios;
-   agents;
-   environment;
-   tools;
-   execution strategy;
-   evaluators;
-   metrics;
-   aggregations;
-   artifact layout;
-   regression rules.

Example suites:

-   AgentDojo
-   Prompt Injection
-   Performance
-   Budget
-   Reliability
-   Planning
-   Negotiation
-   Multi-Agent Coordination
-   Organization-specific suites

Suites may compare variants, but comparison is optional.

------------------------------------------------------------------------

# 7. Platform Responsibilities

Execution: - runners - adapters - endpoint execution - retries - seeds -
concurrency - budgets - simulators

Observation: - traces - tool calls - model calls - latency - cost -
structured events - optional provenance

Investigation: - trial browser - timeline - EvidenceCase creation -
regression pinning

Reproducibility: - replay - signatures - portable artifacts -
environment capture - verification

------------------------------------------------------------------------

# 8. EvidenceCases

EvidenceCases are generic investigations.

Examples:

-   prompt injection
-   hallucination
-   latency spike
-   planner failure
-   budget overflow
-   consensus failure
-   secret leakage
-   unexpected recovery

They are not limited to security.

------------------------------------------------------------------------

# 9. Regressions

Regression = executable invariant.

Examples:

-   task_success == true
-   latency \< threshold
-   budget \<= limit
-   forbidden_tool_calls == 0
-   consensus reached
-   gate verdict sequence
-   custom evaluator outcome

------------------------------------------------------------------------

# 10. Governance Capability

Governance is an optional plugin.

Possible capabilities:

-   provenance
-   policy gates
-   deterministic verdict replay
-   policy comparison
-   Control Plane export

Suites may ignore governance completely.

------------------------------------------------------------------------

# 11. Multi-Agent

The platform is agent-count agnostic.

Example topologies:

-   single agent
-   planner/workers
-   reviewer pipelines
-   negotiations
-   swarms
-   attacker/defender games

Only the suite changes.

------------------------------------------------------------------------

# 12. Suite SDK

A suite should expose:

-   config schema
-   UI schema
-   validators
-   execution hooks
-   metrics
-   artifact renderer
-   regression extractor
-   optional EvidenceCase helpers

------------------------------------------------------------------------

# 13. Suite Builder

The Builder is the primary commercial UX.

Sections:

1.  Agents
2.  Scenarios
3.  Environment & Tools
4.  Execution
5.  Evaluation
6.  Artifact

Every suite contributes declarative schemas.

Modes:

-   Basic
-   Advanced
-   YAML / Code

All modes edit the same portable manifest.

Preview should support a single-trial debugger before full execution.

------------------------------------------------------------------------

# 14. Artifact

Artifact contains:

-   suite
-   configuration
-   environment
-   agent identity
-   observations
-   traces
-   metrics
-   EvidenceCases
-   regressions
-   reproduce instructions
-   signatures
-   hashes

------------------------------------------------------------------------

# 15. Product Positioning

Workflow:

Choose Suite → Configure → Preview Trial → Run → Inspect → Create
EvidenceCase → Pin Regression → Export Artifact

------------------------------------------------------------------------

# 16. Open Core

Open:

-   suite format
-   runner
-   replay
-   artifact format
-   regression format
-   SDK

Commercial:

-   Suite Builder
-   hosted workspace
-   collaboration
-   hosted execution
-   artifact registry
-   private registries
-   enterprise governance
-   compliance
-   SSO/RBAC

------------------------------------------------------------------------

# 17. Long-term Vision

A third-party ecosystem of experiment suites.

Examples:

-   AgentDojo
-   MCP Compliance
-   Long Horizon Planning
-   Banking Security
-   Medical Evaluation
-   Multi-Agent Coordination
-   Internal organization suites

Axor Lab remains the execution, investigation and reproducibility
platform regardless of the experiment domain.
