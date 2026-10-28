"""Shared load/cost model for the infrastructure sizing experiments (spec section 3).

Everything is in units of "1 vCPU-minute of on-demand cloud = 1".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from scipy.ndimage import maximum_filter1d

OUT_DIR = Path(__file__).resolve().parent

MINUTES_PER_DAY = 1440
N_DAYS = 14
D_GRID: tuple[float, ...] = (0.1, 0.2, 0.35, 0.5)
D_REALISTIC: tuple[float, ...] = (0.1, 0.2, 0.35)
SERVER_SIZE = 16  # vCPU per bare-metal box, discrete variant
REACTIVE_W = 10  # autoscaler look-back, minutes
REACTIVE_H = 0.2  # headroom
MEAN_LOAD_FLOOR = 2.0  # significance filter, vCPU
SIZE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("2-16", 2.0, 16.0),
    ("16-128", 16.0, 128.0),
    (">128", 128.0, np.inf),
)

CloudModel = Literal["ideal", "reactive"]


@dataclass(frozen=True)
class Tenants:
    ids: np.ndarray
    load: np.ndarray  # (n_tenants, N_DAYS * MINUTES_PER_DAY) float32
    features: pd.DataFrame | None = None


def load_tenants(level: str = "owner", *, with_features: bool = True) -> Tenants:
    """Load the prepared series and apply the >=2 vCPU significance filter."""
    npz = np.load(OUT_DIR / f"series_{level}.npz", allow_pickle=True)
    ids, load = npz["ids"], npz["load"]
    keep = load.mean(axis=1) >= MEAN_LOAD_FLOOR
    ids, load = ids[keep], load[keep]
    feats = None
    if with_features and level == "owner":
        f = pd.read_parquet(OUT_DIR / "features.parquet").set_index("HashOwner")
        feats = f.loc[ids].reset_index()
    return Tenants(ids=ids, load=load, features=feats)


# --------------------------------------------------------------------------- #
# cloud cost
# --------------------------------------------------------------------------- #

def reactive_capacity(x: np.ndarray, *, lag: bool = False) -> np.ndarray:
    """cap[t] = max(x[t-W .. t]) * (1 + h); with lag=True the window ends at t-1.

    The spec's window includes t, under which the autoscaler can never be
    undersized; `lag=True` is the same rule shifted one minute, used only to
    report a non-degenerate SLO-violation share.
    """
    w = REACTIVE_W + 1
    roll = maximum_filter1d(x, size=w, origin=(w - 1) // 2, mode="nearest")
    if lag:
        roll = np.concatenate([x[:1], roll[:-1]])
    return roll * (1.0 + REACTIVE_H)


def cloud_cost(x: np.ndarray, model: CloudModel) -> float:
    """Cost of serving demand series `x` entirely from the cloud."""
    if model == "ideal":
        return float(x.sum())
    return float(reactive_capacity(x).sum())


def hybrid_cost(load: np.ndarray, capacity: float, d: float, model: CloudModel) -> float:
    """d*C*T for the bare metal plus the cloud cost of the spill-over."""
    overflow = np.maximum(load - capacity, 0.0)
    return d * capacity * load.size + cloud_cost(overflow, model)


def discretize(capacity: float) -> float:
    """Round capacity up to a whole bare-metal server."""
    return float(np.ceil(capacity / SERVER_SIZE) * SERVER_SIZE)


def slo_violation_share(load: np.ndarray, capacity: float) -> float:
    """Share of minutes where lagged reactive cloud capacity is below the spill-over."""
    overflow = np.maximum(load - capacity, 0.0)
    cap = reactive_capacity(overflow, lag=True)
    return float((overflow > cap + 1e-9).mean())


# --------------------------------------------------------------------------- #
# sizing policies
# --------------------------------------------------------------------------- #

def gpd_quantile(train: np.ndarray, q: float) -> float:
    """Peaks-over-threshold quantile: GPD fitted to excesses over the train P95."""
    from scipy.stats import genpareto

    u = float(np.quantile(train, 0.95))
    exc = train[train > u] - u
    if exc.size < 30 or q < 0.95:
        return float(np.quantile(train, q))
    try:
        xi, _, sigma = genpareto.fit(exc, floc=0.0)
    except Exception:  # noqa: BLE001 - fall back to the empirical quantile
        return float(np.quantile(train, q))
    if not np.isfinite(xi) or not np.isfinite(sigma) or sigma <= 0:
        return float(np.quantile(train, q))
    inner = (1.0 - q) / 0.05
    tail = sigma * (inner ** (-xi) - 1.0) / xi if abs(xi) > 1e-8 else -sigma * np.log(inner)
    out = u + float(tail)
    return out if np.isfinite(out) and out >= u else float(np.quantile(train, q))


SIMPLE_POLICIES = ("all_cloud", "all_bm_peak", "bm_mean_x2", "bm_p95")
ALL_POLICIES = (*SIMPLE_POLICIES, "newsvendor", "newsvendor_gpd")


def policy_capacity(name: str, train: np.ndarray, d: float) -> float:
    """Capacity chosen by a policy; `train` is the training-window load only."""
    if name == "all_cloud":
        return 0.0
    if name == "all_bm_peak":
        return float(train.max()) * 1.2
    if name == "bm_mean_x2":
        return 2.0 * float(train.mean())
    if name == "bm_p95":
        return float(np.quantile(train, 0.95))
    if name == "newsvendor":
        return float(np.quantile(train, 1.0 - d))
    if name == "newsvendor_gpd":
        return gpd_quantile(train, 1.0 - d)
    raise ValueError(name)


def oracle_capacity(test: np.ndarray, d: float, model: CloudModel) -> tuple[float, float]:
    """Best hindsight capacity on the test window, and its cost."""
    if model == "ideal":
        c = float(np.quantile(test, 1.0 - d))
        return c, hybrid_cost(test, c, d, model)
    grid = np.unique(
        np.concatenate([[0.0], np.quantile(test, np.linspace(0.0, 1.0, 201))])
    )
    costs = np.array([hybrid_cost(test, float(c), d, model) for c in grid])
    i = int(costs.argmin())
    return float(grid[i]), float(costs[i])
