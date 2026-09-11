# Axor Lab Web UX RFC --- Home & Navigation

Version: 0.1 (Draft)

## Goal

The Home page is **not** a dashboard.

Its purpose is to:

1.  Onboard new users.
2.  Help experienced users immediately start work.
3.  Explain what Axor Lab can do.
4.  Provide access to every major workflow.

The Home page should always answer one question:

> What do you want to do today?

## Design Principles

-   Dashboard is about the past.
-   Home is about the next action.
-   Jobs-to-be-done come before product entities.
-   Progressive disclosure for beginners.
-   Fast paths for experienced users.

## Home Layout

### Hero

Axor Lab

Design, run and investigate reproducible agent experiments.

Actions:

-   Quick Start
-   Documentation

### Getting Started Wizard

Visible for first-time users.

Steps:

1.  Connect an Agent
2.  Choose a Suite
3.  Run an Experiment
4.  Inspect an Evidence Case
5.  Pin a Regression

Automatically collapses after completion.

### Quick Actions

-   New Experiment
-   Run Existing Suite
-   Import Trace
-   Open Artifact
-   Playground

### Built-in Experiment Suites

Examples:

-   AgentDojo
-   Prompt Injection
-   Budget
-   Performance
-   Reliability
-   Blank Suite

Each card includes:

-   description
-   Run
-   Configure
-   Documentation

### Recent Activity

-   Recent Runs
-   Recent Artifacts
-   Recent Evidence Cases
-   Recent Regressions

Secondary content only.

### Learn

-   Tutorials
-   Documentation
-   Community Suites
-   Example Projects

## First Run Experience

Question:

What do you want to do?

Choices:

-   Evaluate my agent
-   Investigate existing traces
-   Benchmark performance
-   Build a custom experiment
-   Explore examples

Each choice starts a contextual onboarding flow.

## Navigation Philosophy

Prefer user goals over domain entities.

Goals:

-   Run an Experiment
-   Investigate a Failure
-   Import Traces
-   Build a Suite
-   Browse Examples

## Primary Navigation

-   Home
-   Suites
-   Runs
-   Evidence
-   Regressions
-   Artifacts
-   Settings

Home is the default landing page.

## Inspiration

Closer to:

-   VS Code Welcome
-   Unity Hub
-   GitHub Home

Rather than a classic enterprise dashboard.
