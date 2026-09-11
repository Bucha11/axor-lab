# Axor Lab --- Experiment Suite Platform (Draft)

## Vision

Axor Lab is a reproducible experiment platform for AI agents.

It is **not** a governance product and **not** an A/B testing tool.

The core abstraction is the **Experiment Suite**.

An Experiment Suite defines how an experiment is executed, observed,
evaluated and packaged into a reproducible artifact.

Governance is an optional capability.

## Core Model

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
Artifact
```

## Principles

-   Bring your own agent
-   Bring your own suite
-   Bring your own traces
-   Every experiment produces a reproducible artifact
-   Every interesting trial can become an EvidenceCase
-   Every EvidenceCase can become a regression
-   Governance is optional

## Inputs

-   Live agent execution
-   Remote endpoint
-   Uploaded traces
-   Recorded bundles
-   Simulated environments

Execution is only one possible source of observations.

## Suite Responsibilities

A suite defines:

-   scenarios
-   environment
-   agents
-   tools
-   execution
-   evaluators
-   metrics
-   aggregations
-   artifact layout
-   regression rules

Example suites:

-   AgentDojo
-   Prompt Injection
-   Performance
-   Budget
-   Reliability
-   Multi-agent Coordination
-   Negotiation
-   Custom organization suites

## Platform Responsibilities

Execution: - runners - adapters - simulators - retries - seeds -
budgets - concurrency

Observation: - traces - tool calls - model calls - latency - costs -
structured events - optional provenance

Investigation: - trial browser - timeline - EvidenceCase generation -
regression pinning

Reproducibility: - portable artifact - replay - verification -
signatures - environment capture

## EvidenceCases

EvidenceCases are generic important trials, not only security incidents.

Examples: - prompt injection - hallucination - budget overflow - latency
spike - planner failure - consensus failure - secret leakage

## Regressions

Regression = executable invariant extracted from a trial.

Examples:

-   task succeeds
-   latency below threshold
-   budget within limit
-   forbidden tool calls == 0
-   consensus reached
-   gate verdict sequence
-   custom evaluator result

## Governance

Optional capabilities:

-   provenance
-   policy gates
-   deterministic replay
-   policy comparison
-   Control Plane export

## Multi-Agent

The platform is agent-count agnostic.

Only suites define the topology.

## UI Vision

The primary product is the Suite Builder.

Sections:

1.  Agents
2.  Scenarios
3.  Environment & Tools
4.  Execution
5.  Evaluation
6.  Artifact

Each suite contributes:

-   config schema
-   UI schema
-   metrics
-   artifact views
-   capabilities

The builder supports:

-   Basic
-   Advanced
-   YAML / Code

All edit the same portable suite manifest.

## Product Positioning

Choose Suite → Configure → Preview one trial → Run → Investigate →
Create EvidenceCase → Pin Regression → Export Artifact

## Open Core

Open: - suite format - runner - artifact format - replay - regression
format

Commercial: - Suite Builder - Hosted workspace - Collaboration - Hosted
execution - Artifact registry - Private registries - Enterprise features
