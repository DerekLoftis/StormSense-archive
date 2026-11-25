"""CREST statistics: everything a station page and the network page show, for one period.

Scores come from stormsense/skill.py (every run x station x member x window, Derek's skill formula
S = 0.25[R2 + e^(-|Bias|/sd) + e^(-MAE/sd) + e^(-RMSE/sd)], sd the population SD of the observations in
the window). CREST averages them over the runs issued in the period. Two things are computed here
from the archived series themselves, with the same formula: the what-if ensemble mean and the
flood-event peak errors.
"""
import math
import warnings

import numpy as np
import pandas as pd

from stormsense import skill as SK

BANDS = ["0-12", "12-36", "36-72", "0-36", "0-72", "full"]
SEGMENTS = [s for s, _, _ in SK.SEGMENTS]
ORDER = ["primary", "stormsense", "petss", "etss", "stofs2d", "stofs3d", "cbofs", "nwm_short", "nwm_medium", "nws",
         "psurge", "tidewatch", "ens_mean", "stormsense_raw", "baseline", "tide"]
NAMES = dict(SK.NAMES, ens_mean="Ensemble mean (what-if)")
# Guidance that can be "best": everything that forecasts, not the references every forecast should beat.
NOT_RANKED = {"baseline", "tide", "stormsense_raw", "ens_mean"}
# Members averaged into the what-if ensemble mean (the forecasts the pick chooses from).
BLEND = ["stormsense", "petss", "etss", "stofs2d", "stofs3d", "cbofs", "nwm_medium", "nws", "psurge"]
CATS = ["action", "minor", "moderate", "major"]
NEAR_FT = 0.0        # a run is "high water" when the observed water reached Action - NEAR_FT within its 72 h
EVENT_GAP_H = 12     # observed exceedances of Action closer than this are one event
PEAK_WINDOW_H = 3    # a forecast's peak is its highest water within this of the observed peak
GOOD, NO_SKILL = 0.75, 0.45


def r(v, n=3):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, n) if math.isfinite(v) else None


def ct(c):
    return pd.Timestamp(c[:10] + " " + c[11:13] + ":00", tz="UTC")


def in_period(df, start, end, col="cycle"):
    if df.empty:
        return df
    t = pd.to_datetime(df[col].str.slice(0, 10) + " " + df[col].str.slice(11, 13) + ":00", utc=True)
    return df[(t >= start) & (t < end)]


# --- the what-if ensemble mean, scored like skill.py ------------------------------------------------------
def ens_mean_rows(runs, obs, stations):
    """Score rows (skill.py's columns) for the mean of the BLEND members, where 3 or more are present."""
    rows = []
    for cyc, run in runs.items():
        for st, d in run["stations"].items():
            if stations and st not in stations:
                continue
            tr = obs.get(st)
            if tr is None or tr.empty:
                continue
            mem = [np.array(d["members"][m], dtype=float) for m in BLEND if m in d["members"]]
            if len(mem) < 3:
                continue
            a = np.vstack(mem)
            n = np.sum(~np.isnan(a), axis=0)
            with np.errstate(all="ignore"):
                y_all = np.where(n >= 3, np.nanmean(a, axis=0), np.nan)
            t0 = pd.Timestamp(d["t0"], unit="ms", tz="UTC")
            grid = pd.date_range(t0, periods=len(y_all), freq=f"{SK.STEP_MIN}min")
            lead = ((grid - t0).total_seconds() / 3600.0).to_numpy()
            x_all = tr.reindex(grid).to_numpy(float)
            have = np.where(~np.isnan(y_all) & (lead > 0))[0]
            if not len(have):
                continue
            span = int(np.floor(lead[have[-1]] / 6.0 + 1e-6) * 6)
            last_h = (tr.index[-1] - t0).total_seconds() / 3600.0
            for band, lo, hi, sel in SK._windows(lead, last_h, span):
                ok = sel & ~np.isnan(x_all) & ~np.isnan(y_all)
                if ok.sum() < SK.MIN_COVERAGE * (hi - lo) * 60 / SK.STEP_MIN:
                    continue
                s = SK.statistics(x_all[ok], y_all[ok])
                rows.append({"cycle": cyc, "station": st, "member": "ens_mean", "band": band, "t0": t0.isoformat(),
                             "span_h": hi - lo, "N": int(ok.sum()), "R2": s["R2"], "Bias": s["Bias"], "MAE": s["MAE"],
                             "RMSE": s["RMSE"], "SDobs": s["SDobs"], "Skill": SK.skill_score(s), "PeakError": s["PeakError"]})
    return pd.DataFrame(rows)


# --- regimes ---------------------------------------------------------------------------------------------
def regimes(sc, obs, levels):
    """{cycle: "high" | "calm"} for one station: "high" when the observed water within the run's 72 h
    reached NWS Action (or, without NWS categories, the period's 90th percentile of 72 h maxima)."""
    if sc.empty or obs is None or obs.empty:
        return {}
    t0 = sc.groupby("cycle")["t0"].first()
    mx = {}
    for cyc, t in t0.items():
        t = pd.Timestamp(t)
        w = obs[(obs.index > t) & (obs.index <= t + pd.Timedelta(hours=72))]
        if len(w) > 100:
            mx[cyc] = float(w.max())
    if not mx:
        return {}
    act = (levels or {}).get("action")
    thr = act - NEAR_FT if act is not None else float(np.percentile(list(mx.values()), 90))
    return {c: ("high" if v >= thr else "calm") for c, v in mx.items()}


# --- tables ----------------------------------------------------------------------------------------------
def leaderboard(sc):
    """{member: {band: {S, Bias, MAE, RMSE, R2, n}}} averaged over runs; plus {member: span hours}."""
    out, span = {}, {}
    s = sc[sc["band"].isin(BANDS)]
    for (m, b), g in s.groupby(["member", "band"]):
        out.setdefault(m, {})[b] = {"S": r(g["Skill"].mean()), "Bias": r(g["Bias"].mean()), "MAE": r(g["MAE"].mean()),
                                    "RMSE": r(g["RMSE"].mean()), "R2": r(g["R2"].mean()), "n": int(g["Skill"].notna().sum())}
    for m, g in s[s["band"] == "full"].groupby("member"):
        span[m] = int(g["span_h"].median())
    return out, span


def decay(sc, col="Skill"):
    s = sc[sc["band"].isin(SEGMENTS)]
    out = {}
    for m, g in s.groupby("member"):
        means = g.groupby("band")[col].mean()
        out[m] = [r(means.get(seg)) for seg in SEGMENTS]
    return out


def daily(sc, band="0-36"):
    s = sc[sc["band"] == band].copy()
    if s.empty:
        return {"days": []}
    s["day"] = s["cycle"].str.slice(0, 10)
    days = sorted(s["day"].unique())
    out = {"days": days}
    for m, g in s.groupby("member"):
        means = g.groupby("day")["Skill"].mean()
        out[m] = [r(means.get(d)) for d in days]
    return out


def cone(sc):
    s = sc[sc["ConeHit"].notna()] if "ConeHit" in sc else sc.iloc[0:0]
    out = {}
    for m, g in s.groupby("member"):
        segs = g[g["band"].isin(SEGMENTS)].groupby("band")["ConeHit"].mean()
        bands = g[g["band"].isin(["0-12", "12-36", "36-72", "0-72"])].groupby("band")["ConeHit"].mean()
        out[m] = {"segments": [r(segs.get(seg), 3) for seg in SEGMENTS],
                  "bands": {b: r(bands.get(b), 3) for b in ["0-12", "12-36", "36-72", "0-72"]},
                  "n": int(g[g["band"] == "0-72"]["ConeHit"].notna().sum())}
    return out


def picks(ph, sc):
    """How the adaptive pick did: counts per band, and the skill it achieved against the best member
    in hindsight (the highest-scoring pickable member of each run and band)."""
    out = {"counts": {}, "achieved": {}, "best": {}, "runs": {}}
    if ph is None or ph.empty:
        return out
    cand = sc[~sc["member"].isin(SK.REFERENCE | NOT_RANKED) & sc["band"].isin(SK.PICK_BANDS)]
    for b, g in ph.groupby("band"):
        out["counts"][b] = {k: int(v) for k, v in g["pick"].value_counts().items()}
        c = cand[cand["band"] == b]
        got, best = [], []
        for cyc, pick in zip(g["cycle"], g["pick"]):
            cc = c[c["cycle"] == cyc]
            if cc.empty:
                continue
            pv = cc[cc["member"] == pick]["Skill"]
            if pv.empty or not np.isfinite(pv.iloc[0]):
                continue
            got.append(float(pv.iloc[0]))
            best.append(float(cc["Skill"].max()))
        out["achieved"][b], out["best"][b], out["runs"][b] = r(np.mean(got) if got else None), r(np.mean(best) if best else None), len(got)
    return out


def floods(fs):
    out = []
    if fs is None or fs.empty:
        return out
    for m, g in fs.groupby("member"):
        cats = {c: [int(g[f"{c}_hit"].sum()), int(g[f"{c}_miss"].sum()), int(g[f"{c}_fa"].sum())] for c in CATS}
        out.append({"member": m, "runs": int(g["cycle"].nunique()), "hw": int(g["high_waters"].sum()),
                    "pe": r(g["peak_error_ft"].mean(), 2), "ape": r(g["peak_abs_error_ft"].mean(), 2),
                    "te": r(g["timing_abs_error_min"].mean(), 0), "cats": cats})
    return sorted(out, key=lambda x: (x["ape"] is None, x["ape"]))


def events(obs, levels, runs, st):
    """Observed floods (Action or above) and how each guidance forecast their peaks."""
    act = (levels or {}).get("action")
    if act is None or obs is None or obs.empty:
        return []
    above = obs[obs >= act]
    if above.empty:
        return []
    groups, cur = [], [above.index[0]]
    for t in above.index[1:]:
        if (t - cur[-1]) > pd.Timedelta(hours=EVENT_GAP_H):
            groups.append(cur)
            cur = []
        cur.append(t)
    groups.append(cur)
    out = []
    for g in groups:
        w = obs[(obs.index >= g[0]) & (obs.index <= g[-1])]
        pt, pv = w.idxmax(), float(w.max())
        cat = [c for c in CATS if (levels.get(c) is not None and pv >= levels[c])][-1]
        by = {}
        for cyc, run in runs.items():
            d = run["stations"].get(st)
            if not d:
                continue
            t0 = pd.Timestamp(d["t0"], unit="ms", tz="UTC")
            lead = (pt - t0).total_seconds() / 3600
            if not (0 < lead <= 72):
                continue
            grid = pd.date_range(t0, periods=len(next(iter(d["members"].values()))), freq=f"{SK.STEP_MIN}min")
            near = (grid >= pt - pd.Timedelta(hours=PEAK_WINDOW_H)) & (grid <= pt + pd.Timedelta(hours=PEAK_WINDOW_H))
            for m, v in d["members"].items():
                if m in ("tide",) or m.endswith(("_p10", "_p90")):
                    continue
                a = np.array(v, dtype=float)[near]
                if np.isnan(a).all():
                    continue
                i = int(np.nanargmax(a))
                by.setdefault(m, []).append([round(lead, 1), round(float(a[i]) - pv, 3),
                                             int((grid[near][i] - pt).total_seconds() / 60), bool(a[i] >= act)])
        summ = {}
        for m, lst in by.items():
            e = np.array([x[1] for x in lst])
            summ[m] = {"n": len(lst), "pe": r(e.mean(), 2), "ape": r(np.abs(e).mean(), 2),
                       "te": r(np.mean([abs(x[2]) for x in lst]), 0), "hit": r(np.mean([x[3] for x in lst]), 2), "runs": lst}
        out.append({"start": g[0].isoformat(), "end": g[-1].isoformat(), "peak_t": pt.isoformat(), "peak": round(pv, 2),
                    "category": cat, "by": summ})
    return out


def health(hx, st, start, end):
    if hx is None or hx.empty:
        return {"days": []}
    h = hx[hx["station"] == st].copy()
    d = pd.to_datetime(h["day"], utc=True)
    h = h[(d >= start.normalize()) & (d < end)].sort_values("day")
    flags = (h["flag_range"] + h["flag_spike"] + h["flag_flatline"]).astype(int)
    return {"days": h["day"].tolist(), "cov": [r(x, 1) for x in h["coverage_pct"]], "flags": flags.tolist(),
            "source": h["source"].fillna("").tolist(), "error": h["error"].fillna("").astype(str).str.slice(0, 80).tolist()}


def pack(s, start, end, step_min=SK.STEP_MIN):
    grid = pd.date_range(start, end, freq=f"{step_min}min", inclusive="left")
    v = s.reindex(grid) if s is not None and len(s) else pd.Series(np.nan, index=grid)
    return {"t0": int(grid[0].value // 10**6), "dt": step_min * 60000, "v": [r(x) for x in v.to_numpy(float)]}


def workbench(runs, st, sc, every=5):
    """Each run's series (every `every`-th 6-minute value, i.e. 30 min) and its per-member scores."""
    full = sc[sc["band"].isin(["full", "0-72", "0-36"])]
    stats = {}
    for (cyc, m, b), g in full.groupby(["cycle", "member", "band"]):
        x = g.iloc[0]
        stats.setdefault(cyc, {}).setdefault(m, {})[b] = [r(x["Skill"]), r(x["Bias"]), r(x["MAE"]), r(x["RMSE"]), r(x["R2"])]
    out = []
    for cyc in sorted(runs):
        d = runs[cyc]["stations"].get(st)
        if not d:
            continue
        ser = {m: v[::every] for m, v in d["members"].items()}
        out.append({"cycle": cyc, "t0": d["t0"], "dt": SK.STEP_MIN * every * 60000, "series": ser,
                    "stats": stats.get(cyc, {}), "basis": runs[cyc].get("obs_basis")})
    # runs scored but not in the downloaded tarballs still list their scores
    have = {x["cycle"] for x in out}
    for cyc in sorted(set(stats) - have):
        t0 = sc[sc["cycle"] == cyc]["t0"].iloc[0]
        out.append({"cycle": cyc, "t0": int(pd.Timestamp(t0).value // 10**6), "series": {}, "stats": stats[cyc]})
    return sorted(out, key=lambda x: x["cycle"])


def report_card(lb, dec, cn, hl, ev):
    def s72(m):
        return ((lb.get(m) or {}).get("0-72") or {}).get("S")
    n72 = {m: ((lb.get(m) or {}).get("0-72") or {}).get("n", 0) for m in lb}
    nmax = max(n72.values()) if n72 else 0
    ranked = [m for m in lb if m not in NOT_RANKED and s72(m) is not None and n72[m] >= max(5, 0.5 * nmax)]
    best = max(ranked, key=s72) if ranked else None
    hz = 0
    for i, v in enumerate(dec.get("primary") or []):
        if v is None or v < GOOD:
            break
        hz = (i + 1) * 6
    cov = [x for x in hl.get("cov", []) if x is not None]
    pc = ((cn.get("primary") or {}).get("bands") or {}).get("0-72")
    card = {"best": best, "best_S": s72(best) if best else None, "best_n": n72.get(best, 0) if best else 0,
            "primary": s72("primary"), "petss": s72("petss"), "nws": s72("nws"), "horizon_h": hz,
            "cone": pc, "coverage": r(np.mean(cov), 1) if cov else None, "events": len(ev)}
    words = []
    if best:
        words.append(f"{NAMES.get(best, best)} scored best over 0-72 hours (S {card['best_S']:.3f}).")
    if card["primary"] is not None:
        if best == "primary":
            head = f"The published forecast was the best guidance here (S {card['primary']:.3f})"
        else:
            head = f"The published forecast scored S {card['primary']:.3f}"
        words.append(head + (f" and stayed good (S 0.75+) for the first {hz} hours." if hz else ", below good (S 0.75) from the first hours."))
    if pc is not None:
        words.append(f"Its 10-90% band held {pc * 100:.0f}% of the observations (80% is calibrated).")
    if card["coverage"] is not None and card["coverage"] < 90:
        words.append(f"The gauge record was thin ({card['coverage']:.0f}% of 6-minute readings), so treat these scores with care.")
    card["verdict"] = " ".join(words)
    return card


def station_doc(st, info, period, sc, fs, ph, runs, obs, levels, hx, changes):
    """Everything one station page shows for one period."""
    start, end = period["start_t"], period["end_t"]
    o = obs.get(st)
    reg = regimes(sc, o, levels)
    lb_all, span = leaderboard(sc)
    lbs = {"all": lb_all}
    counts = {"all": int(sc["cycle"].nunique())}
    for k in ("high", "calm"):
        cyc = [c for c, v in reg.items() if v == k]
        lbs[k], _ = leaderboard(sc[sc["cycle"].isin(cyc)])
        counts[k] = len(cyc)
    dec = decay(sc)
    cn = cone(sc)
    hl = health(hx, st, start, end)
    ev = events(o[(o.index >= start) & (o.index < end)] if o is not None else None, levels, runs, st)
    members = [m for m in ORDER if m in lb_all] + sorted(set(lb_all) - set(ORDER))
    obs_end = end + pd.Timedelta(days=3)
    return {
        "station": st, "name": info.get("name") or st, "lat": info.get("lat"), "lon": info.get("lon"),
        "navd": info.get("navd88"), "levels": levels or {}, "suspect": bool(info.get("suspect")),
        "added": info.get("added") or "", "source": info.get("source", ""),
        "period": {k: period[k] for k in ("id", "label", "start", "end")},
        "members": members, "names": {m: NAMES.get(m, m) for m in members},
        "lb": lbs, "regime_runs": counts, "span": span, "decay": dec, "decay_rmse": decay(sc, "RMSE"),
        "daily": daily(sc), "cone": cn, "picks": picks(ph, sc), "floods": floods(fs), "events": ev,
        "health": hl, "card": report_card(lb_all, dec, cn, hl, ev),
        "runs": workbench(runs, st, sc), "obs": pack(o, start - pd.Timedelta(days=1), obs_end),
        "changes": changes,
    }


def network_doc(period, docs, changes):
    """The map page's data: one summary per station, the network leaderboard and lead-time decay."""
    st_rows, per_member, wins, decs = [], {}, {}, {}
    for d in docs:
        lb = d["lb"]["all"]
        s72 = {m: (lb[m].get("0-72") or {}).get("S") for m in lb}
        best = d["card"]["best"]
        ev = d["events"]
        st_rows.append({"id": d["station"], "name": d["name"], "lat": d["lat"], "lon": d["lon"], "suspect": d["suspect"],
                        "S": {m: v for m, v in s72.items() if v is not None},
                        "bias": {m: (lb[m].get("0-72") or {}).get("Bias") for m in lb if (lb[m].get("0-72") or {}).get("Bias") is not None},
                        "best": best, "cone": d["card"]["cone"], "cov": d["card"]["coverage"],
                        "runs": d["regime_runs"]["all"], "events": len(ev),
                        "last_event": ev[-1]["peak_t"][:10] if ev else None,
                        "last_event_cat": ev[-1]["category"] if ev else None, "verdict": d["card"]["verdict"]})
        if d["suspect"]:
            continue
        for m, v in s72.items():
            if v is not None:
                per_member.setdefault(m, []).append(v)
        if best:
            wins[best] = wins.get(best, 0) + 1
        for m, v in d["decay"].items():
            decs.setdefault(m, []).append(v)
    lb = {m: {"S": r(np.mean(v)), "stations": len(v), "wins": wins.get(m, 0)} for m, v in per_member.items()}
    dec = {}
    for m, lst in decs.items():
        a = np.array([[np.nan if x is None else x for x in row] for row in lst], dtype=float)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            dec[m] = [r(x) for x in np.nanmean(a, axis=0)]
    members = [m for m in ORDER if m in lb] + sorted(set(lb) - set(ORDER))
    return {"period": {k: period[k] for k in ("id", "label", "start", "end")}, "stations": st_rows,
            "leaderboard": lb, "decay": dec, "members": members, "names": {m: NAMES.get(m, m) for m in members},
            "changes": changes}
