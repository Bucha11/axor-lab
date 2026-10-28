"""I4 - three-tier sizing against a realistic cloud base.

Closes the three holes in I1's "31-51% saving" number:

1. The cloud baseline is no longer pure on-demand. A serious buyer sits on a
   Savings Plan / reservation, so the realistic choice is three tiers:
   bare metal (always paid) + committed cloud (always paid, discounted) +
   on-demand (pay per use). The baseline is the best CLOUD-ONLY two-tier plan
   (commit + on-demand), itself fitted on train.
2. d carries a resilience/ops multiplier: bare metal in production means N+1 or
   two sites, so d_eff = d_base * k for k in {1, 1.5, 2}.
3. Results are stratified by how bursty each tenant is (train P95/mean), so the
   flat-fleet population that actually buys repatriation can be read off
   separately from the bursty serverless tail.

Capacity is always chosen on train (days 1-7) and priced on test (days 8-14).
Run with no arguments.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    MINUTES_PER_DAY,
    SERVER_SIZE,
    CloudModel,
    cloud_cost,
    load_tenants,
)

TRAIN_END = 7 * MINUTES_PER_DAY

# Committed-cloud price as a fraction of on-demand. 0.7/0.6 = a 30-40% discount
# (1-3y Compute Savings Plan territory); 0.4 = a 60% discount, the aggressive end
# of the range, so both readings of "commit discount 0.6-0.7" are covered.
COMMIT_PRICE: tuple[float, ...] = (0.7, 0.6, 0.4)
D_BASE: tuple[float, ...] = (0.1, 0.2, 0.35)
RESILIENCE: tuple[float, ...] = (1.0, 1.5, 2.0)
# d measured from dated public prices: AX162 per SMT thread / per physical core.
D_MEASURED: tuple[tuple[str, float], ...] = (("AX162/thread", 0.114), ("AX162/core", 0.229))
# d_eff values worth a per-segment read: the kill threshold, the measured AX162
# core price with k=1.5 and k=2, and the pessimistic corner.
D_EFF_SWEEP: tuple[float, ...] = (0.25, 0.30, 0.3435, 0.40, 0.458, 0.525)
KILL_D_EFF = 0.25
KILL_GAIN = 15.0

BM_GRID_MAX = 160  # candidate bare-metal sizes per tenant
CM_GRID_REACTIVE = 21


def three_tier_cost(
    load: np.ndarray, c_bm: float, c_cm: float, d: float, r: float, model: CloudModel
) -> float:
    """d*C_bm*T + r*C_cm*T + on-demand cost of whatever spills over both."""
    flat = (d * c_bm + r * c_cm) * load.size
    spill = np.maximum(load - c_bm - c_cm, 0.0)
    return flat + cloud_cost(spill, model)


def bm_candidates(train: np.ndarray) -> np.ndarray:
    """Whole 16-vCPU servers, 0 up to just past the train P99.9."""
    top = float(np.quantile(train, 0.999))
    n_srv = int(np.ceil(top / SERVER_SIZE)) + 1
    if n_srv <= BM_GRID_MAX:
        return np.arange(0, n_srv + 1, dtype=np.float64) * SERVER_SIZE
    step = int(np.ceil(n_srv / BM_GRID_MAX))
    return np.arange(0, n_srv + 1, step, dtype=np.float64) * SERVER_SIZE


def fit_cloud_only(train: np.ndarray, r: float, model: CloudModel) -> float:
    """Best committed-cloud capacity with no bare metal (the realistic baseline)."""
    if model == "ideal":
        return float(np.quantile(train, 1.0 - r))
    grid = np.unique(np.quantile(train, np.linspace(0.0, 1.0, 101)))
    costs = [three_tier_cost(train, 0.0, float(c), 0.0, r, model) for c in grid]
    return float(grid[int(np.argmin(costs))])


def fit_three_tier(
    train: np.ndarray, d: float, r: float, model: CloudModel
) -> tuple[float, float]:
    """Jointly size bare metal (whole servers) and the committed cloud tier.

    For `ideal` the inner optimum over C_cm given C_bm is analytic: the flat
    tiers together should reach quantile_{1-r}, so C_cm* = max(0, q_{1-r} - C_bm).
    For `reactive` the inner tier is searched on a quantile grid of the residual.
    """
    best = (0.0, 0.0, np.inf)
    if model == "ideal":
        q_r = float(np.quantile(train, 1.0 - r))
        for c_bm in bm_candidates(train):
            c_cm = max(0.0, q_r - c_bm)
            cost = three_tier_cost(train, c_bm, c_cm, d, r, model)
            if cost < best[2]:
                best = (c_bm, c_cm, cost)
        return best[0], best[1]
    for c_bm in bm_candidates(train):
        resid = np.maximum(train - c_bm, 0.0)
        grid = np.unique(np.quantile(resid, np.linspace(0.0, 1.0, CM_GRID_REACTIVE)))
        for c_cm in grid:
            cost = three_tier_cost(train, c_bm, float(c_cm), d, r, model)
            if cost < best[2]:
                best = (c_bm, float(c_cm), cost)
    return best[0], best[1]


def run_scenario(
    trains: list[np.ndarray], tests: list[np.ndarray],
    d: float, r: float, model: CloudModel,
) -> pd.DataFrame:
    """Per-tenant test cost of the three-tier plan and of the cloud-only base."""
    rows: list[dict[str, object]] = []
    for i, (tr, te) in enumerate(zip(trains, tests, strict=True)):
        c_cm0 = fit_cloud_only(tr, r, model)
        c_bm, c_cm = fit_three_tier(tr, d, r, model)
        rows.append({
            "tenant": i,
            "cost_cloud_commit": three_tier_cost(te, 0.0, c_cm0, d, r, model),
            "cost_cloud_ondemand": cloud_cost(te, model),
            "cost_three_tier": three_tier_cost(te, c_bm, c_cm, d, r, model),
            "c_bm": c_bm,
            "c_cm": c_cm,
            "bm_is_zero": c_bm == 0.0,
        })
    return pd.DataFrame(rows)


def weighted_gain(g: pd.DataFrame, num: str, den: str) -> float:
    return 100.0 * (1.0 - g[num].sum() / g[den].sum())


def main() -> None:
    pd.set_option("display.width", 220, "display.max_columns", 60)
    t = load_tenants("owner")
    load = t.load
    n = load.shape[0]
    trains = [load[i, :TRAIN_END].astype(np.float64) for i in range(n)]
    tests = [load[i, TRAIN_END:].astype(np.float64) for i in range(n)]

    spend = np.array([x.sum() for x in tests])
    top = int(np.argmax(spend))
    print(f"tenants: {n}; largest tenant #{top} = "
          f"{100.0 * spend[top] / spend.sum():.1f}% of on-demand spend")

    # Burstiness: train P95 / train mean. 5 tenants have no train-window load at
    # all (their trace starts mid-period), so they have no shape to measure;
    # np.digitize would silently sort NaN into the burstiest bucket, so they get
    # their own bucket 4 and are kept out of the quartile table.
    tr_mean = np.array([float(tr.mean()) for tr in trains])
    burst = np.divide(
        np.array([float(np.quantile(tr, 0.95)) for tr in trains]), tr_mean,
        out=np.full(n, np.nan), where=tr_mean > 0,
    )
    valid = ~np.isnan(burst)
    qs = np.nanquantile(burst, [0.25, 0.5, 0.75])
    print(f"burstiness (train P95/mean): P25={qs[0]:.2f} median={qs[1]:.2f} "
          f"P75={qs[2]:.2f} max={np.nanmax(burst):.1f}")
    print(f"  no train-window load (excluded from the quartile table): "
          f"{int((~valid).sum())} tenants, "
          f"{100.0 * spend[~valid].sum() / spend.sum():.2f}% of spend")
    bucket = np.where(valid, np.digitize(burst, qs), 4)  # 0 = flattest, 4 = no shape
    names = ["Q1 flattest", "Q2", "Q3", "Q4 burstiest"]

    print("\n### three-tier vs cloud-with-commit, spend-weighted, cloud=ideal")
    print("    gain>0 means the bare-metal hybrid still beats a committed cloud plan")
    hdr = (f"{'d_base':>7} {'k':>4} {'d_eff':>6} {'commit':>7} "
           f"{'gain_vs_commit%':>16} {'ex-top%':>8} {'gain_vs_ondemand%':>18} "
           f"{'tenants_bm=0':>13}")
    print(hdr)
    recs: list[dict[str, object]] = []
    for r in COMMIT_PRICE:
        for d_base in D_BASE:
            for k in RESILIENCE:
                d_eff = round(d_base * k, 4)
                g = run_scenario(trains, tests, d_eff, r, "ideal")
                gain = weighted_gain(g, "cost_three_tier", "cost_cloud_commit")
                gx = weighted_gain(
                    g[g.tenant != top], "cost_three_tier", "cost_cloud_commit")
                gon = weighted_gain(g, "cost_three_tier", "cost_cloud_ondemand")
                zero = 100.0 * g["bm_is_zero"].mean()
                print(f"{d_base:>7} {k:>4} {d_eff:>6} {r:>7} {gain:>16.2f} "
                      f"{gx:>8.2f} {gon:>18.2f} {zero:>12.0f}%")
                recs.append({
                    "cloud": "ideal", "d_base": d_base, "k": k, "d_eff": d_eff,
                    "commit": r, "gain_vs_commit": gain, "gain_ex_top": gx,
                    "gain_vs_ondemand": gon, "bm_zero_pct": zero,
                    **{f"burst_{names[b]}": weighted_gain(
                        g[bucket[g.tenant.to_numpy()] == b],
                        "cost_three_tier", "cost_cloud_commit")
                       for b in range(4)},
                })
    res = pd.DataFrame(recs)

    print("\n### same, cloud=reactive (commit=0.6 only, to bound runtime)")
    print(hdr)
    for d_base in D_BASE:
        for k in RESILIENCE:
            d_eff = round(d_base * k, 4)
            g = run_scenario(trains, tests, d_eff, 0.6, "reactive")
            gain = weighted_gain(g, "cost_three_tier", "cost_cloud_commit")
            gx = weighted_gain(g[g.tenant != top], "cost_three_tier", "cost_cloud_commit")
            gon = weighted_gain(g, "cost_three_tier", "cost_cloud_ondemand")
            zero = 100.0 * g["bm_is_zero"].mean()
            print(f"{d_base:>7} {k:>4} {d_eff:>6} {0.6:>7} {gain:>16.2f} "
                  f"{gx:>8.2f} {gon:>18.2f} {zero:>12.0f}%")
            recs.append({
                "cloud": "reactive", "d_base": d_base, "k": k, "d_eff": d_eff,
                "commit": 0.6, "gain_vs_commit": gain, "gain_ex_top": gx,
                "gain_vs_ondemand": gon, "bm_zero_pct": zero,
                **{f"burst_{names[b]}": weighted_gain(
                    g[bucket[g.tenant.to_numpy()] == b],
                    "cost_three_tier", "cost_cloud_commit") for b in range(4)},
            })
    res = pd.DataFrame(recs)

    print("\n### measured d from dated public prices, cloud=ideal, commit=0.6")
    print(f"{'source':>14} {'k':>4} {'d_eff':>6} {'gain_vs_commit%':>16} {'ex-top%':>8}")
    for label, d_base in D_MEASURED:
        for k in RESILIENCE:
            d_eff = round(d_base * k, 4)
            g = run_scenario(trains, tests, d_eff, 0.6, "ideal")
            print(f"{label:>14} {k:>4} {d_eff:>6} "
                  f"{weighted_gain(g, 'cost_three_tier', 'cost_cloud_commit'):>16.2f} "
                  f"{weighted_gain(g[g.tenant != top], 'cost_three_tier', 'cost_cloud_commit'):>8.2f}")

    print("\n### gain vs cloud-with-commit by burstiness, commit=0.6")
    print("    Q1 is the flattest quartile - the population that actually buys")
    print("    repatriation. bm0 = share of tenants the optimiser gives no bare metal.")
    for model in ("ideal", "reactive"):
        print(f"\n  cloud={model}")
        print(f"  {'d_eff':>7} | "
              + " | ".join(f"{m:>21}" for m in names) + " |  pooled")
        for d_eff in D_EFF_SWEEP:
            cells, tn, td = [], 0.0, 0.0
            for b in range(4):
                idx = np.flatnonzero(bucket == b)
                g = run_scenario([trains[i] for i in idx], [tests[i] for i in idx],
                                 d_eff, 0.6, model)
                cells.append(f"{weighted_gain(g, 'cost_three_tier', 'cost_cloud_commit'):+6.1f}% "
                             f"bm0={100.0 * g['bm_is_zero'].mean():3.0f}%")
                tn += g["cost_three_tier"].sum(); td += g["cost_cloud_commit"].sum()
            print(f"  {d_eff:>7} | " + " | ".join(f"{c:>21}" for c in cells)
                  + f" | {100.0 * (1 - tn / td):+7.2f}%")

    print("\n### non-stationarity risk: who loses money in the burstiest quartile")
    print("    (cloud=ideal, d_eff=0.15, commit=0.6; a plan fitted on train can be")
    print("     catastrophically wrong when a tenant's load collapses before test)")
    idx = np.flatnonzero(bucket == 3)
    g = run_scenario([trains[i] for i in idx], [tests[i] for i in idx], 0.15, 0.6, "ideal")
    g["tenant"] = idx
    g["excess"] = g["cost_three_tier"] - g["cost_cloud_commit"]
    g["train_mean"] = [trains[i].mean() for i in idx]
    g["test_mean"] = [tests[i].mean() for i in idx]
    g["ratio"] = g["cost_three_tier"] / g["cost_cloud_commit"]
    worst = g.nlargest(5, "excess")
    print(f"  {'tenant':>7} {'excess':>12} {'C_bm':>8} {'train_mean':>11} "
          f"{'test_mean':>10} {'cost_ratio':>11}")
    for _, w in worst.iterrows():
        print(f"  {int(w['tenant']):>7} {w['excess']:>12.0f} {w['c_bm']:>8.0f} "
              f"{w['train_mean']:>11.2f} {w['test_mean']:>10.2f} {w['ratio']:>11.2f}")
    pos = g.loc[g["excess"] > 0, "excess"].sum()
    print(f"  the single worst tenant is {100.0 * worst['excess'].iloc[0] / pos:.0f}% "
          f"of all excess cost in this quartile")

    print(f"\n### KILL CRITERION (I4): hybrid vs cloud-with-commit >= {KILL_GAIN}% "
          f"at d_eff >= {KILL_D_EFF}")
    hot = res[res.d_eff >= KILL_D_EFF]
    for cloud in ("ideal", "reactive"):
        h = hot[hot.cloud == cloud]
        if h.empty:
            continue
        print(f"  cloud={cloud}: n={len(h)} scenarios, gain_vs_commit "
              f"median={h.gain_vs_commit.median():.2f}% "
              f"min={h.gain_vs_commit.min():.2f}% max={h.gain_vs_commit.max():.2f}%")
        print(f"     ex-top-tenant: median={h.gain_ex_top.median():.2f}% "
              f"min={h.gain_ex_top.min():.2f}%")
        n_pass = int((h.gain_vs_commit >= KILL_GAIN).sum())
        print(f"     scenarios clearing {KILL_GAIN}%: {n_pass}/{len(h)} "
              f"-> {'PASS' if n_pass > len(h) / 2 else 'KILL'}")
    res.to_parquet("i4_scenarios.parquet", index=False)
    print("\nwrote i4_scenarios.parquet")


if __name__ == "__main__":
    main()
