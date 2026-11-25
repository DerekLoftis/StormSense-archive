"""CREST feeds: the same numbers as the pages, as CSV and GeoJSON for the ArcGIS Experience Builder
dashboard (crest/arcgis_push.py loads them into hosted feature layers) or any other tool.

    stations.geojson / .csv      one point per station: skill of the main guidance, best guidance,
                                 cone hit rate, gauge coverage, events, links to its pages
    daily_skill.geojson / .csv   one point per station and day (time-enabled): skill per guidance
                                 (0-36 h windows of the runs issued that day), highest observed water
    leaderboard.csv              station x guidance x lead band: S, Bias, MAE, RMSE, R2, runs
    lead_decay.csv               station x guidance x 6-hour lead segment: S, RMSE
    flood_events.geojson / .csv  one point per observed flood (Action or above): peak, category, and
                                 each main guidance's mean peak-height and timing error
    changes.csv                  dated model and pipeline changes
"""
import csv
import json
import os

import numpy as np
import pandas as pd

from . import stats

BASE = os.environ.get("CREST_BASE_URL", "https://derekloftis.github.io/StormSense-archive/crest/")
LATEST = "https://derekloftis.github.io/StormSense-latest/fore72/"
MAIN = ["primary", "stormsense", "petss", "nws", "tidewatch", "stofs2d", "etss"]


def _csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _geojson(path, rows, lon="lon", lat="lat"):
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r[lon], r[lat]]},
              "properties": {k: v for k, v in r.items() if k not in (lon, lat)}}
             for r in rows if r.get(lon) is not None and r.get(lat) is not None]
    json.dump({"type": "FeatureCollection", "features": feats}, open(path, "w"), separators=(",", ":"), allow_nan=False)


def write(out, net, docs, sc, obs, levels, changes, period):
    os.makedirs(out, exist_ok=True)
    pid = period["id"]
    # stations
    rows = []
    for s in net["stations"]:
        row = {"station": s["id"], "name": s["name"], "lon": s["lon"], "lat": s["lat"], "period": pid,
               "best": s["best"] or "", "best_name": stats.NAMES.get(s["best"], "") if s["best"] else "",
               "cone_pct": round(s["cone"] * 100, 1) if s["cone"] is not None else None,
               "obs_coverage_pct": s["cov"], "runs": s["runs"], "events": s["events"],
               "last_event": s["last_event"] or "", "suspect_obs": int(bool(s["suspect"])), "verdict": s["verdict"],
               "crest_url": f"{BASE}crest_{s['id'].lower()}.html",
               "forecast_url": f"{LATEST}{s['id']}_fore72_NAVD.html"}
        for m in MAIN:
            row[f"S_{m}"] = s["S"].get(m)
        rows.append(row)
    fields = list(rows[0]) if rows else ["station"]
    _csv(os.path.join(out, "stations.csv"), rows, fields)
    _geojson(os.path.join(out, "stations.geojson"), rows)
    loc = {s["id"]: (s["lon"], s["lat"]) for s in net["stations"]}

    # daily skill (time-enabled)
    rows = []
    for d in docs:
        dl = d["daily"]
        o = obs.get(d["station"])
        lv = levels.get(d["station"], {}) or {}
        for i, day in enumerate(dl["days"]):
            row = {"station": d["station"], "date": day + "T12:00:00Z", "lon": loc[d["station"]][0], "lat": loc[d["station"]][1]}
            for m in MAIN:
                row[f"S_{m}"] = dl.get(m, [None] * len(dl["days"]))[i]
            mx = None
            if o is not None and len(o):
                w = o[(o.index >= pd.Timestamp(day, tz="UTC")) & (o.index < pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1))]
                mx = round(float(w.max()), 2) if len(w) else None
            row["max_obs_ft_mllw"] = mx
            row["category"] = next((c for c in reversed(stats.CATS) if mx is not None and lv.get(c) is not None and mx >= lv[c]), "")
            rows.append(row)
    fields = ["station", "date", "lon", "lat"] + [f"S_{m}" for m in MAIN] + ["max_obs_ft_mllw", "category"]
    _csv(os.path.join(out, "daily_skill.csv"), rows, fields)
    _geojson(os.path.join(out, "daily_skill.geojson"), rows)

    # leaderboard and decay
    rows, drows = [], []
    for d in docs:
        for m, bands in d["lb"]["all"].items():
            for b, v in bands.items():
                rows.append({"station": d["station"], "member": m, "member_name": stats.NAMES.get(m, m), "band": b,
                             "period": pid, **{k: v.get(k) for k in ("S", "Bias", "MAE", "RMSE", "R2", "n")}})
        for m, seg in d["decay"].items():
            for i, v in enumerate(seg):
                drows.append({"station": d["station"], "member": m, "member_name": stats.NAMES.get(m, m),
                              "segment": stats.SEGMENTS[i], "lead_mid_h": i * 6 + 3, "S": v,
                              "RMSE": (d["decay_rmse"].get(m) or [None] * 12)[i], "period": pid})
    _csv(os.path.join(out, "leaderboard.csv"), rows,
         ["station", "member", "member_name", "band", "period", "S", "Bias", "MAE", "RMSE", "R2", "n"])
    _csv(os.path.join(out, "lead_decay.csv"), drows,
         ["station", "member", "member_name", "segment", "lead_mid_h", "S", "RMSE", "period"])

    # flood events
    rows = []
    for d in docs:
        for e in d["events"]:
            row = {"station": d["station"], "lon": loc[d["station"]][0], "lat": loc[d["station"]][1],
                   "peak_time": e["peak_t"], "peak_ft_mllw": e["peak"], "category": e["category"]}
            for m in MAIN:
                b = e["by"].get(m) or {}
                row[f"peak_err_{m}"] = b.get("pe")
                row[f"timing_err_min_{m}"] = b.get("te")
                row[f"runs_{m}"] = b.get("n")
            rows.append(row)
    fields = ["station", "lon", "lat", "peak_time", "peak_ft_mllw", "category"] + \
        [f"{k}_{m}" for m in MAIN for k in ("peak_err", "timing_err_min", "runs")]
    _csv(os.path.join(out, "flood_events.csv"), rows, fields)
    _geojson(os.path.join(out, "flood_events.geojson"), rows)
    _csv(os.path.join(out, "changes.csv"), changes, ["date", "label", "detail"])
    print(f"feeds: {len(net['stations'])} stations, {len(rows)} flood events -> {out}")
