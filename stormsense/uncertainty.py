"""The StormSense forecast's uncertainty, from its own record: how far the water has ended up from
past forecasts at each station and lead time.

History: every published 72-hour forecast is kept hourly (cycle, station, t0, lead_h, fc_ft;
fore72_history.csv.gz on the month's stormsense-YYYY-MM release). Each past forecast whose 72 hours
have passed gives an error trajectory e(lead) = observed - forecast, lead 1-72 h, observed from
the gauge's own record.

Cone: at each lead the 10th and 90th weighted percentiles of e, added to the new forecast, so
that about 80% of observations should fall inside. Weights: exp(-age / EFOLD_DAYS) over the last
WINDOW_DAYS; the station's own trajectories count fully and every other station's count
POOL_WEIGHT as much, so a new station starts from the network's errors and moves to its own
as they build up.

Chance of reaching a flood category within 72 h: each past error trajectory is added to the new
forecast as a whole (errors at successive high tides are strongly correlated, so they are not
drawn independently), and the weighted share of trajectories whose highest water reaches the
category is the chance.
"""
import os

import numpy as np
import pandas as pd

WINDOW_DAYS = 30
EFOLD_DAYS = 7.0
POOL_WEIGHT = 0.1
MIN_COVER = 0.85      # share of the 72 hourly leads a trajectory needs
MIN_TRAJ = 8          # trajectories (any station) needed before a cone is drawn
LEADS = np.arange(1, 73)
COLS = ["cycle", "station", "t0", "lead_h", "fc_ft"]


def read(path):
    if path and os.path.exists(path):
        h = pd.read_csv(path, compression="gzip" if path.endswith(".gz") else None)
        h["t0"] = pd.to_datetime(h["t0"], utc=True)
        return h
    return pd.DataFrame(columns=COLS)


def write(h, path):
    out = h.copy()
    out["t0"] = out["t0"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(path, index=False, float_format="%.3f", compression="gzip")


def rows_for(cycle, station, grid, t0, fc):
    """Hourly history rows of one forecast (6-minute grid; the values at whole lead hours)."""
    lead = np.array([(t - t0).total_seconds() / 3600.0 for t in grid])
    out = []
    for h in LEADS:
        j = np.where(np.abs(lead - h) < 0.01)[0]
        if len(j) and not np.isnan(fc[j[0]]):
            out.append({"cycle": cycle, "station": station, "t0": pd.Timestamp(t0), "lead_h": int(h),
                        "fc_ft": round(float(fc[j[0]]), 3)})
    return out


def append(h, rows, keep_days=45):
    new = pd.DataFrame(rows, columns=COLS)
    if not new.empty:
        new["t0"] = pd.to_datetime(new["t0"], utc=True)
        h = pd.concat([h[~h["cycle"].isin(set(new["cycle"]))], new], ignore_index=True)
    if not h.empty:
        h["t0"] = pd.to_datetime(h["t0"], utc=True)
        h = h[h["t0"] > h["t0"].max() - pd.Timedelta(days=keep_days)]
    return h.sort_values(["cycle", "station", "lead_h"]).reset_index(drop=True)


def trajectories(h, truth, now):
    """[(station, t0, errors over LEADS (NaN where unobserved))] for forecasts whose 72 h have
    passed. truth: {station: (times, ft)} of the gauges' own observations."""
    out = []
    if h.empty:
        return out
    h = h[(h["t0"] > now - pd.Timedelta(days=WINDOW_DAYS)) & (h["t0"] + pd.Timedelta(hours=72) <= now)]
    for (st, t0), g in h.groupby(["station", "t0"]):
        if st not in truth:
            continue
        ot, ov = truth[st]
        if not len(ot):
            continue
        s = np.array([x.timestamp() for x in ot])
        times = np.array([(t0 + pd.Timedelta(hours=int(k))).timestamp() for k in g["lead_h"]])
        j = np.searchsorted(s, times)
        j = np.clip(j, 0, len(s) - 1)
        exact = np.abs(s[j] - times) <= 180  # an observation within 3 minutes of the hour
        e = np.full(len(LEADS), np.nan)
        idx = g["lead_h"].to_numpy(int) - 1
        e[idx[exact]] = ov[j[exact]] - g["fc_ft"].to_numpy(float)[exact]
        if np.isfinite(e).mean() >= MIN_COVER:
            ok = np.isfinite(e)
            e = np.interp(LEADS, LEADS[ok], e[ok])  # fill short gaps
            out.append((st, t0, e))
    return out


def _wq(x, w, q):
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w) - 0.5 * w
    return float(np.interp(q * w.sum(), c, x))


class Model:
    """Error trajectories weighted for one station."""

    def __init__(self, trajs, station, now):
        self.e, self.w = [], []
        for st, t0, e in trajs:
            w = np.exp(-(now - t0).total_seconds() / 86400.0 / EFOLD_DAYS) * (1.0 if st == station else POOL_WEIGHT)
            self.e.append(e)
            self.w.append(w)
        self.e, self.w = np.array(self.e), np.array(self.w)
        self.own = sum(1 for st, _, _ in trajs if st == station)
        self.ok = len(self.w) >= MIN_TRAJ and self.w.sum() > 0

    def cone(self, lead, fc, lo=0.1, hi=0.9):
        """(p10, p90) on the forecast's grid (lead hours; NaN where lead <= 0)."""
        if not self.ok:
            return None
        q_lo = np.array([_wq(self.e[:, i], self.w, lo) for i in range(len(LEADS))])
        q_hi = np.array([_wq(self.e[:, i], self.w, hi) for i in range(len(LEADS))])
        fut = lead > 0
        a = np.where(fut, fc + np.interp(lead, LEADS, q_lo), np.nan)
        b = np.where(fut, fc + np.interp(lead, LEADS, q_hi), np.nan)
        return a, b

    def chance(self, lead, fc, level):
        """Chance (0-1) that the water reaches `level` within the 72 hours, or None."""
        if not self.ok:
            return None
        fut = (lead > 0) & (lead <= 72) & ~np.isnan(fc)
        if not fut.any():
            return None
        ee = np.array([np.interp(lead[fut], LEADS, e) for e in self.e])
        peak = np.max(fc[fut][None, :] + ee, axis=1)
        return float(np.sum(self.w * (peak >= level)) / self.w.sum())

    def describe(self):
        return {"trajectories": int(len(self.w)), "own": int(self.own), "window_days": WINDOW_DAYS,
                "efold_days": EFOLD_DAYS, "pool_weight": POOL_WEIGHT}


def bootstrap_rows(tarball, table):
    """History rows from an archived StormSense Latest build (stormsense_<time>.tar.xz): its fore72
    NAVD88 CSVs put on each station's MLLW with StormSense's NAVD88 offset, so forecasts made on
    Tidewatch's MLLW (USGS gauges) are compared with the gauges' own records on one basis."""
    import tarfile
    rows = []
    name = os.path.basename(tarball)
    cycle = name[len("stormsense_"):-len(".tar.xz")]
    with tarfile.open(tarball) as t:
        for m in t.getmembers():
            n = os.path.basename(m.name)
            if not (m.name.startswith("fore72/") and n.endswith("_fore72_NAVD.csv")):
                continue
            st = n[:-len("_fore72_NAVD.csv")]
            if st not in table or table[st]["navd88"] is None:
                continue
            df = pd.read_csv(t.extractfile(m))
            if "SSforecast_ft" not in df or df.empty:
                continue
            tt = pd.to_datetime(df["date_time_z"], utc=True)
            t0 = tt.iloc[0]
            v = df["SSforecast_ft"].to_numpy(float) + table[st]["navd88"]
            lead = (tt - t0).dt.total_seconds().to_numpy() / 3600.0
            for hh in LEADS:
                j = np.where(np.abs(lead - hh) < 0.01)[0]
                if len(j) and np.isfinite(v[j[0]]):
                    rows.append({"cycle": cycle, "station": st, "t0": t0, "lead_h": int(hh), "fc_ft": round(float(v[j[0]]), 3)})
    return rows
