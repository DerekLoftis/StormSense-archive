"""Check Tidewatch's observed water levels (3d/30d hindcast CSVs) for aliasing.

Tidewatch republishes every gauge on a 6-minute grid. When the source gauge
reports less often, or reports instantaneous (unaveraged) readings, the 6-min
series shows it:

  * held samples: the same value repeated until the next report (stair steps);
  * linear fill: straight segments between sparse reports;
  * spiky noise: unaveraged wave/seiche energy folded into the 6-min series,
    seen as high-frequency scatter whose successive changes flip sign;
  * a timing offset: the observed tide leads or lags Tidewatch's own
    astronomical prediction (pa30) for the same station by a fixed amount;
  * a copied series: two stations publishing the same values.

Each check is measured over the last OBS_DAYS days of the archive.
"""
import numpy as np
import pandas as pd

from . import data, nws

OBS_DAYS = 30
STEP = pd.Timedelta(minutes=6)
MAX_LAG_MIN = 180


def _grid(s, start, end):
    s = s.loc[start:end]
    s = s[~s.index.duplicated(keep="last")]
    return s


def _runs(mask):
    """Lengths of consecutive True runs."""
    m = np.asarray(mask, bool).astype(int)
    if not m.any():
        return np.array([], int)
    d = np.diff(np.concatenate([[0], m, [0]]))
    return np.flatnonzero(d == -1) - np.flatnonzero(d == 1)


def _hp(s, hours=13):
    """Remove tides-and-slower: subtract a centered running mean."""
    return s - s.rolling(f"{hours}h", center=True, min_periods=10).mean()


def station_metrics(obs, pa30=None, nwps_obs=None):
    out = {}
    t = obs.index
    dts = pd.Series(t[1:] - t[:-1]).dt.total_seconds() / 60
    out["n"] = len(obs)
    out["step_min"] = float(dts.median()) if len(dts) else np.nan
    out["gap_frac"] = float((dts > 6.5).mean()) if len(dts) else np.nan
    reg = obs.asfreq(STEP)
    d1 = reg.diff()
    valid = d1.notna()
    eq = (d1 == 0) & valid
    out["repeat_frac"] = float(eq.sum() / max(valid.sum(), 1))
    runs = _runs(eq.values)
    out["hold_min"] = float((np.median(runs[runs >= 1]) + 1) * 6) if len(runs) else 6.0
    d2 = d1.diff()
    lin = (d2.abs() < 2e-4) & (d1.abs() > 0) & d2.notna()
    out["linear_frac"] = float(lin.sum() / max(d2.notna().sum(), 1))
    lruns = _runs(lin.values)
    out["linear_seg_min"] = float((np.median(lruns) + 1) * 6) if len(lruns) else 6.0
    hp = _hp(reg.dropna())
    smooth = reg.rolling("1h", center=True, min_periods=5).median()
    resid = (reg - smooth).dropna()
    out["hf_rms_ft"] = float(np.sqrt((resid ** 2).mean())) if len(resid) else np.nan
    dd = d1.dropna()
    out["dz_lag1_corr"] = float(dd.autocorr(1)) if len(dd) > 20 else np.nan
    out["range_ft"] = float(obs.max() - obs.min())

    out["lag_vs_pa30_min"] = np.nan
    out["corr_vs_pa30"] = np.nan
    if pa30 is not None and len(pa30) > 100:
        a = _hp(pa30.asfreq(STEP).interpolate(limit=2))
        b = hp.asfreq(STEP)
        best = (np.nan, -2)
        for k in range(-MAX_LAG_MIN // 6, MAX_LAG_MIN // 6 + 1):
            j = pd.concat([b.rename("o"), a.shift(k).rename("p")], axis=1).dropna()
            if len(j) < 100:
                continue
            c = j["o"].corr(j["p"])
            if c > best[1]:
                best = (k * 6, c)
        # positive lag: observations trail the astronomical prediction
        out["lag_vs_pa30_min"], out["corr_vs_pa30"] = best[0], best[1]

    out["nwps_diff_sd_ft"] = np.nan
    out["nwps_median_diff_ft"] = np.nan
    out["nwps_matched"] = 0
    if nwps_obs is not None and len(nwps_obs):
        med, sd, n = nws.datum_shift(nwps_obs, obs)
        out["nwps_median_diff_ft"], out["nwps_diff_sd_ft"], out["nwps_matched"] = med, sd, n
    return out


def classify(m):
    flags = []
    if m["repeat_frac"] > 0.25 and m["hold_min"] >= 12:
        flags.append(f"held values (~{m['hold_min']:.0f}-min updates)")
    if m["linear_frac"] > 0.4 and m["linear_seg_min"] >= 12:
        flags.append(f"linear fill between ~{m['linear_seg_min']:.0f}-min samples")
    if m["hf_rms_ft"] > 0.04 and m["dz_lag1_corr"] < -0.25:
        flags.append(f"spiky high-frequency noise ({m['hf_rms_ft']:.2f} ft rms)")
    if pd.notna(m["lag_vs_pa30_min"]) and abs(m["lag_vs_pa30_min"]) >= 30 and m["corr_vs_pa30"] > 0.5:
        flags.append(f"timing offset vs astronomical tide ({m['lag_vs_pa30_min']:+.0f} min)")
    if pd.notna(m["nwps_diff_sd_ft"]) and m["nwps_diff_sd_ft"] > 0.05:
        flags.append(f"disagrees with NWPS gauge (SD {m['nwps_diff_sd_ft']:.2f} ft)")
    if m["gap_frac"] > 0.2:
        flags.append(f"irregular reporting ({m['gap_frac']:.0%} of steps > 6 min)")
    return flags


def duplicates(obs, start, end, thresh=0.98):
    """Pairs of stations whose de-tided series move almost identically."""
    hp = {}
    for st, s in obs.items():
        s = _grid(s, start, end)
        if len(s) > 500:
            hp[st] = _hp(s.asfreq(STEP)).diff()
    df = pd.DataFrame(hp)
    c = df.corr(min_periods=500)
    pairs = []
    cols = list(c.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            v = c.loc[a, b]
            if pd.notna(v) and v >= thresh:
                sa, sb = _grid(obs[a], start, end), _grid(obs[b], start, end)
                j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1).dropna()
                same = float((j["a"] - j["b"]).abs().lt(1e-6).mean()) if len(j) else np.nan
                pairs.append({"a": a, "b": b, "corr_dz": round(float(v), 4), "identical_frac": same,
                              "median_offset_ft": float((j["a"] - j["b"]).median()) if len(j) else np.nan})
    return pd.DataFrame(pairs)


def run(roots, out_dir, now=None, operational=None):
    """operational: stations with a live f36 forecast; others are reported but marked."""
    roots = data.archive_roots(roots)
    obs = data.load_observations(roots, "MLLW")
    forecasts = data.load_forecasts(roots, "MLLW")
    end = now or max(s.index.max() for s in obs.values())
    start = end - pd.Timedelta(days=OBS_DAYS)
    pa = {}
    for f in forecasts:
        if "pa30" in f.data:
            pa.setdefault(f.station, []).append(f.data.set_index("time")["pa30"].dropna())
    pa30 = {}
    for st, parts in pa.items():
        s = pd.concat(parts)
        pa30[st] = s[~s.index.duplicated(keep="last")].sort_index()
    live = {f.station for f in forecasts if f.issued >= end - pd.Timedelta(days=2)}
    nroot = None
    for r in roots:
        if (r.parent / "nwps").exists():
            nroot = r.parent / "nwps"
    arch = nws.NwpsArchive(nroot) if nroot else None
    rows = []
    for st, s in sorted(obs.items()):
        s = _grid(s, start, end)
        if len(s) < 200:
            continue
        lid = nws.NWPS_LID.get(st)
        m = station_metrics(s, pa30.get(st), arch.observed(lid) if (arch and lid) else None)
        m["station"] = st
        m["source"] = data.STATION_INFO.get(st, {}).get("source", "?")
        m["operational"] = st in live if operational is None else st in operational
        m["flags"] = "; ".join(classify(m))
        rows.append(m)
    table = pd.DataFrame(rows).set_index("station")
    dup = duplicates(obs, start, end)
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / "aliasing_by_station.csv")
    dup.to_csv(out_dir / "duplicate_series.csv", index=False)
    return table, dup, (start, end)
