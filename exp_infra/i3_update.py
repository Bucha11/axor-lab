"""I3 - how fast a tenant's own history catches up (Bayesian blend). Spec 4, I3.

Prior shape = the best analogue method from I2. After k days of the tenant's
own data the shape is w*own + (1-w)*analog with w = n_k / (n_k + n0); n0 is
tuned once on one half of the tenants and evaluated on the other half.
Money is measured on the last 7 days. Run with no arguments.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from common import (
    D_GRID,
    D_REALISTIC,
    MINUTES_PER_DAY,
    CloudModel,
    hybrid_cost,
    load_tenants,
    oracle_capacity,
)
from i2_analogs import TRIG_COLS, build_shapes, predict_loo, size_bucket

K_DAYS: tuple[int, ...] = (1, 2, 3, 5)
TEST_START = 7 * MINUTES_PER_DAY  # money is measured on days 8-14
N0_GRID: tuple[float, ...] = (
    0.0, 60.0, 180.0, 360.0, 720.0, 1440.0, 2880.0, 5760.0, 11520.0, 1e9
)
OUT_DIR = Path(__file__).resolve().parent


def own_shape_k(load: np.ndarray, i: int, k: int, qs: tuple[float, ...]) -> np.ndarray:
    """Shape from the tenant's first k days only (days 1..k, strictly pre-test)."""
    x = load[i, : k * MINUTES_PER_DAY].astype(np.float64)
    mean = float(x.mean())
    if mean <= 0:
        return np.zeros(len(qs))
    return np.quantile(x, list(qs)) / mean


def blended_cost(
    load: np.ndarray, idx: np.ndarray, analog: np.ndarray, own_k: np.ndarray,
    n0: float, k: int, d: float, qj: int, model: CloudModel,
    oracle: np.ndarray, m_test: np.ndarray,
) -> float:
    """Spend-weighted cost/oracle for one (n0, k, d)."""
    n_k = k * MINUTES_PER_DAY
    w = n_k / (n_k + n0) if np.isfinite(n0) else 1.0
    tot = 0.0
    for p, i in enumerate(idx):
        shape = w * own_k[p, qj] + (1.0 - w) * analog[i, qj]
        cap = float(m_test[p] * shape)
        tot += hybrid_cost(load[i, TEST_START:].astype(np.float64), cap, d, model)
    return tot / oracle.sum()


def main() -> None:
    pd.set_option("display.width", 220, "display.max_columns", 60)
    best_path = OUT_DIR / "i2_best_method.txt"
    if not best_path.exists():
        raise SystemExit("run i2_analogs.py first (need i2_best_method.txt)")
    best_method = best_path.read_text().strip()
    print(f"prior from I2 best analogue method: {best_method}")

    t = load_tenants("owner")
    load, feats = t.load, t.features.copy()
    n = load.shape[0]
    feats["bucket"] = [size_bucket(m) for m in feats["mean_train"]]
    feats["log_mean"] = np.log1p(feats["mean_train"])
    feats["log_nfuncs"] = np.log1p(feats["n_funcs"])

    qs = tuple(sorted({0.5, 0.9, 0.95, 0.99, *(round(1.0 - d, 10) for d in D_GRID)}))
    shapes_train = build_shapes(load, qs)
    analog = predict_loo(shapes_train, feats, qs)[best_method]

    test = [load[i, TEST_START:].astype(np.float64) for i in range(n)]
    m_test = np.array([x.mean() for x in test])

    rng = np.random.default_rng(7)
    perm = rng.permutation(n)
    tune_idx, eval_idx = perm[: n // 2], perm[n // 2:]
    print(f"tenants: {n}  (n0 tuned on {len(tune_idx)}, evaluated on {len(eval_idx)})")

    own_k_cache = {
        k: np.vstack([own_shape_k(load, i, k, qs) for i in range(n)]) for k in K_DAYS
    }

    # ---- tune n0 once, on the tuning half, at the realistic d values, ideal cloud ----
    oracle_tune = np.array([
        oracle_capacity(test[i], D_REALISTIC[1], "ideal")[1] for i in tune_idx
    ])
    qj_tune = qs.index(round(1.0 - D_REALISTIC[1], 10))
    scores: list[tuple[float, float]] = []
    for n0 in N0_GRID:
        vals = [
            blended_cost(
                load, tune_idx, analog, own_k_cache[k][tune_idx], n0, k,
                D_REALISTIC[1], qj_tune, "ideal", oracle_tune, m_test[tune_idx],
            )
            for k in K_DAYS
        ]
        scores.append((n0, float(np.mean(vals))))
    print("\n-- n0 tuning on the tuning half (mean cost/oracle over k, d=0.2, ideal) --")
    for n0, sc in scores:
        print(f"   n0={n0:>12.0f} minutes  mean cost/oracle={sc:.4f}")
    n0_best = min(scores, key=lambda kv: kv[1])[0]
    print(f"   chosen n0 = {n0_best:.0f} minutes "
          f"(w after 1 day = {MINUTES_PER_DAY / (MINUTES_PER_DAY + n0_best):.3f})"
          if np.isfinite(n0_best) and n0_best < 1e8 else
          f"   chosen n0 = {n0_best:.0f} (-> pure own history)")

    # ---- evaluate on the held-out half ----
    rows: list[dict[str, object]] = []
    for model in ("ideal", "reactive"):
        for d in D_GRID:
            qj = qs.index(round(1.0 - d, 10))
            oracle = np.array([oracle_capacity(test[i], d, model)[1] for i in eval_idx])
            row: dict[str, object] = {"cloud": model, "d": d}
            row["analog_only"] = round(
                blended_cost(load, eval_idx, analog, own_k_cache[1][eval_idx],
                             np.inf if False else 1e18, 1, d, qj, model,
                             oracle, m_test[eval_idx]), 4)
            for k in K_DAYS:
                row[f"k={k}"] = round(
                    blended_cost(load, eval_idx, analog, own_k_cache[k][eval_idx],
                                 n0_best, k, d, qj, model, oracle, m_test[eval_idx]), 4)
                row[f"own_k={k}"] = round(
                    blended_cost(load, eval_idx, analog, own_k_cache[k][eval_idx],
                                 0.0, k, d, qj, model, oracle, m_test[eval_idx]), 4)
            row["own_7d"] = round(
                blended_cost(load, eval_idx, analog, shapes_train[eval_idx], 0.0, 7,
                             d, qj, model, oracle, m_test[eval_idx]), 4)
            rows.append(row)
    res = pd.DataFrame(rows)
    print("\n### cost(test days 8-14) / cost(oracle), held-out half, spend-weighted")
    print("    analog_only = prior only; k=N = blend after N own days; "
          "own_k=N = own N days alone; own_7d = all 7 train days")
    print(res.to_string(index=False))

    print("\n### VERDICT (I3)")
    for d in D_REALISTIC:
        r = res[(res["cloud"] == "ideal") & (res["d"] == d)].iloc[0]
        print(
            f"  d={d}: analog_only={r['analog_only']:.3f}  "
            + "  ".join(f"k={k}:{r[f'k={k}']:.3f}" for k in K_DAYS)
            + f"  own_1d_alone={r['own_k=1']:.3f}  own_7d={r['own_7d']:.3f}"
        )
    r = res[(res["cloud"] == "ideal") & (res["d"] == 0.2)].iloc[0]
    print(
        f"  at d=0.2, one day of own history alone reaches {r['own_k=1']:.3f}x oracle "
        f"vs {r['analog_only']:.3f}x for the analogue prior and "
        f"{r['own_7d']:.3f}x for the full 7 days."
    )


if __name__ == "__main__":
    main()
