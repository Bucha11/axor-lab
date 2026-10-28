"""Build per-tenant minute-level vCPU load series from the Azure Functions 2019 trace.

Source: https://github.com/Azure/AzurePublicDataset (dataset-functions-2019 release).
Load unit, per spec section 2:

    L_f[t] = invocations_f[t] * avg_duration_f(day) / 60_000      # ms -> minutes
    L_tenant[t] = sum over the tenant's functions

Writes series_owner.npz, series_app.npz and features.parquet next to this file.
Run with no arguments.
"""

from __future__ import annotations

import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

DATA_URL = (
    "https://github.com/Azure/AzurePublicDataset/releases/download/"
    "dataset-functions-2019/azurefunctions_dataset2019_azurefunctions-dataset2019.tar.xz"
)
DATA_DIR = Path("/home/user/data_azure")
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = Path(__file__).resolve().parent

DAYS: list[int] = list(range(1, 15))
MINUTES_PER_DAY = 1440
TRAIN_DAYS = 7  # days 1..7 are train, 8..14 are test (spec section 4)
TRIGGERS = ("http", "timer", "queue", "event", "storage", "orchestration", "others")


def ensure_data() -> None:
    """Download and unpack the trace if it is not already on disk."""
    need = [RAW_DIR / f"invocations_per_function_md.anon.d{d:02d}.csv" for d in DAYS]
    need += [RAW_DIR / f"function_durations_percentiles.anon.d{d:02d}.csv" for d in DAYS]
    if all(p.exists() for p in need):
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive = DATA_DIR / "azurefunctions-dataset2019.tar.xz"
    if not archive.exists():
        print(f"downloading {DATA_URL}")
        urllib.request.urlretrieve(DATA_URL, archive)  # noqa: S310
    print(f"extracting {archive} -> {RAW_DIR}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:xz") as tf:
        tf.extractall(RAW_DIR)  # noqa: S202 - first-party Azure release
    missing = [p.name for p in need if not p.exists()]
    if missing:
        sys.exit(f"data incomplete after extraction, missing: {missing[:4]}")


def invocations_path(day: int) -> Path:
    return RAW_DIR / f"invocations_per_function_md.anon.d{day:02d}.csv"


def durations_path(day: int) -> Path:
    return RAW_DIR / f"function_durations_percentiles.anon.d{day:02d}.csv"


def build_index() -> tuple[pd.Index, pd.Index]:
    """First (cheap) pass: the union of owner and app ids across all 14 days."""
    owners: set[str] = set()
    apps: set[str] = set()
    for day in DAYS:
        keys = pd.read_csv(invocations_path(day), usecols=["HashOwner", "HashApp"])
        owners.update(keys["HashOwner"].unique())
        apps.update(keys["HashApp"].unique())
    return pd.Index(sorted(owners), name="HashOwner"), pd.Index(sorted(apps), name="HashApp")


@dataclass
class DayStats:
    n_rows: int
    n_matched: int
    inv_total: float
    inv_matched: float


def accumulate_day(
    day: int,
    owner_index: pd.Index,
    app_index: pd.Index,
    owner_series: np.ndarray,
    app_series: np.ndarray,
    owner_trigger_inv: np.ndarray,
    owner_funcs: list[set[str]],
) -> DayStats:
    """Add one day's load into the owner/app series; return merge diagnostics."""
    minute_cols = [str(m) for m in range(1, MINUTES_PER_DAY + 1)]
    dtypes: dict[str, type] = {c: np.int32 for c in minute_cols}
    for c in ("HashOwner", "HashApp", "HashFunction", "Trigger"):
        dtypes[c] = str
    inv = pd.read_csv(invocations_path(day), dtype=dtypes)

    dur = pd.read_csv(
        durations_path(day),
        usecols=["HashOwner", "HashApp", "HashFunction", "Average"],
        dtype={"HashOwner": str, "HashApp": str, "HashFunction": str, "Average": np.float64},
    )
    dur = dur.drop_duplicates(subset=["HashOwner", "HashApp", "HashFunction"], keep="first")

    counts = inv[minute_cols].to_numpy(dtype=np.float32)
    inv_total = float(counts.sum())

    merged = inv[["HashOwner", "HashApp", "HashFunction", "Trigger"]].merge(
        dur, on=["HashOwner", "HashApp", "HashFunction"], how="left"
    )
    avg_ms = merged["Average"].to_numpy(dtype=np.float64)
    keep = np.isfinite(avg_ms)
    inv_matched = float(counts[keep].sum())

    counts = counts[keep]
    avg_ms = avg_ms[keep]
    merged = merged.loc[keep].reset_index(drop=True)

    # L_f[t] in vCPU-equivalents.
    load = counts * np.float32(avg_ms[:, None] / 60_000.0)

    owner_codes = owner_index.get_indexer(merged["HashOwner"])
    app_codes = app_index.get_indexer(merged["HashApp"])
    assert owner_codes.min() >= 0 and app_codes.min() >= 0

    lo, hi = (day - 1) * MINUTES_PER_DAY, day * MINUTES_PER_DAY
    n = len(merged)
    ones = np.ones(n, dtype=np.float32)
    owner_mat = sparse.csr_matrix(
        (ones, (owner_codes, np.arange(n))), shape=(len(owner_index), n)
    )
    app_mat = sparse.csr_matrix((ones, (app_codes, np.arange(n))), shape=(len(app_index), n))
    owner_series[:, lo:hi] = owner_mat @ load
    app_series[:, lo:hi] = app_mat @ load

    if day <= TRAIN_DAYS:
        # Trigger mix and function count: TRAIN days only (no test leakage, spec 5).
        row_inv = counts.sum(axis=1, dtype=np.float64)
        trig_codes = merged["Trigger"].map({t: i for i, t in enumerate(TRIGGERS)})
        trig_codes = trig_codes.fillna(TRIGGERS.index("others")).to_numpy(dtype=np.int64)
        np.add.at(owner_trigger_inv, (owner_codes, trig_codes), row_inv)
        for oc, fn in zip(owner_codes, merged["HashFunction"], strict=True):
            owner_funcs[oc].add(fn)

    return DayStats(len(inv), n, inv_total, inv_matched)


def main() -> None:
    ensure_data()
    print("indexing owners/apps ...")
    owner_index, app_index = build_index()
    n_o, n_a = len(owner_index), len(app_index)
    total_minutes = len(DAYS) * MINUTES_PER_DAY
    print(f"owners={n_o} apps={n_a} minutes={total_minutes}")

    owner_series = np.zeros((n_o, total_minutes), dtype=np.float32)
    app_series = np.zeros((n_a, total_minutes), dtype=np.float32)
    owner_trigger_inv = np.zeros((n_o, len(TRIGGERS)), dtype=np.float64)
    owner_funcs: list[set[str]] = [set() for _ in range(n_o)]

    stats: list[DayStats] = []
    for day in DAYS:
        s = accumulate_day(
            day, owner_index, app_index, owner_series, app_series,
            owner_trigger_inv, owner_funcs,
        )
        stats.append(s)
        print(
            f"  day {day:02d}: rows={s.n_rows} with_duration={s.n_matched} "
            f"({100.0 * s.n_matched / s.n_rows:.1f}%) "
            f"invocations_kept={100.0 * s.inv_matched / max(s.inv_total, 1):.2f}%"
        )

    inv_tot = sum(s.inv_total for s in stats)
    inv_kept = sum(s.inv_matched for s in stats)
    print(
        f"merge coverage overall: rows {sum(s.n_matched for s in stats)}/"
        f"{sum(s.n_rows for s in stats)}, invocations {100.0 * inv_kept / inv_tot:.3f}%"
    )

    train_slice = slice(0, TRAIN_DAYS * MINUTES_PER_DAY)
    trig_tot = owner_trigger_inv.sum(axis=1)
    trig_share = np.divide(
        owner_trigger_inv, trig_tot[:, None],
        out=np.zeros_like(owner_trigger_inv), where=trig_tot[:, None] > 0,
    )
    features = pd.DataFrame(
        {"HashOwner": owner_index.to_numpy()}
        | {f"trig_{t}": trig_share[:, i] for i, t in enumerate(TRIGGERS)}
    )
    features["n_funcs"] = [len(s) for s in owner_funcs]
    features["mean_train"] = owner_series[:, train_slice].mean(axis=1)
    features["mean_all"] = owner_series.mean(axis=1)
    dom = np.where(trig_tot > 0, trig_share.argmax(axis=1), len(TRIGGERS))
    features["dom_trigger"] = [
        TRIGGERS[i] if i < len(TRIGGERS) else "none" for i in dom
    ]

    np.savez_compressed(
        OUT_DIR / "series_owner.npz", ids=owner_index.to_numpy(), load=owner_series
    )
    np.savez_compressed(
        OUT_DIR / "series_app.npz", ids=app_index.to_numpy(), load=app_series
    )
    features.to_parquet(OUT_DIR / "features.parquet", index=False)

    # ---- sanity checks (spec section 5) ----
    print("\n== sanity: daily total load (vCPU-minutes summed over all owners) ==")
    for day in DAYS:
        lo, hi = (day - 1) * MINUTES_PER_DAY, day * MINUTES_PER_DAY
        chunk = owner_series[:, lo:hi]
        print(
            f"  day {day:02d}: mean_concurrent={chunk.sum(axis=0).mean():9.1f} vCPU   "
            f"peak_minute={chunk.sum(axis=0).max():10.1f} vCPU   "
            f"active_owners={(chunk.sum(axis=1) > 0).sum()}"
        )

    print("\n== sanity: top-10 owners by mean load, daily means ==")
    order = np.argsort(-features["mean_all"].to_numpy())[:10]
    for rank, oc in enumerate(order, 1):
        per_day = owner_series[oc].reshape(len(DAYS), MINUTES_PER_DAY).mean(axis=1)
        zero_days = int((per_day == 0).sum())
        print(
            f"  #{rank:2d} {owner_index[oc][:12]} mean={features['mean_all'][oc]:8.1f} "
            f"zero_days={zero_days} daily=["
            + " ".join(f"{v:7.1f}" for v in per_day) + "]"
        )

    print("\n== sanity: 2-vCPU significance filter (owners) ==")
    mean_all = features["mean_all"].to_numpy()
    kept = mean_all >= 2.0
    tot_load = mean_all.sum()
    print(f"  owners kept: {kept.sum()}/{n_o} ({100.0 * kept.sum() / n_o:.1f}%)")
    print(
        f"  load carried by kept owners: {100.0 * mean_all[kept].sum() / tot_load:.2f}%  "
        f"| dropped owners carry {100.0 * mean_all[~kept].sum() / tot_load:.2f}%"
    )
    app_mean = app_series.mean(axis=1)
    kept_a = app_mean >= 2.0
    print(
        f"  apps kept: {kept_a.sum()}/{n_a} ({100.0 * kept_a.sum() / n_a:.1f}%), "
        f"carrying {100.0 * app_mean[kept_a].sum() / app_mean.sum():.2f}% of load"
    )
    print("\nwrote series_owner.npz, series_app.npz, features.parquet")


if __name__ == "__main__":
    main()
