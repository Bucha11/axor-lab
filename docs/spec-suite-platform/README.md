# Axor Lab — Experiment Suite Platform spec & design pack

The **governing** product specification. Supersedes `docs/spec-v0.3/` as the
product narrative. Where this pack and `docs/spec-v0.3/` disagree, this pack
wins; where it and `contracts/` disagree, this pack sets the target and
`contracts/` is migrated toward it (see the plan).

## Read in this order

1. **[INTEGRATION_PLAN.md](INTEGRATION_PLAN.md)** — the gap analysis between this
   spec and the repo as it stands, the eight conflict resolutions, the extracted
   design tokens, and the phased plan. Start here.
2. **[Axor-Lab-RFC-Experiment-Suite-Platform.md](Axor-Lab-RFC-Experiment-Suite-Platform.md)**
   — the platform concept: experiment suites, runs, trials, EvidenceCases,
   regressions, artifacts, the Suite SDK and Builder, optional governance,
   multi-agent, open core.
3. **[Axor-Lab-Web-UX-Home-RFC.md](Axor-Lab-Web-UX-Home-RFC.md)** — Home /
   Launchpad, onboarding wizard, quick actions, primary navigation.
4. **[early-concept-draft.md](early-concept-draft.md)** — earlier condensed
   draft, kept for context only. Superseded by the RFC.

## Design boards

- `design/axor-lab-dark-ui-design-system.png` — **current** visual direction:
  dark-first, teal accent, full token board plus the major product screens.
- `design/axor-lab-light-concept-and-design-system.png` — light concept and
  parallel token ramp; also the clearest board for the Suite Builder, Run
  Report, Trial, EvidenceCase and Regression screens.
- `design/axor-lab-dark-home-concept.png` — earlier purple Home concept, layout
  reference only.

Generated UI images are conceptual references, not pixel-perfect implementation
specifications. Extracted tokens live in INTEGRATION_PLAN.md §5.
