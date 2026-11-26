"""CREST feeds: the same numbers as the pages, as CSV and GeoJSON for the ArcGIS dashboard
(crest/arcgis_push.py loads them into hosted feature layers, crest/arcgis_dashboard.py builds the
dashboard on those layers) or any other tool. Columns, types and labels are in crest/schema.py.

    stations.geojson / .csv      one point per station: skill of the main guidance, best guidance,
                                 cone hit rate, gauge coverage, events, links to its pages
    daily_skill.geojson / .csv   one point per station and day (time-enabled): skill per guidance
                                 (0-36 h windows of the runs issued that day), highest observed water
    leaderboard.csv              station x guidance x lead band: S, Bias, MAE, RMSE, R2, runs
    lead_decay.csv               station x guidance x 6-hour lead segment: S, RMSE
    forecasts.csv                the forecast archive: one row per station, forecast and main guidance
                                 with its scores (whole forecast and per lead band), its own highest water
                                 and the flood category that reaches, and a link to its chart
    flood_events.geojson / .csv  one point per observed flood (Action or above): peak, category, and
                                 each main guidance's mean peak-height and timing error
    changes.csv                  dated model and pipeline changes
"""
import csv
import json
import os

import numpy as np
import pandas as pd

from . import schema, stats

BASE = os.environ.get("CREST_BASE_URL", "https://derekloftis.github.io/StormSense-archive/crest/")
LATEST = "https://derekloftis.github.io/StormSense-latest/fore72/"
MAIN = schema.MAIN


def _iso(t):
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    _csv(os.path.join(out, "stations.csv"), rows, schema.columns("stations"))
    _geojson(os.path.join(out, "stations.geojson"), rows)
    loc = {s["id"]: (s["lon"], s["lat"]) for s in net["stations"]}

    # daily skill (time-enabled)
    rows = []
    for d in docs:
        dl = d["daily"]
        o = obs.get(d["station"])
        lv = levels.get(d["station"], {}) or {}
        for i, day in enumerate(dl["days"]):
            row = {"station": d["station"], "name": d["name"], "date": day + "T12:00:00Z",
                   "lon": loc[d["station"]][0], "lat": loc[d["station"]][1]}
            for m in MAIN:
                row[f"S_{m}"] = dl.get(m, [None] * len(dl["days"]))[i]
            mx = None
            if o is not None and len(o):
                w = o[(o.index >= pd.Timestamp(day, tz="UTC")) & (o.index < pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1))]
                mx = round(float(w.max()), 2) if len(w) else None
            row["max_obs_ft_mllw"] = mx
            row["category"] = next((c for c in reversed(stats.CATS) if mx is not None and lv.get(c) is not None and mx >= lv[c]), "")
            rows.append(row)
    _csv(os.path.join(out, "daily_skill.csv"), rows, schema.columns("daily"))
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
                              "segment": stats.SEGMENTS[i], "lead": f"{i * 6:02d}-{i * 6 + 6:02d} h",
                              "lead_mid_h": i * 6 + 3, "S": v,
                              "RMSE": (d["decay_rmse"].get(m) or [None] * 12)[i], "period": pid})
    _csv(os.path.join(out, "leaderboard.csv"), rows, schema.columns("leaderboard"))
    _csv(os.path.join(out, "lead_decay.csv"), drows, schema.columns("decay"))
    frows = forecast_rows(docs, sc, period)
    _csv(os.path.join(out, "forecasts.csv"), frows, schema.columns("forecasts"))

    # flood events
    rows = []
    for d in docs:
        for e in d["events"]:
            row = {"station": d["station"], "name": d["name"], "lon": loc[d["station"]][0], "lat": loc[d["station"]][1],
                   "peak_time": _iso(e["peak_t"]), "peak_ft_mllw": e["peak"], "category": e["category"].capitalize()}
            for m in MAIN:
                b = e["by"].get(m) or {}
                row[f"peak_err_{m}"] = b.get("pe")
                row[f"timing_err_min_{m}"] = b.get("te")
                row[f"runs_{m}"] = b.get("n")
            p = e["by"].get("primary") or {}
            row["summary"] = f"Peak {e['peak']:.2f} ft MLLW" + (
                f"; published forecasts {p['pe']:+.2f} ft on the peak over {p['n']} runs" if p.get("pe") is not None else "")
            rows.append(row)
    _csv(os.path.join(out, "flood_events.csv"), rows, schema.columns("floods"))
    _geojson(os.path.join(out, "flood_events.geojson"), rows)
    # a change's day at 12:00 UTC, so a dashboard in any US time zone shows the same day
    _csv(os.path.join(out, "changes.csv"),
         [dict(c, date=c["date"] + "T12:00:00Z" if len(str(c.get("date", ""))) == 10 else c.get("date")) for c in changes],
         schema.columns("changes"))
    print(f"feeds: {len(net['stations'])} stations, {len(frows)} forecast rows, {len(rows)} flood events -> {out}")


def forecast_rows(docs, sc, period):
    """The forecast archive as a table: every forecast of the main guidance in the period, with its
    scores once the gauge has observed it. Unverified forecasts are listed too, so the newest run is there."""
    bands = {"full": "S", "0-12": "S_0_12", "12-36": "S_12_36", "36-72": "S_36_72", "0-36": "S_0_36"}
    got = {}
    if len(sc):
        s = sc[sc["member"].isin(MAIN) & sc["band"].isin(list(bands)) & sc["station"].isin([d["station"] for d in docs])]
        s = stats.in_period(s, period["start_t"], period["end_t"])
        for st, cyc, m, b, sk, bias, mae, rmse, r2, pk, span in zip(
                s["station"], s["cycle"], s["member"], s["band"], s["Skill"], s["Bias"], s["MAE"], s["RMSE"], s["R2"],
                s["PeakError"] if "PeakError" in s else [None] * len(s), s["span_h"] if "span_h" in s else [None] * len(s)):
            g = got.setdefault((st, cyc, m), {})
            g[bands[b]] = stats.r(sk)
            if b == "full":
                g.update(Bias=stats.r(bias), MAE=stats.r(mae), RMSE=stats.r(rmse), R2=stats.r(r2), peak_err_ft=stats.r(pk),
                         span_h=None if span is None or pd.isna(span) else int(span))
    rows = []
    for d in docs:
        st = d["station"]
        lv = d.get("levels") or {}
        for run in d["runs"]:
            cyc = run["cycle"]
            t = stats.ct(cyc)
            for m in MAIN:
                g = got.get((st, cyc, m), {})
                ser = (run.get("series") or {}).get(m)
                if not g and not ser:
                    continue
                S = g.get("S")
                if S is not None:
                    status = "Verified"
                    summary = f"S {S:.3f}" + (f" · RMSE {g['RMSE']:.2f} ft" if g.get("RMSE") is not None else "") + \
                        (f" · bias {g['Bias']:+.2f} ft" if g.get("Bias") is not None else "")
                elif g.get("S_0_12") is not None:
                    status = "Partly verified"
                    summary = f"First 12 h S {g['S_0_12']:.3f} · later hours not observed yet"
                else:
                    status, summary = "Awaiting observations", "Awaiting observations"
                # the forecast's own highest water and the NWS flood category it reaches
                peak = peak_t = cat = over = None
                vals = [(v, i) for i, v in enumerate(ser or []) if v is not None]
                if vals:
                    peak, i = max(vals)
                    peak_t = pd.Timestamp(run["t0"] + i * run["dt"], unit="ms", tz="UTC")
                    cat = next((c for c in reversed(stats.CATS) if lv.get(c) is not None and peak >= lv[c]), None)
                    over = round(peak - lv["action"], 2) if lv.get("action") is not None else None
                    summary = f"Peak {peak:.2f} ft MLLW {peak_t.day} {peak_t:%b %H:%M}Z" + (f" ({cat.capitalize()})" if cat else "") + " · " + summary
                rows.append({
                    "station": st, "name": d["name"], "member": m, "member_name": stats.NAMES.get(m, m),
                    "cycle": cyc, "issued": _iso(t), "issued_day": f"{t:%Y-%m-%d}T12:00:00Z",
                    "issued_label": f"{t.day} {t:%b %Y %H}Z", "status": status,
                    **{k: g.get(k) for k in ("S", "S_0_12", "S_12_36", "S_36_72", "S_0_36", "Bias", "MAE", "RMSE", "R2",
                                             "peak_err_ft", "span_h")},
                    "rating": "" if S is None else "Good" if S >= stats.GOOD else "Fair" if S >= stats.NO_SKILL else "No skill",
                    "fc_peak_ft_mllw": None if peak is None else round(peak, 2), "fc_peak_time": None if peak_t is None else _iso(peak_t),
                    "fc_category": cat.capitalize() if cat else "", "fc_over_action_ft": over,
                    "summary": summary,
                    "chart_url": f"{BASE}crest_{st.lower()}.html?embed=1&view=forecast&theme=light#run={cyc}&show={m}"})
    return rows
