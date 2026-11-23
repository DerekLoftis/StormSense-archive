"""Score every archived StormSense run against the water levels observed after it, with Derek's
skill formula; score the high waters that flood people; pick each station's primary guidance per
lead band (phase 2); and write the diagnostic skill tables.

Inputs: the run tarballs (ensemble_<cycle>.tar.gz on the ensemble-YYYY-MM releases: this
repository's, and the public StormSense-ensemble's before Oct 2026). The last RESCORE_DAYS of runs
are rescored each time; earlier scores come from the cumulative skill_scores.csv.gz and
flood_scores.csv.gz on the newest release that holds them.

Truth: the gauges' own observations (observe.py, passed in by build.py), falling back to the
observed_ft column stitched across the archived runs. Runs made before StormSense read its own
observations ("obs_basis" absent in latest.json) used Tidewatch's MLLW, which for USGS gauges is a
fixed 1.645 ft below NAVD88; their NAVD88 CSVs plus StormSense's NAVD88 offset put them on the
station's own MLLW, so all runs are scored on one basis.

Each run x station x member is scored per window of lead time (hours after the station's last
observation at run time) once observations cover MIN_COVERAGE of it:

    lead bands  0-12, 12-36, 36-72 (the pick), 0-36, 0-72
    full        the member's whole forecast period (0-36 h for Tidewatch, 0-72 h for most)
    segments    every 6 hours: 0-6, 6-12, ... 66-72

    S = 0.25 [R2 + e^(-|Bias|/sigma) + e^(-MAE/sigma) + e^(-RMSE/sigma)],  sigma = population SD of
    the observations in the window (the Tidewatch ASA formula, asa/metrics.py)

High waters (flood.py): every observed high water in the member's period is paired with the
member's highest water within 3 hours of it: peak-height error, peak-timing error, and per NWS
flood category a hit, a miss or a false alarm.

Adaptive pick: at each update, a member's recent skill at a station and band is the mean of its
scores over the last WINDOW_DAYS, weighted by exp(-age / EFOLD_DAYS). The leader must beat the
current pick by MARGIN for CONFIRM updates running before the pick switches. A station and band
stay on P-ETSS until READY_DAYS of verified runs exist and the member has MIN_RUNS scores.

Tables (per station and over all stations, ordered by SS-Skill over the forecast period):
  1 overall   forecast, period, SS-Skill, R2, Bias, MAE, RMSE
  2 segments  forecast, period, SS-Skill, then R2, Bias, MAE, RMSE, SS-Skill per 6 hours
  3 peaks     forecast, period, SS-Skill, high waters, peak-height and peak-timing errors, and
              hits, misses and false alarms per flood category
"""
import html as H
import io
import json
import os
import tarfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import flood, releases

BANDS = (("0-12", 0, 12), ("12-36", 12, 36), ("36-72", 36, 72), ("0-36", 0, 36), ("0-72", 0, 72))
SEGMENTS = tuple((f"{a}-{a + 6}", a, a + 6) for a in range(0, 72, 6))
FULL = "full"
PICK_BANDS = ("0-12", "12-36", "36-72")
STEP_MIN = 6
MIN_COVERAGE = 0.85
RESCORE_DAYS = 5        # runs newer than this are rescored each time (72 h + slack for late runs)
WINDOW_DAYS = 10        # recent-skill window
EFOLD_DAYS = 3.0        # recency weighting
READY_DAYS = 7          # verified history needed before the pick may leave P-ETSS
MIN_RUNS = 12           # scores a member needs in the window to be picked
MARGIN = 0.01           # skill a challenger must lead by ...
CONFIRM = 2             # ... for this many updates running
REPLAY_DAYS = 30        # the pick is replayed from P-ETSS through this many days of updates
PUBLISH_DAYS = 31       # runs in the published scores.csv.gz (the release keeps them all)
# GitHub refuses a push holding a file over 100 MiB; 31 days of scores passed that on 10 Nov 2026 and the
# Ensemble site stopped updating. scores.csv keeps the newest whole cycles that fit under this size.
PLAIN_MAX_BYTES = 80 * 1024 * 1024
DEFAULT = "petss"
NOT_MEMBERS = {"observed", "astro_tide", "petss_surge", "etss_surge"}
# Members that can drive the primary forecast. Tidewatch is the other brand and the baseline and
# the tide alone are references every forecast should beat; they are scored but never picked.
# stormsense_raw is StormSense before its site corrector, kept to check that the corrector helps;
# primary is the published StormSense Latest forecast itself (made from the pick).
REFERENCE = {"tidewatch", "baseline", "tide", "stormsense_raw", "primary"}
# Tidewatch published another gauge's observations under these ids (handoff aliasing check,
# Oct 2026; USBB is held in ~18-min steps): scored for the record, never used to pick guidance.
SUSPECT_OBS = {"USSC", "USAR", "USCB", "USNR", "USSB", "GS01", "USBB"}
NAMES = {"primary": "StormSense Latest (published)", "stormsense": "StormSense model",
         "stormsense_raw": "StormSense model (uncorrected)", "petss": "NWS P-ETSS", "stofs2d": "NOAA STOFS-2D-Global",
         "stofs3d": "NOAA STOFS-3D-Atlantic", "cbofs": "NOAA CBOFS", "nwm_short": "NWM coastal (short range)",
         "nwm_medium": "NWM coastal (medium range)", "etss": "NWS ETSS", "psurge": "NHC P-Surge (50%)",
         "nws": "NWS official forecast", "tidewatch": "VIMS Tidewatch", "baseline": "Tide + persistence",
         "tide": "Astronomical tide only"}
SCORE_COLS = ["cycle", "station", "member", "member_run", "band", "t0", "lead_end", "span_h", "N", "R2", "Bias",
              "MAE", "RMSE", "SDobs", "Skill", "PeakError", "ConeHit"]
FLOOD_COLS = (["cycle", "station", "member", "t0", "lead_end", "span_h", "high_waters", "peak_error_ft",
               "peak_abs_error_ft", "timing_error_min", "timing_abs_error_min"]
              + [f"{c}_{k}" for c in flood.CATEGORIES for k in ("hit", "miss", "fa")])
RUN_COLS = ["cycle", "updated", "station", "t0"]


# --- Derek's statistics (same as handoff/skill/tidewatch-asa/asa/metrics.py) -------------------
def statistics(x, y):
    """x observed, y forecast: R2 (squared Pearson r), Bias, MAE, RMSE, SDobs (population), PeakError."""
    err = y - x
    r = np.corrcoef(x, y)[0, 1] if x.std() > 0 and y.std() > 0 else np.nan
    return {"R2": r * r, "Bias": err.mean(), "MAE": np.abs(err).mean(), "RMSE": np.sqrt((err ** 2).mean()),
            "SDobs": x.std(ddof=0), "PeakError": y.max() - x.max()}


def skill_score(s):
    """S = 0.25 [R2 + e^(-|Bias|/sd) + e^(-MAE/sd) + e^(-RMSE/sd)], sd the population SD of obs."""
    sd = s["SDobs"]
    if not (sd > 0) or np.isnan(s["R2"]):
        return np.nan
    return 0.25 * (float(np.clip(s["R2"], 0, 1)) + np.exp(-abs(s["Bias"]) / sd)
                   + np.exp(-s["MAE"] / sd) + np.exp(-s["RMSE"] / sd))


# --- archive access ------------------------------------------------------------------------------
def cycle_of(name):
    return datetime.strptime(os.path.basename(name)[9:23], "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)


def _cached(rel, name):
    return next((a[name] for a in rel.values() if name in a), None)


def gather(work, local, now, rescore_all=False):
    """Download the cached scores and the run tarballs to (re)score.
    Returns (scores, flood scores, runs, {cycle: tarball})."""
    tars = {cycle_of(p): p for p in local}
    scores, floods = pd.DataFrame(columns=SCORE_COLS), pd.DataFrame(columns=FLOOD_COLS)
    runs = pd.DataFrame(columns=RUN_COLS)
    try:
        rel = releases.assets("ensemble-")
    except Exception as e:
        print("releases unavailable:", e)
        rel = {}
    cached = None if rescore_all else _cached(rel, "skill_scores.csv.gz")
    if cached:
        head = pd.read_csv(io.BytesIO(releases._get(cached, "application/octet-stream")), compression="gzip", nrows=1)
        if "span_h" not in head:  # scores from before the 6-hour segments and high-water scores: start over
            print("cached scores predate the segment and high-water scores; rescoring every run")
            cached = None
    if cached:
        scores = pd.read_csv(io.BytesIO(releases._get(cached, "application/octet-stream")), compression="gzip")
        runs = pd.read_csv(io.BytesIO(releases._get(_cached(rel, "skill_runs.csv"), "application/octet-stream")))
        fl = _cached(rel, "flood_scores.csv.gz")
        if fl:
            floods = pd.read_csv(io.BytesIO(releases._get(fl, "application/octet-stream")), compression="gzip")
        for c in SCORE_COLS:
            if c not in scores:
                scores[c] = np.nan
        print(f"cached scores: {len(scores)} rows, {runs['cycle'].nunique()} runs; {len(floods)} flood rows")
    since = now - pd.Timedelta(days=RESCORE_DAYS) if cached else pd.Timestamp(2000, 1, 1, tz="UTC")
    for assets in rel.values():
        for name, url in assets.items():
            if not (name.startswith("ensemble_") and name.endswith(".tar.gz")):
                continue
            c = cycle_of(name)
            if c >= since and c < now and c not in tars:
                tars[c] = releases.download(url, os.path.join(work, "tars", name))
    print(f"{len(tars)} run tarballs to score")
    return scores, floods, runs, dict(sorted(tars.items()))


def read_run(path, navd88=None):
    """(updated, {station: (t0, DataFrame of the ens72 CSV on the station's MLLW indexed by time,
    member notes)}). Runs on Tidewatch's observation basis are moved onto StormSense's: their NAVD88
    CSVs plus navd88[station]."""
    navd88 = navd88 or {}
    out = {}
    with tarfile.open(path) as t:
        latest = json.load(t.extractfile("latest.json"))
        own = latest.get("obs_basis") == "stormsense"
        files = {m.name: m for m in t.getmembers() if m.name.startswith("ens72/") and m.name.endswith(".csv")}
        for name in files:
            n = os.path.basename(name)
            if not n.endswith("_ens72_MLLW.csv"):
                continue
            st = n[:-len("_ens72_MLLW.csv")]
            info = latest["stations"].get(st)
            if not info or not info.get("t0"):
                continue
            navd = f"ens72/{st}_ens72_NAVD.csv"
            if not own and navd88.get(st) is not None and navd in files:
                df = pd.read_csv(t.extractfile(files[navd]))
                lv = [c for c in df.columns if c.endswith("_ft") and not c.endswith("_surge_ft")]
                df[lv] = df[lv] + navd88[st]
            else:
                df = pd.read_csv(t.extractfile(files[name]))
            df.index = pd.to_datetime(df.pop("date_time_z"), utc=True)
            out[st] = (pd.Timestamp(info["t0"]), df, info.get("members", {}))
    updated = pd.Timestamp(datetime.strptime(latest["updated"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc))
    return updated, out


# --- scoring ---------------------------------------------------------------------------------------
def _windows(lead, t_last_obs_h, span):
    """(name, lo, hi, mask) for every window of one member verified by the observations."""
    out = []
    for name, lo, hi in BANDS + SEGMENTS + ((FULL, 0, span),):
        if hi <= lo or hi > t_last_obs_h:
            continue
        out.append((name, lo, hi, (lead > lo) & (lead <= hi)))
    return out


def score_runs(tars, truth=None, levels=None, navd88=None):
    """Score every run in tars. truth: {station: pd.Series of observed ft MLLW}; stations missing
    from it use the observations stitched from the runs. levels: {station: {category: ft MLLW}}.
    Returns (scores, flood scores, runs)."""
    loaded = {c: read_run(p, navd88) for c, p in tars.items()}
    stitched = {}
    for c in sorted(loaded):  # newer runs overwrite older ones
        for st, (_, df, _) in loaded[c][1].items():
            if "observed_ft" in df:
                stitched.setdefault(st, {}).update(df["observed_ft"].dropna().to_dict())
    obs = {st: pd.Series(v).sort_index() for st, v in stitched.items()}
    obs.update({st: s for st, s in (truth or {}).items() if len(s)})
    rows, frows, runs = [], [], []
    for c, (updated, stations) in loaded.items():
        cyc = f"{c:%Y-%m-%dT%HZ}"
        for st, (t0, df, meta) in stations.items():
            runs.append({"cycle": cyc, "updated": updated.isoformat(), "station": st, "t0": t0.isoformat()})
            tr = obs.get(st)
            if tr is None or tr.empty:
                continue
            lead = ((df.index - t0).total_seconds() / 3600.0).to_numpy()
            x_all = tr.reindex(df.index).to_numpy(float)
            last_h = (tr.index[-1] - t0).total_seconds() / 3600.0
            cols = {}
            for k in df.columns:
                if not k.endswith("_ft") or k.endswith(("_p10_ft", "_p90_ft")):
                    continue
                m = "tide" if k == "astro_tide_ft" else k[:-3]
                if m in NOT_MEMBERS:
                    continue
                cols[m] = k
            lv = (levels or {}).get(st, {})
            for m, col in cols.items():
                y_all = df[col].to_numpy(float)
                have = np.where(~np.isnan(y_all) & (lead > 0))[0]
                if not len(have):
                    continue
                span = int(np.floor(lead[have[-1]] / 6.0 + 1e-6) * 6)  # the member's period, whole 6 h
                if span < 6:
                    continue
                lo_c = df[f"{m}_p10_ft"].to_numpy(float) if f"{m}_p10_ft" in df else None
                hi_c = df[f"{m}_p90_ft"].to_numpy(float) if f"{m}_p90_ft" in df else None
                for band, lo, hi, sel in _windows(lead, last_h, span):
                    ok = sel & ~np.isnan(x_all) & ~np.isnan(y_all)
                    if ok.sum() < MIN_COVERAGE * (hi - lo) * 60 / STEP_MIN:
                        continue
                    x, y = x_all[ok], y_all[ok]
                    s = statistics(x, y)
                    cone = np.nan
                    if lo_c is not None and hi_c is not None:
                        a, b = lo_c[ok], hi_c[ok]
                        good = ~np.isnan(a) & ~np.isnan(b)
                        if good.any():
                            cone = float(np.mean((x[good] >= a[good]) & (x[good] <= b[good])))
                    rows.append({"cycle": cyc, "station": st, "member": m,
                                 "member_run": (meta.get(m) or {}).get("run", ""), "band": band,
                                 "t0": t0.isoformat(), "lead_end": (t0 + pd.Timedelta(hours=hi)).isoformat(),
                                 "span_h": hi - lo, "N": int(ok.sum()),
                                 **{k: s[k] for k in ("R2", "Bias", "MAE", "RMSE", "SDobs")},
                                 "Skill": skill_score(s), "PeakError": s["PeakError"], "ConeHit": cone})
                    if band == FULL:
                        ev = flood.verify(lead[sel], np.where(ok, x_all, np.nan)[sel], np.where(ok, y_all, np.nan)[sel], lv)
                        summ = flood.summarize(ev)
                        if summ:
                            frows.append({"cycle": cyc, "station": st, "member": m, "t0": t0.isoformat(),
                                          "lead_end": (t0 + pd.Timedelta(hours=hi)).isoformat(), "span_h": hi, **summ})
    return (pd.DataFrame(rows, columns=SCORE_COLS), pd.DataFrame(frows, columns=FLOOD_COLS),
            pd.DataFrame(runs, columns=RUN_COLS))


def merge(old, new, rescored, order=("cycle", "station", "member", "band")):
    """Old rows for runs not rescored now, plus the new rows."""
    if old is not None and not old.empty:
        new = pd.concat([old[~old["cycle"].isin(rescored)], new], ignore_index=True)
    order = [c for c in order if c in new.columns]
    return new.sort_values(order).reset_index(drop=True)


# --- recent skill and the adaptive pick ------------------------------------------------------------
REC_COLS = ["station", "band", "member", "n", "skill_ew", "r2_ew", "bias_ew", "mae_ew", "rmse_ew", "span_h"]


def recent(scores, at, bands=PICK_BANDS):
    """Recent skill per station x band x member as known at time `at` (verified by then):
    exponentially weighted means over the last WINDOW_DAYS. No look-ahead."""
    s = scores[scores["band"].isin(bands)].copy()
    s["lead_end"] = pd.to_datetime(s["lead_end"], utc=True)
    s = s[(s["lead_end"] <= at) & (s["lead_end"] > at - pd.Timedelta(days=WINDOW_DAYS)) & s["Skill"].notna()]
    if s.empty:
        return pd.DataFrame(columns=REC_COLS)
    s["w"] = np.exp(-(at - s["lead_end"]).dt.total_seconds() / 86400 / EFOLD_DAYS)
    if "span_h" not in s:
        s["span_h"] = np.nan
    g = s.assign(ws=s["w"] * s["Skill"], wr=s["w"] * s["RMSE"], wm=s["w"] * s["MAE"], wb=s["w"] * s["Bias"],
                 w2=s["w"] * s["R2"].fillna(0))
    a = g.groupby(["station", "band", "member"]).agg(
        n=("w", "size"), w=("w", "sum"), ws=("ws", "sum"), wr=("wr", "sum"), wm=("wm", "sum"), wb=("wb", "sum"),
        w2=("w2", "sum"), span_h=("span_h", "median")).reset_index()
    for k, c in (("skill_ew", "ws"), ("rmse_ew", "wr"), ("mae_ew", "wm"), ("bias_ew", "wb"), ("r2_ew", "w2")):
        a[k] = a[c] / a["w"]
    return a[REC_COLS]


def recent_floods(floods, at):
    """Peak and flood-stage scores per station x member over the last WINDOW_DAYS (verified by
    `at`): mean errors weighted by the number of high waters, counts summed."""
    f = floods.copy()
    if f.empty:
        return pd.DataFrame(columns=["station", "member", "runs"] + FLOOD_COLS[6:])
    f["lead_end"] = pd.to_datetime(f["lead_end"], utc=True)
    f = f[(f["lead_end"] <= at) & (f["lead_end"] > at - pd.Timedelta(days=WINDOW_DAYS))]
    out = []
    for (st, m), g in f.groupby(["station", "member"]):
        n = g["high_waters"].sum()
        row = {"station": st, "member": m, "runs": len(g), "high_waters": int(n)}
        for k in ("peak_error_ft", "peak_abs_error_ft", "timing_error_min", "timing_abs_error_min"):
            row[k] = float((g[k] * g["high_waters"]).sum() / n) if n else np.nan
        for c in flood.CATEGORIES:
            for k in ("hit", "miss", "fa"):
                row[f"{c}_{k}"] = int(g[f"{c}_{k}"].sum())
        out.append(row)
    return pd.DataFrame(out)


def replay(scores, runs):
    """Run the pick with hysteresis through every update in order, so the current pick needs no
    saved state. Returns (current picks, history rows)."""
    sc = scores[scores["band"].isin(PICK_BANDS)].copy()
    sc["lead_end_t"] = pd.to_datetime(sc["lead_end"], utc=True)
    first = sc.groupby(["station", "band"])["lead_end_t"].min()
    ups = runs.assign(updated=pd.to_datetime(runs["updated"], utc=True)).groupby("cycle")["updated"].max().sort_index()
    ups = ups[ups > ups.max() - pd.Timedelta(days=REPLAY_DAYS)] if len(ups) else ups
    sc = sc[sc["lead_end_t"] > ups.min() - pd.Timedelta(days=WINDOW_DAYS)] if len(ups) else sc
    state, hist = {}, []
    for cyc, at in ups.items():
        rec = recent(sc, at)
        stations = sorted(runs.loc[runs["cycle"] == cyc, "station"])
        for st in stations:
            for band in PICK_BANDS:
                key = (st, band)
                cur = state.setdefault(key, {"member": DEFAULT, "since": cyc, "challenger": None, "count": 0})
                r = rec[(rec["station"] == st) & (rec["band"] == band)].set_index("member")
                since = first.get(key)
                ready = since is not None and at - since >= pd.Timedelta(days=READY_DAYS) and st not in SUSPECT_OBS
                elig = r[(r["n"] >= MIN_RUNS) & ~r.index.isin(REFERENCE)]
                leader = elig["skill_ew"].idxmax() if not elig.empty else None
                cur_skill = elig["skill_ew"].get(cur["member"], np.nan)
                if not ready:
                    cur.update(member=DEFAULT, challenger=None, count=0)
                elif leader is not None and leader != cur["member"] and (
                        np.isnan(cur_skill) or elig.loc[leader, "skill_ew"] >= cur_skill + MARGIN):
                    cur["count"] = cur["count"] + 1 if cur["challenger"] == leader else 1
                    cur["challenger"] = leader
                    if cur["count"] >= CONFIRM:
                        cur.update(member=leader, since=cyc, challenger=None, count=0)
                else:
                    cur.update(challenger=None, count=0)
                hist.append({"cycle": cyc, "station": st, "band": band, "pick": cur["member"], "ready": ready,
                             "leader": leader, "leader_skill": round(float(elig.loc[leader, "skill_ew"]), 3) if leader else None,
                             "pick_skill": None if np.isnan(elig["skill_ew"].get(cur["member"], np.nan))
                             else round(float(elig["skill_ew"][cur["member"]]), 3),
                             "challenger": cur["challenger"], "count": cur["count"]})
    return state, pd.DataFrame(hist)


# --- diagnostic tables -----------------------------------------------------------------------------
def r3(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 3)


def _period(span):
    return f"0-{int(span)} h" if span == span and span else ""


def tables(rec, frec, station=None):
    """The three tables for one station (or, station None, the mean over every station whose
    observations are its own) as DataFrames ordered by SS-Skill over the forecast period."""
    r = rec[rec["station"] == station] if station else rec[~rec["station"].isin(SUSPECT_OBS)]
    f = frec[frec["station"] == station] if station else frec[~frec["station"].isin(SUSPECT_OBS)]
    full = r[r["band"] == FULL]
    if full.empty:
        return None
    agg = {"skill_ew": "mean", "r2_ew": "mean", "bias_ew": "mean", "mae_ew": "mean", "rmse_ew": "mean",
           "span_h": "median", "n": "sum"}
    base = full.groupby("member").agg(agg).sort_values("skill_ew", ascending=False)
    order = list(base.index)

    def lead(m):
        return {"forecast": NAMES.get(m, m), "member": m, "period": _period(base.loc[m, "span_h"]),
                "ss_skill": r3(base.loc[m, "skill_ew"])}

    t1 = pd.DataFrame([{**lead(m), "r2": r3(base.loc[m, "r2_ew"]), "bias_ft": r3(base.loc[m, "bias_ew"]),
                        "mae_ft": r3(base.loc[m, "mae_ew"]), "rmse_ft": r3(base.loc[m, "rmse_ew"]),
                        "scores": int(base.loc[m, "n"])} for m in order])
    seg = r[r["band"].isin([b for b, _, _ in SEGMENTS])].groupby(["member", "band"]).agg(agg)
    rows2 = []
    for m in order:
        row = lead(m)
        for b, _, _ in SEGMENTS:
            v = seg.loc[(m, b)] if (m, b) in seg.index else None
            for k, c in (("r2", "r2_ew"), ("bias_ft", "bias_ew"), ("mae_ft", "mae_ew"), ("rmse_ft", "rmse_ew"),
                         ("ss_skill", "skill_ew")):
                row[f"{b}h_{k}"] = None if v is None else r3(v[c])
        rows2.append(row)
    t2 = pd.DataFrame(rows2)
    if station:
        fa = f.set_index("member")
    else:
        fa = None
        if not f.empty:
            num = f.drop(columns=["station"]).copy()
            n = num.groupby("member")["high_waters"].sum()
            fa = num.groupby("member").sum(numeric_only=True)
            for k in ("peak_error_ft", "peak_abs_error_ft", "timing_error_min", "timing_abs_error_min"):
                fa[k] = (f[k] * f["high_waters"]).groupby(f["member"]).sum() / n.replace(0, np.nan)
    rows3 = []
    for m in order:
        row = lead(m)
        v = fa.loc[m] if fa is not None and m in fa.index else None
        row["high_waters"] = None if v is None else int(v["high_waters"])
        for k in ("peak_error_ft", "peak_abs_error_ft"):
            row[k] = None if v is None else r3(v[k])
        for k in ("timing_error_min", "timing_abs_error_min"):
            row[k] = None if v is None or v[k] != v[k] else int(round(v[k]))
        for c in flood.CATEGORIES:
            for k in ("hit", "miss", "fa"):
                row[f"{c}_{k}"] = None if v is None else int(v[f"{c}_{k}"])
        rows3.append(row)
    return t1, t2, pd.DataFrame(rows3)


def _cell(v, fmt="%.3f"):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "<td></td>"
    return f"<td>{fmt % v if isinstance(v, float) else v}</td>"


def tables_html(t, heading_level=2):
    """The three tables as HTML (the ens72 pages and the skill page)."""
    if t is None:
        return "<p>No verified forecasts yet: the tables fill in once 72 hours of observations follow a run.</p>"
    t1, t2, t3 = t
    hn = f"h{heading_level}"

    def lead_cells(r):
        cls = ' class="ss"' if r["member"] in ("primary", "stormsense") else ""
        return cls, f'<td class="l">{H.escape(r["forecast"])}</td><td class="l">{r["period"]}</td>{_cell(r["ss_skill"])}'

    head = '<th class="l">Forecast</th><th class="l">Forecast period</th><th>SS-Skill</th>'
    rows = []
    for _, r in t1.iterrows():
        cls, lc = lead_cells(r)
        rows.append(f"<tr{cls}>{lc}{_cell(r['r2'])}{_cell(r['bias_ft'])}{_cell(r['mae_ft'])}{_cell(r['rmse_ft'])}"
                    f"{_cell(r['scores'], '%d')}</tr>")
    out = [f"<{hn}>1. Forecast skill over each forecast's period</{hn}>",
           f'<div class="wrap"><table><thead><tr>{head}<th>R&sup2;</th><th>Bias (ft)</th><th>MAE (ft)</th>'
           f'<th>RMSE (ft)</th><th>Runs scored</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>']
    seg_head = "".join(f'<th colspan="5">{b} h</th>' for b, _, _ in SEGMENTS)
    sub = "".join("<th>R&sup2;</th><th>Bias</th><th>MAE</th><th>RMSE</th><th>SS</th>" for _ in SEGMENTS)
    rows = []
    for _, r in t2.iterrows():
        cls, lc = lead_cells(r)
        cells = "".join(_cell(r[f"{b}h_{k}"]) for b, _, _ in SEGMENTS
                        for k in ("r2", "bias_ft", "mae_ft", "rmse_ft", "ss_skill"))
        rows.append(f"<tr{cls}>{lc}{cells}</tr>")
    out += [f"<{hn}>2. Skill in each 6-hour segment of the forecast</{hn}>",
            f'<div class="wrap"><table><thead><tr><th class="l" rowspan="2">Forecast</th><th class="l" rowspan="2">'
            f'Forecast period</th><th rowspan="2">SS-Skill</th>{seg_head}</tr><tr>{sub}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>']
    cat_head = "".join(f'<th colspan="3">{c.capitalize()}</th>' for c in flood.CATEGORIES)
    sub = "".join("<th>Hit</th><th>Miss</th><th>False alarm</th>" for _ in flood.CATEGORIES)
    rows = []
    for _, r in t3.iterrows():
        cls, lc = lead_cells(r)
        cells = (_cell(r["high_waters"], "%d") + _cell(r["peak_error_ft"]) + _cell(r["peak_abs_error_ft"])
                 + _cell(r["timing_error_min"], "%d") + _cell(r["timing_abs_error_min"], "%d")
                 + "".join(_cell(r[f"{c}_{k}"], "%d") for c in flood.CATEGORIES for k in ("hit", "miss", "fa")))
        rows.append(f"<tr{cls}>{lc}{cells}</tr>")
    out += [f"<{hn}>3. High waters and flood stages</{hn}>",
            f'<div class="wrap"><table><thead><tr><th class="l" rowspan="2">Forecast</th><th class="l" rowspan="2">'
            f'Forecast period</th><th rowspan="2">SS-Skill</th><th rowspan="2">High waters</th>'
            f'<th rowspan="2">Peak-height error (ft)</th><th rowspan="2">Peak-height |error| (ft)</th>'
            f'<th rowspan="2">Peak-timing error (min)</th><th rowspan="2">Peak-timing |error| (min)</th>{cat_head}</tr>'
            f'<tr>{sub}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>']
    return "\n".join(out)


TABLES_NOTE = (f"SS-Skill = 0.25[R&sup2; + e<sup>&minus;|Bias|/&sigma;</sup> + e<sup>&minus;MAE/&sigma;</sup> + "
               f"e<sup>&minus;RMSE/&sigma;</sup>], &sigma; the population SD of the observations in the window: 1 is "
               f"perfect, about 0.45 is no skill. Each value is the mean over the runs verified in the last {WINDOW_DAYS} "
               f"days, weighted toward the newest (e-folding {EFOLD_DAYS:g} days); rows are ordered by SS-Skill over "
               f"each forecast's own period. Bias and errors are forecast minus observed. High waters: every observed "
               f"high tide in a forecast's period, paired with the forecast's highest water within 3 hours; a high tide "
               f"is counted once for each run that forecast it. Flood stages are the NWS Action, Minor, Moderate and "
               f"Major levels: hit = both reached it, miss = the water reached it and the forecast did not, false "
               f"alarm = the forecast reached it and the water did not.")


def write_tables(folder, name, t):
    """<name>_skill_{overall,segments,peaks}.csv and one <name>_skill.json."""
    if t is None:
        return
    os.makedirs(folder, exist_ok=True)
    doc = {"window_days": WINDOW_DAYS, "efold_days": EFOLD_DAYS}
    for key, df in zip(("overall", "segments", "peaks"), t):
        df.drop(columns=["member"]).to_csv(os.path.join(folder, f"{name}_skill_{key}.csv"), index=False)
        doc[key] = json.loads(df.to_json(orient="records"))
    json.dump(doc, open(os.path.join(folder, f"{name}_skill.json"), "w"), separators=(",", ":"))


def write_plain_scores(path, pub, limit=None):
    """scores.csv: the newest whole cycles of pub that fit under PLAIN_MAX_BYTES. Returns the first cycle kept."""
    limit = PLAIN_MAX_BYTES if limit is None else limit
    text = pub.to_csv(index=False, float_format="%.4f")
    if len(text.encode()) > limit and not pub.empty:
        lines = text.splitlines(keepends=True)
        sizes = np.array([len(x.encode()) for x in lines[1:]])
        cyc = pub["cycle"].to_numpy()
        order = np.argsort(cyc, kind="stable")[::-1]          # newest rows first
        room = limit - len(lines[0].encode())
        used, keep, first = 0, [], None
        for c in sorted(set(cyc), reverse=True):               # add whole cycles, newest first
            idx = order[cyc[order] == c]
            n = int(sizes[idx].sum())
            if used + n > room:
                break
            used += n
            keep.extend(idx.tolist())
            first = c
        text = lines[0] + "".join(lines[i + 1] for i in sorted(keep))
    else:
        first = pub["cycle"].min() if not pub.empty else None
    with open(path, "w") as fh:
        fh.write(text)
    return first


# --- outputs -----------------------------------------------------------------------------------------
def write(out, scores, floods, runs, now):
    """The skill/ folder. Returns (pick document, recent skill, recent flood scores)."""
    os.makedirs(out, exist_ok=True)
    cut = f"{now - pd.Timedelta(days=PUBLISH_DAYS):%Y-%m-%dT%HZ}"
    pub = scores[scores["cycle"] >= cut]
    pub.to_csv(os.path.join(out, "scores.csv.gz"), index=False, float_format="%.4f", compression="gzip")
    write_plain_scores(os.path.join(out, "scores.csv"), pub)
    floods[floods["cycle"] >= cut].to_csv(os.path.join(out, "flood_scores.csv"), index=False, float_format="%.4f")
    state, hist = replay(scores, runs)
    hist.to_csv(os.path.join(out, "picks_history.csv"), index=False)
    rec = recent(scores, now, bands=[b for b, _, _ in BANDS + SEGMENTS] + [FULL])
    rec.to_csv(os.path.join(out, "summary.csv"), index=False, float_format="%.4f")
    frec = recent_floods(floods, now)
    frec.to_csv(os.path.join(out, "flood_summary.csv"), index=False, float_format="%.4f")
    last = hist[hist["cycle"] == hist["cycle"].max()] if not hist.empty else hist
    picks = {}
    for _, h in last.iterrows():
        cur = state[(h["station"], h["band"])]
        picks.setdefault(h["station"], {})[h["band"]] = {
            "member": h["pick"], "since": cur["since"], "ready": bool(h["ready"]), "skill": h["pick_skill"],
            "leader": h["leader"], "leader_skill": h["leader_skill"],
            "challenger": h["challenger"], "challenger_updates": int(h["count"])}
    rule = (f"Per station and lead band (hours after the last observation), the member with the best recent skill "
            f"S = 0.25[R2 + e^(-|Bias|/sd) + e^(-MAE/sd) + e^(-RMSE/sd)] (mean over {WINDOW_DAYS} days, weighted "
            f"exp(-age/{EFOLD_DAYS:g} d)); it must lead the current pick by {MARGIN} for {CONFIRM} updates running. "
            f"P-ETSS until {READY_DAYS} days of verified runs exist and the member has {MIN_RUNS} scores; "
            f"Tidewatch, the baseline and the tide alone are never picked; stations whose archived observations were "
            f"another gauge's ({', '.join(sorted(SUSPECT_OBS))}) stay on P-ETSS.")
    doc = {"updated": now.strftime("%Y-%m-%d %H:%M UTC"), "default": DEFAULT, "bands_h": list(PICK_BANDS),
           "rule": rule, "names": NAMES, "stations": picks,
           "ready_stations": sorted(st for st, b in picks.items() if any(v["ready"] for v in b.values()))}
    json.dump(doc, open(os.path.join(out, "pick.json"), "w"), indent=1)
    summ = {}
    for _, r in rec.iterrows():
        summ.setdefault(r["station"], {}).setdefault(r["band"], {})[r["member"]] = {
            "n": int(r["n"]), "skill": r3(r["skill_ew"]), "r2": r3(r["r2_ew"]), "rmse_ft": r3(r["rmse_ew"]),
            "mae_ft": r3(r["mae_ew"]), "bias_ft": r3(r["bias_ew"])}
    json.dump({"updated": doc["updated"], "window_days": WINDOW_DAYS, "efold_days": EFOLD_DAYS, "stations": summ},
              open(os.path.join(out, "summary.json"), "w"), indent=1)
    tdir = os.path.join(out, "tables")
    allst = tables(rec, frec)
    write_tables(tdir, "ALL", allst)
    stations = sorted(set(rec["station"]))
    for st in stations:
        write_tables(tdir, st, tables(rec, frec, st))
    write_page(out, scores, picks, doc, allst, stations)
    return doc, rec, frec


def write_page(out, scores, picks, doc, allst, stations):
    ncyc = scores["cycle"].nunique() if not scores.empty else 0
    links = " ".join(f'<a href="../ens72/{st}_ens72_MLLW.html#skill">{st}</a>' for st in stations)
    moved = []
    for st in sorted(picks):
        p = picks[st]
        if any(v["member"] != DEFAULT for v in p.values()):
            moved.append(f"<li>{st}: " + "; ".join(f"{b} h {H.escape(NAMES.get(v['member'], v['member']))}"
                                                  for b, v in p.items()) + "</li>")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>StormSense Skill</title>
<style>
body{{font-family:Arial,'Liberation Sans',sans-serif;margin:0 auto;padding:16px;max-width:1200px;color:#1b2533;background:#fff}}
header img{{width:min(520px,100%);height:auto;display:block}}
h1{{font-size:1.5rem;margin:12px 0 4px;color:#14285a}} h2{{font-size:1.1rem;margin:20px 0 4px;color:#14285a}}
p,li{{margin:6px 0;line-height:1.45}} .disc{{color:#555;font-size:.88rem}}
.wrap{{overflow-x:auto;margin-top:6px}} table{{border-collapse:collapse;font-size:.82rem}}
th,td{{border:1px solid #d5dbe3;padding:3px 6px;text-align:right;white-space:nowrap}}
th.l,td.l{{text-align:left}} thead th{{background:#eef6fa;text-align:center}} tr.ss td{{background:#e0fbfc}} a{{color:#0d6e8a}}
</style></head>
<body><header><img src="../stormsense_logo.png" alt="StormSense"></header>
<h1>StormSense Forecast Skill</h1>
<p>How each forecast did against the water levels the gauges recorded after it (updated {doc['updated']}; {ncyc} archived
runs scored). The tables below are the mean over every station whose observations are its own; each station's tables are on
its ensemble chart: {links}.</p>
<p class="disc">{TABLES_NOTE}</p>
<p class="disc">{H.escape(doc['rule'])}</p>
<p class="disc">Stations whose primary guidance is not P-ETSS in some lead band:</p><ul class="disc">{''.join(moved) or '<li>none yet</li>'}</ul>
<p class="disc">Downloads: <a href="scores.csv.gz">every score (last {PUBLISH_DAYS} days, gzip)</a>, <a href="scores.csv">the newest scores (csv, up
to {PLAIN_MAX_BYTES // 2**20} MB)</a>, <a href="flood_scores.csv">high-water
scores</a>, <a href="summary.csv">recent skill</a>, <a href="flood_summary.csv">recent high-water skill</a>,
<a href="summary.json">(json)</a>, <a href="pick.json">guidance pick</a>, <a href="picks_history.csv">pick history</a>,
<a href="tables/">tables (csv, json)</a>.</p>
<p class="disc">Disclaimer: StormSense&trade; Predictive Plots are computer-generated operational storm tide forecasts;
the user assumes any and all risk.</p>
{tables_html(allst)}
</body></html>
"""
    open(os.path.join(out, "index.html"), "w").write(page)


def run(out, work, now, truth=None, levels=None, navd88=None, local=(), rescore_all=False):
    """Score, pick and write skill/. Returns (pick document, recent skill, recent flood scores) and
    leaves skill_scores.csv.gz, flood_scores.csv.gz and skill_runs.csv in work for the release."""
    old_scores, old_floods, old_runs, tars = gather(work, list(local), now, rescore_all)
    new_scores, new_floods, new_runs = score_runs(tars, truth, levels, navd88)
    rescored = set(new_runs["cycle"]) if not new_runs.empty else set()
    scores = merge(old_scores, new_scores, rescored)
    floods = merge(old_floods, new_floods, rescored, ("cycle", "station", "member"))
    runs = merge(old_runs, new_runs, rescored, ("cycle", "station"))
    os.makedirs(work, exist_ok=True)
    scores.to_csv(os.path.join(work, "skill_scores.csv.gz"), index=False, float_format="%.4f", compression="gzip")
    floods.to_csv(os.path.join(work, "flood_scores.csv.gz"), index=False, float_format="%.4f", compression="gzip")
    runs.to_csv(os.path.join(work, "skill_runs.csv"), index=False)
    doc, rec, frec = write(out, scores, floods, runs, now)
    moved = {f"{st} {b}": v["member"] for st, bb in doc["stations"].items() for b, v in bb.items() if v["member"] != DEFAULT}
    print(f"skill: {len(scores)} scores from {scores['cycle'].nunique()} runs; "
          f"ready at {len(doc['ready_stations'])} stations; picks off P-ETSS: {moved or 'none'}")
    return doc, rec, frec
