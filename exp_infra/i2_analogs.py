"""I2 - the method of analogues across tenants (cold start). Spec section 4, I2.

A tenant with no history knows only its mean load `m` (the index). Does the
shape (quantile / mean ratios) of similar tenants predict its quantiles, and
more importantly its bill? Leave-one-tenant-out throughout. Run with no args.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    D_GRID,
    D_REALISTIC,
    MINUTES_PER_DAY,
    SIMPLE_POLICIES,
    SIZE_BUCKETS,
    CloudModel,
    hybrid_cost,
    load_tenants,
    oracle_capacity,
    policy_capacity,
)

TRAIN_END = 7 * MINUTES_PER_DAY
QUANTILES: tuple[float, ...] = (0.5, 0.9, 0.95, 0.99)
K_NN = 20
METHODS = ("global", "class", "knn", "own_history")
TRIG_COLS = [
    "trig_http", "trig_timer", "trig_queue", "trig_event",
    "trig_storage", "trig_orchestration", "trig_others",
]


def _finite_ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """Ratios over tenants, dropping those with a zero denominator.

    Two tenants in the filtered set carry load only in the train window, so
    every test-window cost for them is 0. They are excluded from per-tenant
    medians and still counted in the spend-weighted totals (contributing 0).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        r = num.astype(np.float64) / den.astype(np.float64)
    return r[np.isfinite(r)]


def size_bucket(m: float) -> str:
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= m < hi:
            return name
    return SIZE_BUCKETS[-1][0]


def shape_ratios(x: np.ndarray, qs: tuple[float, ...]) -> np.ndarray:
    """r_q = quantile_q(x) / mean(x)."""
    mean = float(x.mean())
    if mean <= 0:
        return np.zeros(len(qs))
    return np.quantile(x, list(qs)) / mean


def build_shapes(load: np.ndarray, qs: tuple[float, ...]) -> np.ndarray:
    """(n_tenants, n_quantiles) train-window shapes."""
    return np.vstack([shape_ratios(load[i, :TRAIN_END].astype(np.float64), qs)
                      for i in range(load.shape[0])])


def predict_loo(
    shapes: np.ndarray, feats: pd.DataFrame, qs: tuple[float, ...]
) -> dict[str, np.ndarray]:
    """Leave-one-tenant-out shape predictions for each analogue method."""
    n = shapes.shape[0]
    out = {m: np.zeros_like(shapes) for m in METHODS}
    out["own_history"] = shapes.copy()  # the tenant does have history: upper bound

    # global: LOO median over everybody else
    for j in range(shapes.shape[1]):
        col = shapes[:, j]
        for i in range(n):
            out["global"][i, j] = np.median(np.delete(col, i))

    # class: same dominant trigger + same size bucket (train-only features)
    cls = feats["dom_trigger"].astype(str) + "|" + feats["bucket"].astype(str)
    cls_vals = cls.to_numpy()
    for key in np.unique(cls_vals):
        idx = np.flatnonzero(cls_vals == key)
        for j in range(shapes.shape[1]):
            col = shapes[idx, j]
            if idx.size == 1:
                out["class"][idx[0], j] = np.median(np.delete(shapes[:, j], idx[0]))
                continue
            for p, i in enumerate(idx):
                out["class"][i, j] = np.median(np.delete(col, p))

    # knn: k nearest on standardised pre-launch features
    fm = feats[[*TRIG_COLS, "log_mean", "log_nfuncs"]].to_numpy(dtype=np.float64)
    fm = (fm - fm.mean(axis=0)) / np.where(fm.std(axis=0) > 0, fm.std(axis=0), 1.0)
    for i in range(n):
        dist = np.linalg.norm(fm - fm[i], axis=1)
        dist[i] = np.inf
        nb = np.argpartition(dist, K_NN)[:K_NN]
        out["knn"][i] = np.median(shapes[nb], axis=0)
    return out


def report_shape_error(
    pred: dict[str, np.ndarray], actual: np.ndarray, qs: tuple[float, ...]
) -> pd.DataFrame:
    """max(pred/actual, actual/pred): median and P90 over tenants."""
    rows: list[dict[str, object]] = []
    for method in METHODS:
        row: dict[str, object] = {"method": method}
        for j, q in enumerate(qs):
            p = np.where(pred[method][:, j] > 0, pred[method][:, j], np.nan)
            a = np.where(actual[:, j] > 0, actual[:, j], np.nan)
            err = np.fmax(p / a, a / p)
            err = err[np.isfinite(err)]
            row[f"q{q}_med"] = round(float(np.median(err)), 3)
            row[f"q{q}_p90"] = round(float(np.quantile(err, 0.90)), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def money_table(
    load: np.ndarray, pred: dict[str, np.ndarray], qs: tuple[float, ...],
    models: tuple[CloudModel, ...] = ("ideal", "reactive"),
) -> pd.DataFrame:
    """C = m_test * r_{1-d}_pred -> hybrid cost on test, against oracle."""
    n = load.shape[0]
    test = [load[i, TRAIN_END:].astype(np.float64) for i in range(n)]
    m_test = np.array([x.mean() for x in test])
    rows: list[dict[str, object]] = []
    for d in D_GRID:
        q = 1.0 - d
        if q not in qs:
            continue
        j = qs.index(q)
        for model in models:
            rec: dict[str, object] = {"d": d, "cloud": model}
            oracle = np.array([oracle_capacity(test[i], d, model)[1] for i in range(n)])
            rec["oracle_total"] = float(oracle.sum())
            for method in METHODS:
                caps = m_test * pred[method][:, j]
                cost = np.array([
                    hybrid_cost(test[i], float(caps[i]), d, model) for i in range(n)
                ])
                rec[f"{method}/oracle_w"] = round(float(cost.sum() / oracle.sum()), 4)
                rec[f"{method}/oracle_med"] = round(
                    float(np.median(_finite_ratio(cost, oracle))), 4)
            # index-only simple rules, computed from m_test (no history needed)
            for pol, cap in (
                ("mean_x2", 2.0 * m_test),
                ("all_cloud", np.zeros(n)),
            ):
                cost = np.array([
                    hybrid_cost(test[i], float(cap[i]), d, model) for i in range(n)
                ])
                rec[f"rule_{pol}/oracle_w"] = round(float(cost.sum() / oracle.sum()), 4)
                rec[f"rule_{pol}/oracle_med"] = round(
                    float(np.median(_finite_ratio(cost, oracle))), 4)
            rows.append(rec)
    return pd.DataFrame(rows)


def main() -> None:
    pd.set_option("display.width", 220, "display.max_columns", 60)
    t = load_tenants("owner")
    load, feats = t.load, t.features.copy()
    n = load.shape[0]
    print(f"tenants: {n}  (HashOwner, mean load >= 2 vCPU)")

    feats["bucket"] = [size_bucket(m) for m in feats["mean_train"]]
    feats["log_mean"] = np.log1p(feats["mean_train"])
    feats["log_nfuncs"] = np.log1p(feats["n_funcs"])
    print("\n-- pre-launch feature coverage --")
    print(feats.groupby(["dom_trigger", "bucket"]).size().unstack(fill_value=0).to_string())

    qs = tuple(sorted({*QUANTILES, *(round(1.0 - d, 10) for d in D_GRID)}))
    shapes_train = build_shapes(load, qs)
    actual_test = np.vstack([
        shape_ratios(load[i, TRAIN_END:].astype(np.float64), qs) for i in range(n)
    ])

    pred = predict_loo(shapes_train, feats, qs)

    print(f"\n### shape prediction error, max(pred/actual, actual/pred), quantiles {qs}")
    err = report_shape_error(pred, actual_test, qs)
    print(err.to_string(index=False))

    print("\n### money: cost(test) / cost(oracle), w = spend-weighted")
    money = money_table(load, pred, qs)
    cols_w = ["d", "cloud"] + [f"{m}/oracle_w" for m in METHODS] + [
        "rule_mean_x2/oracle_w", "rule_all_cloud/oracle_w"]
    print(money[cols_w].to_string(index=False))
    print("\n-- per-tenant median of cost/oracle --")
    cols_m = ["d", "cloud"] + [f"{m}/oracle_med" for m in METHODS] + [
        "rule_mean_x2/oracle_med", "rule_all_cloud/oracle_med"]
    print(money[cols_m].to_string(index=False))

    # best analogue method = lowest spend-weighted cost in the realistic d range
    cand = money[(money["cloud"] == "ideal") & (money["d"].isin(D_REALISTIC))]
    analog_methods = [m for m in METHODS if m != "own_history"]
    totals = {m: float(cand[f"{m}/oracle_w"].mean()) for m in analog_methods}
    best = min(totals, key=lambda k: totals[k])
    print(f"\nbest analogue method (mean cost/oracle over realistic d, ideal): "
          f"{best}  {totals}")

    print("\n### KILL CRITERIA (I2)")
    j95 = qs.index(0.95)
    med95 = {}
    for m in analog_methods:
        p = np.where(pred[m][:, j95] > 0, pred[m][:, j95], np.nan)
        a = np.where(actual_test[:, j95] > 0, actual_test[:, j95], np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            e = np.fmax(p / a, a / p)
        med95[m] = float(np.median(e[np.isfinite(e)]))
    best_err_m = min(med95, key=lambda k: med95[k])
    print(f"  median shape error at q=0.95, best method ({best_err_m}): "
          f"{med95[best_err_m]:.3f}x -> "
          f"{'PASS' if med95[best_err_m] <= 1.5 else 'KILL'} (threshold 1.5x)")

    loss = []
    for _, r in cand.iterrows():
        loss.append(100.0 * (r[f"{best}/oracle_w"] / r["own_history/oracle_w"] - 1.0))
    worst_loss = max(loss)
    print(f"  money loss of '{best}' vs own_history, spend-weighted, worst over "
          f"d in {D_REALISTIC}: {worst_loss:+.2f}% -> "
          f"{'PASS' if worst_loss <= 5.0 else 'KILL'} (threshold 5%)")

    beats = []
    for _, r in cand.iterrows():
        beats.append((r["d"], r[f"{best}/oracle_w"], r["rule_mean_x2/oracle_w"]))
    print(f"  does '{best}' beat the index-only rule 2*m_test? (cost/oracle, lower is better)")
    for d, a, b in beats:
        print(f"    d={d:<5} analog={a:.4f} vs 2*m_test={b:.4f} -> "
              f"{'analog wins' if a < b else 'RULE WINS'}")

    np.savez_compressed(
        "i2_shapes.npz", shapes_train=shapes_train, actual_test=actual_test,
        qs=np.array(qs), **{f"pred_{m}": pred[m] for m in METHODS},
    )
    with open("i2_best_method.txt", "w") as f:
        f.write(best + "\n")


if __name__ == "__main__":
    main()
