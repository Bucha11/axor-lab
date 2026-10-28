"""I1 - is there money in quantile sizing? (spec section 4, I1 + sanity 5)

Trains every sizing policy on days 1-7, prices them on days 8-14, and reports
how much a tenant saves. Run with no arguments.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    ALL_POLICIES,
    D_GRID,
    D_REALISTIC,
    MINUTES_PER_DAY,
    SIMPLE_POLICIES,
    SIZE_BUCKETS,
    CloudModel,
    discretize,
    hybrid_cost,
    load_tenants,
    oracle_capacity,
    policy_capacity,
    slo_violation_share,
)

CLOUD_MODELS: tuple[CloudModel, ...] = ("ideal", "reactive")
TRAIN_END = 7 * MINUTES_PER_DAY


def evaluate_split(
    load: np.ndarray, train_sl: slice, test_sl: slice, *, discrete: bool = False,
    models: tuple[CloudModel, ...] = CLOUD_MODELS,
) -> pd.DataFrame:
    """Per-tenant costs of every policy under every (d, cloud model)."""
    rows: list[dict[str, object]] = []
    for i in range(load.shape[0]):
        train, test = load[i, train_sl].astype(np.float64), load[i, test_sl].astype(np.float64)
        mean_train = float(train.mean())
        for model in models:
            for d in D_GRID:
                rec: dict[str, object] = {
                    "tenant": i, "d": d, "cloud": model, "mean_train": mean_train
                }
                _, rec["cost_oracle"] = oracle_capacity(test, d, model)
                for pol in ALL_POLICIES:
                    c = policy_capacity(pol, train, d)
                    if discrete and c > 0:
                        c = discretize(c)
                    rec[f"cap_{pol}"] = c
                    rec[f"cost_{pol}"] = hybrid_cost(test, c, d, model)
                    if model == "reactive":
                        rec[f"slo_{pol}"] = slo_violation_share(test, c)
                rows.append(rec)
    return pd.DataFrame(rows)


def _finite_ratio(num: pd.Series, den: pd.Series) -> np.ndarray:
    """Ratios over tenants, dropping the ones whose denominator is 0.

    Two tenants in the filtered set carry load only in the train window (their
    trace stops), so every test-window cost for them is exactly 0. They are
    excluded from per-tenant ratio statistics and counted in `n_excl`; they
    still enter the spend-weighted totals, where they contribute nothing.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (num.to_numpy(dtype=np.float64) / den.to_numpy(dtype=np.float64))
    return r[np.isfinite(r)]


def _ratio_stats(num: pd.Series, den: pd.Series) -> tuple[float, float, float]:
    r = _finite_ratio(num, den)
    return float(np.median(r)), float(np.quantile(r, 0.10)), float(np.quantile(r, 0.90))


def _median_gain(num: pd.Series, den: pd.Series) -> float:
    """Median over tenants of 1 - num/den, in percent."""
    r = _finite_ratio(num, den)
    return 100.0 * float(np.median(1.0 - r))


def report_vs_oracle(df: pd.DataFrame) -> pd.DataFrame:
    """cost(policy) / cost(oracle): median and P10/P90 over tenants."""
    out: list[dict[str, object]] = []
    for (model, d), g in df.groupby(["cloud", "d"]):
        n_excl = int((g["cost_oracle"].to_numpy() == 0).sum())
        row: dict[str, object] = {
            "cloud": model, "d": d, "n_tenants": len(g) - n_excl, "n_excl": n_excl
        }
        for pol in ALL_POLICIES:
            med, p10, p90 = _ratio_stats(g[f"cost_{pol}"], g["cost_oracle"])
            row[pol] = f"{med:.3f} [{p10:.2f},{p90:.2f}]"
        out.append(row)
    return pd.DataFrame(out)


def report_money(df: pd.DataFrame) -> pd.DataFrame:
    """The two business numbers, per-tenant-median and spend-weighted."""
    out: list[dict[str, object]] = []
    for (model, d), g in df.groupby(["cloud", "d"]):
        simple_tot = {p: g[f"cost_{p}"].sum() for p in SIMPLE_POLICIES}
        best_simple = min(simple_tot, key=lambda p: simple_tot[p])
        nv_tot = g["cost_newsvendor"].sum()
        all_cloud_tot = simple_tot["all_cloud"]
        best_any = min(
            ((p, g[f"cost_{p}"].sum()) for p in ALL_POLICIES), key=lambda kv: kv[1]
        )
        # per-tenant best simple rule = the harsher comparison (rule picked in hindsight)
        per_tenant_best_simple = g[[f"cost_{p}" for p in SIMPLE_POLICIES]].min(axis=1)
        out.append({
            "cloud": model,
            "d": d,
            "best_simple_rule": best_simple,
            "nv_vs_best_simple_w%": 100.0 * (1.0 - nv_tot / simple_tot[best_simple]),
            "nv_vs_best_simple_med%": _median_gain(
                g["cost_newsvendor"], g[f"cost_{best_simple}"]),
            "nv_vs_pertenant_best_simple_w%": 100.0 * (
                1.0 - nv_tot / per_tenant_best_simple.sum()),
            "nv_vs_pertenant_best_simple_med%": _median_gain(
                g["cost_newsvendor"], per_tenant_best_simple),
            "best_policy": best_any[0],
            "best_vs_allcloud_w%": 100.0 * (1.0 - best_any[1] / all_cloud_tot),
            "best_vs_allcloud_med%": _median_gain(
                g[f"cost_{best_any[0]}"], g["cost_all_cloud"]),
            "oracle_vs_allcloud_w%": 100.0 * (1.0 - g["cost_oracle"].sum() / all_cloud_tot),
        })
    return pd.DataFrame(out)


def report_by_size(df: pd.DataFrame) -> pd.DataFrame:
    out: list[dict[str, object]] = []
    for (model, d), g in df.groupby(["cloud", "d"]):
        for name, lo, hi in SIZE_BUCKETS:
            s = g[(g["mean_train"] >= lo) & (g["mean_train"] < hi)]
            if s.empty:
                continue
            simple_tot = {p: s[f"cost_{p}"].sum() for p in SIMPLE_POLICIES}
            best_simple = min(simple_tot, key=lambda p: simple_tot[p])
            nv = s["cost_newsvendor"].sum()
            best_any = min(s[f"cost_{p}"].sum() for p in ALL_POLICIES)
            out.append({
                "cloud": model, "d": d, "bucket": name, "n": len(s),
                "best_simple": best_simple,
                "nv_vs_simple_w%": 100.0 * (1.0 - nv / simple_tot[best_simple]),
                "best_vs_allcloud_w%": 100.0 * (1.0 - best_any / simple_tot["all_cloud"]),
                "nv/oracle_med": float(np.median(
                    _finite_ratio(s["cost_newsvendor"], s["cost_oracle"]))),
            })
    return pd.DataFrame(out)


def sanity_newsvendor_is_optimal(load: np.ndarray, n_tenants: int = 3) -> None:
    """Spec 5: brute-force confirm C* = quantile_{1-d} minimises the ideal-cloud cost."""
    print("\n== sanity: newsvendor optimum vs brute-force grid (cloud=ideal) ==")
    rng = np.random.default_rng(0)
    picks = rng.choice(load.shape[0], size=n_tenants, replace=False)
    for i in picks:
        test = load[i, TRAIN_END:].astype(np.float64)
        for d in D_GRID:
            grid = np.unique(np.quantile(test, np.linspace(0.0, 1.0, 2001)))
            costs = np.array([hybrid_cost(test, float(c), d, "ideal") for c in grid])
            c_grid = float(grid[int(costs.argmin())])
            c_nv = float(np.quantile(test, 1.0 - d))
            cost_nv = hybrid_cost(test, c_nv, d, "ideal")
            print(
                f"  tenant#{i:5d} d={d:<4} C_grid={c_grid:10.2f} C_nv={c_nv:10.2f}  "
                f"cost_nv/cost_grid={cost_nv / costs.min():.6f}"
            )


def gpd_branch_check(load: np.ndarray) -> None:
    """Exercise the EVT policy where the spec's gate actually opens.

    `newsvendor_gpd` falls back to the empirical quantile unless 1-d >= 0.95,
    i.e. d <= 0.05, which the d grid {0.1, 0.2, 0.35, 0.5} never reaches - so in
    every table above it is identical to `newsvendor` by construction. These two
    extra d values are the only place the GPD tail is actually used.
    """
    from common import gpd_quantile

    print("\n== EVT check: GPD vs empirical quantile at d where 1-d >= 0.95 ==")
    for d in (0.05, 0.02):
        emp = gpd = orc = 0.0
        ratios: list[tuple[float, float]] = []
        for i in range(load.shape[0]):
            train = load[i, :TRAIN_END].astype(np.float64)
            test = load[i, TRAIN_END:].astype(np.float64)
            c_e = float(np.quantile(train, 1.0 - d))
            c_g = gpd_quantile(train, 1.0 - d)
            ce = hybrid_cost(test, c_e, d, "ideal")
            cg = hybrid_cost(test, c_g, d, "ideal")
            co = oracle_capacity(test, d, "ideal")[1]
            emp, gpd, orc = emp + ce, gpd + cg, orc + co
            if co > 0:
                ratios.append((ce / co, cg / co))
        r = np.array(ratios)
        print(
            f"  d={d:<5} spend-weighted cost/oracle: empirical={emp / orc:.4f} "
            f"gpd={gpd / orc:.4f}  | per-tenant median: empirical="
            f"{np.median(r[:, 0]):.4f} gpd={np.median(r[:, 1]):.4f}  -> "
            f"{'GPD better' if gpd < emp else 'empirical better or equal'}"
        )


def report_slo(df: pd.DataFrame) -> pd.DataFrame:
    g = df[df["cloud"] == "reactive"]
    out: list[dict[str, object]] = []
    for d, gg in g.groupby("d"):
        row: dict[str, object] = {"d": d}
        for pol in ALL_POLICIES:
            row[pol] = f"{100.0 * gg[f'slo_{pol}'].mean():.3f}%"
        out.append(row)
    return pd.DataFrame(out)


def sliding_windows() -> list[tuple[slice, slice, str]]:
    """Train 4 days -> test 3 days, step 1 day (spec section 4)."""
    out = []
    for start in range(0, 14 - 7 + 1):
        tr = slice(start * MINUTES_PER_DAY, (start + 4) * MINUTES_PER_DAY)
        te = slice((start + 4) * MINUTES_PER_DAY, (start + 7) * MINUTES_PER_DAY)
        out.append((tr, te, f"train d{start + 1}-{start + 4} / test d{start + 5}-{start + 7}"))
    return out


def main() -> None:
    pd.set_option("display.width", 200, "display.max_columns", 50)
    t = load_tenants("owner")
    load = t.load
    print(f"tenants (HashOwner, mean load >= 2 vCPU): {load.shape[0]}")
    print(f"test window: days 8-14 ({load.shape[1] - TRAIN_END} minutes)")

    spend = load[:, TRAIN_END:].sum(axis=1)
    order = np.argsort(-spend)
    print(
        f"spend concentration on test (all_cloud/ideal basis): top-1 tenant = "
        f"{100.0 * spend[order[0]] / spend.sum():.1f}%, top-5 = "
        f"{100.0 * spend[order[:5]].sum() / spend.sum():.1f}%, top-20 = "
        f"{100.0 * spend[order[:20]].sum() / spend.sum():.1f}% of total spend"
    )

    sanity_newsvendor_is_optimal(load)

    print("\n### main split: train d1-7 / test d8-14, continuous capacity")
    df = evaluate_split(load, slice(0, TRAIN_END), slice(TRAIN_END, None))
    print("\n-- cost / oracle, median [P10,P90] over tenants --")
    print(report_vs_oracle(df).to_string(index=False))
    print("\n-- money (w% = spend-weighted over all tenants, med% = per-tenant median) --")
    print(report_money(df).to_string(index=False))
    print("\n-- by tenant size (mean train vCPU), spend-weighted --")
    print(report_by_size(df).to_string(index=False))
    print("\n-- SLO violation share, lagged reactive autoscaler (mean over tenants) --")
    print(report_slo(df).to_string(index=False))
    gpd_branch_check(load)

    print("\n### discrete capacity (rounded up to 16-vCPU servers)")
    dfd = evaluate_split(load, slice(0, TRAIN_END), slice(TRAIN_END, None), discrete=True)
    print(report_money(dfd).to_string(index=False))

    print("\n### sliding windows (train 4d -> test 3d, step 1d), cloud=ideal")
    rows = []
    for tr, te, label in sliding_windows():
        w = evaluate_split(load, tr, te, models=("ideal",))
        m = report_money(w)
        m = m[(m["cloud"] == "ideal") & (m["d"].isin(D_REALISTIC))].copy()
        m.insert(0, "window", label)
        rows.append(m[[
            "window", "d", "best_simple_rule", "nv_vs_best_simple_w%",
            "best_policy", "best_vs_allcloud_w%",
        ]])
    sl = pd.concat(rows, ignore_index=True)
    print(sl.to_string(index=False))
    print("\n-- sliding windows, mean over windows --")
    print(
        sl.groupby("d")[["nv_vs_best_simple_w%", "best_vs_allcloud_w%"]]
        .agg(["mean", "min", "max"]).round(2).to_string()
    )

    # ---- verdicts ----
    print("\n### KILL CRITERIA (I1)")
    real = report_money(df)
    real = real[(real["cloud"] == "ideal") & (real["d"].isin(D_REALISTIC))]
    print("  (a) newsvendor vs best simple rule, spend-weighted, threshold +5%:")
    for _, r in real.iterrows():
        v = r["nv_vs_best_simple_w%"]
        print(
            f"      d={r['d']:<5} {v:+6.2f}%  (beaten rule: {r['best_simple_rule']})"
            f"  -> {'PASS' if v >= 5.0 else 'KILL'}"
        )
    print("  (b) best hybrid policy vs all_cloud, spend-weighted, threshold +20%:")
    for _, r in real.iterrows():
        v = r["best_vs_allcloud_w%"]
        print(
            f"      d={r['d']:<5} {v:+6.2f}%  (policy: {r['best_policy']})"
            f"  -> {'PASS' if v >= 20.0 else 'KILL'}"
        )
    drop = int(order[0])
    sub = df[(df["cloud"] == "ideal") & (df["d"].isin(D_REALISTIC)) & (df["tenant"] != drop)]
    r2 = report_money(sub)
    print(
        "  robustness, dropping the single largest tenant "
        f"({100.0 * spend[order[0]] / spend.sum():.0f}% of spend):"
    )
    for _, r in r2.iterrows():
        print(
            f"    d={r['d']:<5} nv_vs_best_simple_w={r['nv_vs_best_simple_w%']:+.2f}%  "
            f"best_vs_allcloud_w={r['best_vs_allcloud_w%']:+.2f}%"
        )
    df.to_parquet("i1_per_tenant.parquet", index=False)


if __name__ == "__main__":
    main()
