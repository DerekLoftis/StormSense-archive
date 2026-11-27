"""The published StormSense files, every one as a chart (html), csv and json, on the station's MLLW
and on NAVD88:

    fore72/<ST>_fore72_<MLLW|NAVD>   StormSense Latest 72-hour forecast (FORE72_HEAD)
    7day/<ST>_7day_<MLLW|NAVD>       last 7 days of the gauge's own observations (HC_HEAD)
    30day/<ST>_30day_<MLLW|NAVD>     last 30 days
    ens72/<ST>_ens72_<MLLW|NAVD>     StormSense Ensemble: every forecast, plus the skill tables

The astronomical tide everywhere is StormSense's own harmonic tide locked to Boon's m30 (tide.py).
"""
import csv
import json
import os
from datetime import timedelta

import numpy as np

from . import charts as C, flood, skill as SK, station as STN

FORE72_HEAD = ["date_time_z", "astro_tide_ft", "surge_ft", "bias_ft", "residual_ft", "SSforecast_ft",
               "SSforecast_p10_ft", "SSforecast_p90_ft", "surge_p10_ft", "surge_p90_ft", "m30_ft"]
HC_HEAD = ["date_time_z", "water_level_ft", "astro_tide_ft", "residual_ft"]
LEVELS = {"astro_tide_ft", "SSforecast_ft", "SSforecast_p10_ft", "SSforecast_p90_ft", "m30_ft", "water_level_ft"}
GRID_GAP = 1.5 * STN.STEP     # observations on the run's six-minute grid: a missing step is the gauge's outage
DESCRIPTION = {"fore72": "72-hour storm tide forecast", "7day": "7-day observed water level hindcast",
               "30day": "30-day observed water level hindcast"}
# ens72 columns: water levels, then the P-ETSS and ETSS surges the StormSense model uses on our tide
OWN = ["stormsense", "stormsense_p10", "stormsense_p90", "stormsense_raw"]
ENS_LEVELS = (["observed", "astro_tide", "primary", "primary_p10", "primary_p90"]
              + [k for k in STN.MEMBERS if k not in OWN] + ["petss_p10", "petss_p90"] + OWN)
ENS_SURGES = ["petss_surge", "etss_surge"]
ENS_HEAD = ["date_time_z"] + [f"{k}_ft" for k in ENS_LEVELS + ENS_SURGES]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt(v):
    return "" if v is None or np.isnan(v) else "%.3f" % (round(float(v), 3) + 0.0)


def shift_of(datum, navd88):
    return 0.0 if datum == "MLLW" else -navd88


def datums_for(info, d):
    return [x for x in ("MLLW", "NAVD") if x == "MLLW" or info["navd88"] is not None]


def write_csv(path, head, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def write_json(path, head, rows, header):
    recs = [{h: (r[j] if j == 0 else (float(r[j]) if r[j] != "" else None)) for j, h in enumerate(head)} for r in rows]
    doc = dict(header, fields=head, data=recs)
    with open(path, "w") as f:
        json.dump(doc, f, separators=(",", ":"))


def header(st, prod, datum, updated, source, **extra):
    meta = f"stormsense-metadata-for-{prod}-csv-data{'-NAVD' if datum == 'NAVD' else ''}.json"
    return dict({"station": st, "product": prod, "description": DESCRIPTION.get(prod, prod),
                 "datum": "NAVD88" if datum == "NAVD" else "MLLW", "units": "feet", "time_zone": "UTC (Z)",
                 "interval_minutes": 6, "updated": updated, "source": source,
                 "metadata": f"../metadata/{meta}"}, **extra)


# --- fore72 ----------------------------------------------------------------------------------------
def xhw_threshold(datum, d, levels_mllw, navd88):
    """Forecast peaks labelled XHW: within 0.5 ft of Action stage or above on NAVD88 charts, above
    HAT on MLLW charts (ft MLLW; None if the chart has neither)."""
    if datum == "MLLW":
        return d.get("HAT")
    if levels_mllw.get("action") is not None:
        return levels_mllw["action"] - flood.XHW_NEAR_ACTION_FT
    return None


def fore72(site, st, info, d, grid, t0, cols, prim, cone, chances, m30, levels_mllw, guidance, source, updated,
           extra=None):
    """fore72 files. prim: station.primary() output; cone: (p10, p90, label) on the grid; chances:
    {category: 0-1} of reaching it in 72 h; m30: ft MLLW. Returns the XHW peaks per datum."""
    lead = STN.lead_h(grid, t0)
    fut = (lead >= 0) & (lead <= STN.HOURS)
    p10, p90, cone_label = cone
    surge_lo = p10 - prim["astro_tide"] - prim["bias"]
    surge_hi = p90 - prim["astro_tide"] - prim["bias"]
    fields = {"astro_tide_ft": prim["astro_tide"], "surge_ft": prim["surge"], "bias_ft": prim["bias"],
              "residual_ft": prim["residual"], "SSforecast_ft": prim["SSforecast"], "SSforecast_p10_ft": p10,
              "SSforecast_p90_ft": p90, "surge_p10_ft": surge_lo, "surge_p90_ft": surge_hi,
              "m30_ft": np.full(len(grid), np.nan if m30 is None else m30)}
    out = {}
    for datum in datums_for(info, d):
        sh = shift_of(datum, info["navd88"])
        rows = [[iso(grid[i])] + [fmt(fields[h][i] + (sh if h in LEVELS else 0.0)) for h in FORE72_HEAD[1:]]
                for i in np.where(fut)[0]]
        base = os.path.join(site, "fore72", f"{st}_fore72_{datum}")
        write_csv(base + ".csv", FORE72_HEAD, rows)
        thr = xhw_threshold(datum, d, levels_mllw, info["navd88"])
        fc = prim["SSforecast"]
        idx = flood.xhw(lead, np.where(fut, fc, np.nan), thr) if thr is not None else []
        peaks = [{"date_time_z": iso(grid[i]), "xhw_ft": round(float(fc[i] + sh), 2)} for i in idx]
        out[datum] = peaks
        flood_lv = ({k: round(v + sh, 3) for k, v in levels_mllw.items()} if levels_mllw else {})
        hdr = header(st, "fore72", datum, updated, source, guidance=guidance, uncertainty=cone_label,
                     m30_ft=None if m30 is None else round(m30 + sh, 3),
                     flood_levels_ft=flood_lv or None,
                     chance_of_reaching_within_72h={k: round(v, 3) for k, v in (chances or {}).items()} or None,
                     xhw_threshold_ft=None if thr is None else round(thr + sh, 2), xhw_peaks=peaks, **(extra or {}))
        write_json(base + ".json", FORE72_HEAD, rows, hdr)
        past = (lead >= -24) & (lead <= 0)
        tide_all = cols["astro_tide"]
        series = [
            {"data": C.pts(grid[past], cols["observed"][past] + sh, GRID_GAP), "type": "line", "color": "red", "name": "Observed",
             "lineWidth": 2, "zIndex": 4},
            {"data": C.pts(grid[past], tide_all[past] + sh), "type": "line", "color": "blue", "name": "Astronomic Tide"},
            {"data": C.pts(grid[past], cols["observed"][past] - tide_all[past], GRID_GAP), "type": "line", "color": C.RESIDUAL,
             "name": "Residual"},
            {"data": [[C.ms(t), round(float(a + sh), 3), round(float(b + sh), 3)]
                      for t, a, b in zip(grid[fut], p10[fut], p90[fut]) if not (np.isnan(a) or np.isnan(b))],
             "type": "arearange", "name": "Forecast Uncertainty (10%–90%)", "color": C.CONE,
             "fillColor": C.CONE, "lineWidth": 0, "zIndex": 0, "marker": {"enabled": False}},
            {"data": C.pts(grid[fut], prim["astro_tide"][fut] + sh), "type": "line", "color": "blue",
             "name": "Astronomic Tide Forecast"},
            {"data": C.xhw_points(grid[fut], fc[fut] + sh, [i - np.where(fut)[0][0] for i in idx]), "type": "line",
             "color": "red", "name": "StormSense Forecast", "dashStyle": "Dash", "lineWidth": 2, "zIndex": 5},
            {"data": C.pts(grid[fut], prim["residual"][fut]), "type": "line", "color": C.RESIDUAL,
             "name": "Residual Forecast", "dashStyle": "Dash"},
        ]
        lines, hat, hk = C.datum_lines(d, datum, info["navd88"], levels_mllw, chances, m30)
        C.page(base + ".html", f"72 Hour StormSense Forecast for {st}", t0, datum, series, lines, hat, hk,
               guidance=f"{guidance}; shaded cone: {cone_label}")
    return out


# --- 7day / 30day ------------------------------------------------------------------------------------
def hindcast(site, prod, days, st, info, d, obs, tide_fn, levels_mllw, updated, source):
    t, v = obs
    t_last = t[-1]
    sel = t > t_last - timedelta(days=days)
    t, v = t[sel], v[sel]
    if not len(t):
        return False
    tide = tide_fn(t)
    gap = STN.obs_gap(obs, t_last)        # the charts leave a gauge's outage open
    for datum in datums_for(info, d):
        sh = shift_of(datum, info["navd88"])
        rows = [[iso(a), fmt(b + sh), fmt(c + sh), fmt(b - c)] for a, b, c in zip(t, v, tide)]
        base = os.path.join(site, prod, f"{st}_{prod}_{datum}")
        write_csv(base + ".csv", HC_HEAD, rows)
        write_json(base + ".json", HC_HEAD, rows, header(st, prod, datum, updated, source))
        series = [{"data": C.pts(t, v + sh, gap), "type": "line", "color": "red", "name": "Observed", "zIndex": 3},
                  {"data": C.pts(t, tide + sh), "type": "line", "color": "blue", "name": "Astronomic Tide"},
                  {"data": C.pts(t, v - tide, gap), "type": "line", "color": C.RESIDUAL, "name": "Residual"}]
        lines, hat, hk = C.datum_lines(d, datum, info["navd88"], levels_mllw)
        C.page(base + ".html", f"{days} Day StormSense Observations for {st}", t_last, datum, series, lines, hat, hk,
               guidance=f"Observations: {source}")
    return True


# --- ens72 -----------------------------------------------------------------------------------------
def ens_series(grid, cols, sh, t0):
    keep = grid >= t0 - timedelta(hours=24)
    g = grid[keep]

    def col(k):
        return cols[k][keep] + sh

    s = [{"data": C.pts(g, col("observed"), GRID_GAP), "type": "line", "color": "red", "name": "Observed", "lineWidth": 3,
          "zIndex": 5},
         {"data": C.pts(g, col("astro_tide")), "type": "line", "color": "blue", "name": "Astronomic Tide",
          "lineWidth": 1, "dashStyle": "Dot"}]
    if "petss_p10" in cols:
        a, b = col("petss_p10"), col("petss_p90")
        s.append({"data": [[C.ms(t), round(float(x), 3), round(float(y), 3)] for t, x, y in zip(g, a, b)
                           if not (np.isnan(x) or np.isnan(y))], "type": "arearange", "color": C.CONE,
                  "fillColor": C.CONE, "lineWidth": 0, "zIndex": 0, "name": "P-ETSS 10%–90%"})
    if "primary" in cols:
        s.append({"data": C.pts(g, col("primary")), "type": "line", "color": "red", "dashStyle": "Dash",
                  "lineWidth": 3, "zIndex": 6, "name": "StormSense Latest (published)"})
    for key, (label, color, dash, _) in STN.MEMBERS.items():
        if key in cols:
            s.append({"data": C.pts(g, col(key)), "type": "line", "color": color, "name": label, "dashStyle": dash,
                      "lineWidth": 2})
    return s


def ens72(site, st, info, d, grid, t0, cols, notes, levels_mllw, tables, updated):
    """ens72 files: chart with the three skill tables under it, csv and json."""
    members_here = [STN.MEMBERS[k][0] for k in STN.MEMBERS if k in cols]
    guide = (f"StormSense Ensemble: {len(members_here)} forecasts; model guidance lined up with the gauge's last "
             f"24 hours; dashed red: the published StormSense Latest forecast")
    below = (f'<a id="skill"></a><p>{SK.TABLES_NOTE}</p>' + SK.tables_html(tables))
    for datum in datums_for(info, d):
        sh = shift_of(datum, info["navd88"])
        rows = [[iso(t)] + [fmt(cols[k][i] + sh) if k in cols else "" for k in ENS_LEVELS]
                + [fmt(cols[k][i]) if k in cols else "" for k in ENS_SURGES] for i, t in enumerate(grid)]
        base = os.path.join(site, "ens72", f"{st}_ens72_{datum}")
        write_csv(base + ".csv", ENS_HEAD, rows)
        doc = {"station": st, "product": "ens72", "description": "StormSense Ensemble: 72-hour storm tide forecasts "
               "from every available source", "datum": "NAVD88" if datum == "NAVD" else "MLLW", "units": "feet",
               "time_zone": "UTC (Z)", "interval_minutes": 6, "updated": updated, "members": notes,
               "skill_tables": f"../skill/tables/{st}_skill.json", "fields": ENS_HEAD,
               "data": [{h: (r[j] if j == 0 else (float(r[j]) if r[j] else None)) for j, h in enumerate(ENS_HEAD)}
                        for r in rows]}
        with open(base + ".json", "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        lines, hat, hk = C.datum_lines(d, datum, info["navd88"], levels_mllw)
        C.page(base + ".html", f"72 Hour StormSense Ensemble for {st}", t0, datum, ens_series(grid, cols, sh, t0),
               lines, hat, hk, guidance=guide, after_html=below)
