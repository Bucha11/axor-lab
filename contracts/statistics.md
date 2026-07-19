# Axor Lab — Statistical Contract (v1)

The single source of truth for how every number is computed. The mocks earlier used CI-overlap as a significance test and named tests that couldn't be computed from the stored data; this document fixes the method so the implementation and the UI cannot drift into decorative statistics.

**Governing rule:** a metric declares its *unit of analysis, estimator, interval method, and hypothesis test*. Nothing is inferred at render time. The stored aggregate carries all four (see `bundle.schema.json` → aggregates).

---

## 1. Unit of analysis — the error that invalidates multi-agent games

The independent observation is **one run (one trial)**, never one round, message, or step inside a run.

Rounds within a single iterated game are serially correlated: round *k* depends on round *k−1*. Treating 100 rounds as n=100 fabricates precision — the CI collapses to meaninglessness. For an iterated game the metric is computed **per run** (e.g. cooperation rate over its rounds), and n is the number of runs.

- benchmark suite: unit = one task attempt; n = repeats × scenarios (independent, different tasks/seeds).
- iterated game: unit = one run; the within-run rate is the run's single value; n = repeats.
- federation game: unit = one run of the federation; per-node values are structure within the observation, not observations.

The aggregate MUST record `unit_of_analysis`. A number whose unit is "round" is rejected in review.

## 2. Metric families

### 2a. Binary outcome per trial (ASR, breach, containment-as-reached)
- **Estimator:** proportion p̂ = successes / n.
- **Interval:** **Wilson score** 95%. (Not normal-approx: it misbehaves near 0/1, exactly where ASR lives.)
- **Paired test** (same scenarios/seeds under two conditions): **McNemar** on the discordant pairs. This REQUIRES the 2×2 table of paired outcomes — b (ungoverned success, governed fail) and c (ungoverned fail, governed success) — which means trials must be **paired and stored per-pair**, not reduced to two marginal proportions. Two proportions (0.83, 0.17) are insufficient to compute McNemar; the bundle stores the pairing.
- **Unpaired test** (independent groups): Fisher exact (small n) or χ².

### 2b. Continuous / rate outcome per trial (cooperation rate, utility, welfare)
- **Estimator:** mean of per-run values.
- **Interval:** **paired bootstrap** 95% (resample runs, not rounds). The interval **narrows with n** — a fixed hardcoded interval is a bug.
- **Paired test:** paired bootstrap of the difference, or Wilcoxon signed-rank; report **effect size** (e.g. standardized mean difference), not only a p-value.

### 2c. Utility cost (paper Table 1 shape)
- Paired Δ = governed − undefended, per pass; report the paired mean and its bootstrap CI. This is exactly the paper's methodology (banking −17±7pp is a 7-pass paired mean), and Lab must match it.

## 3. Significance is a test, not CI-overlap

Disjoint 95% CIs imply significance, but **overlapping CIs do NOT imply non-significance** — for paired designs two overlapping marginal CIs can still be a highly significant difference. The verdict is the **test** (§2a/2b), computed on the paired data. The UI may *show* CIs, but the significance line reads the test result, never `ci1.high < ci2.low`.

## 4. Underpowered / inconclusive

n < 10 per condition → the UI reports "inconclusive — raise repeats" and suppresses any significance claim. Above that, report the test and the effect size. This is honesty, not decoration: a stable sign with a wide interval is stated as such (the paper does this: +13.4 ± 9.1pp, "sign stable, magnitude not tight").

## 5. Missingness — partial results are not automatically valid

Failed/excluded trials are recorded with a reason (`bundle.trials[].failure_reason`). Before aggregating:
- Report the **denominator and the missing count** on every result ("n=228/240, 12 excluded: provider 429").
- If missingness is plausibly **non-random** (e.g. timeouts concentrate on the hardest scenario), the result is flagged **potentially biased**, not silently computed over survivors. Excluding 12 rate-limited trials can shift the estimate; the UI shows missingness rather than hiding it.

## 6. Multiple comparisons

Comparing many conditions or many scenarios inflates false positives. When >2 conditions or a scenario-family sweep is reported, apply a correction (Holm–Bonferroni default) and state it. A single pre-declared comparison needs none.

## 7. Determinism boundary (ties to claims.md)

Seeds order sampling where a provider honors them; they do **not** make the model bit-deterministic. Therefore every §2 interval is over **live** variance and is real, not simulated. The one thing that IS deterministic — the governance verdict over a fixed trace — carries no CI: it is exact (see claims.md). CI attaches to behavior; exactness attaches to verdicts. Never a CI on a replayed verdict; never "deterministic" on a live aggregate.

---

### Implementation note
`bundle.aggregates[]` stores {metric, estimate, interval:{method,low,high}, n, unit_of_analysis, test}. The results UI renders those fields verbatim. If a field is absent the UI shows "not computed", never a placeholder number. There is no code path that derives a p-value at render time.
