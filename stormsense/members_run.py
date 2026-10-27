"""Fetch every forecast member's newest run for one StormSense run, keyed by station.

    P-ETSS            NWS, NOMADS station CSVs, by each station's P-ETSS point (petss_point)
    STOFS-2D/3D, NWM  NOAA AWS SHEF bulletins, by NWS id
    CBOFS             NOAA AWS station netCDF, nearest node within 3 km
    NWS official      NWPS stage forecast, by NWS id, in the gauge's datum
    NHC P-Surge       NOMADS SHEF, only while NHC has advisories out
    ETSS              tgftp East Coast surge bulletin (surge only)
    Tidewatch         VIMS web4 f36 36-hour forecast (px30), the one thing still read from web4
"""
import csv
import io
import re
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np

from . import etss, members as M, petss, psurge

TIDEWATCH_URL = "https://web4.vims.edu/physical/tidewatch/"


def raw_members(now, table, nws_offset):
    """{member key: (run time, {station: (times, ft, datum, (times, ft) with the run before
    stitched in front)})}. nws_offset: {station: ft to add to an NWS stage for MLLW}."""
    stations = {st: r for st, r in table.items() if not r["alias_of"]}
    lid_st = {r["nws_lid"]: st for st, r in stations.items() if r["nws_lid"]}
    latlon = {st: (r["lat_f"], r["lon_f"]) for st, r in stations.items() if r["lat_f"] is not None}
    jobs = {"stofs2d": lambda: M.stofs2d(now, set(lid_st)), "stofs3d": lambda: M.stofs3d(now, set(lid_st)),
            "nwm_short": lambda: M.nwm(now, set(lid_st), "short"),
            "nwm_medium": lambda: M.nwm(now, set(lid_st), "medium"),
            "cbofs": lambda: M.cbofs(now, latlon), "nws": lambda: M.nws(set(lid_st)),
            "psurge": lambda: psurge.fetch(now, stations)}
    out = {}
    for key, job in jobs.items():
        try:
            run, ser, datum, prev = job()
        except Exception as e:
            print(f"{key}: unavailable ({e})")
            continue
        by_st = {}
        for k, (t, v) in ser.items():
            st = lid_st.get(k, k)
            if st not in stations:
                continue
            if datum == "per-gauge":  # NWS stages: each gauge's own datum (no run before is kept)
                if st not in nws_offset:
                    continue
                v, d = v + nws_offset[st], "MLLW"
            else:
                d = "NAVD88" if datum.startswith("NAVD") else datum
            if k in prev:  # the run before, ahead of this run's start: overlaps the latest observations
                pt, pv = prev[k]
                early = pt < t[0]
                by_st[st] = (t, v, d, (np.concatenate([pt[early], t]), np.concatenate([pv[early], v])))
            else:
                by_st[st] = (t, v, d, (t, v))
        out[key] = (run, by_st)
        print(f"{key}: run {run:%Y-%m-%d %HZ} at {len(by_st)} stations" if run else f"{key}: {len(by_st)} stations")
    return out


def petss_points(now, table):
    """(cycle, {station: P-ETSS station dict}) by each station's petss_point; ({}, None) if down."""
    try:
        cycle, a, b = petss.newest_cycle(now)
    except Exception as e:
        print("petss: unavailable", e)
        return None, {}
    want = {r["petss_point"] for r in table.values() if r["petss_point"]}
    pts = petss.load_points(a, want)
    out = {st: pts[r["petss_point"]] for st, r in table.items() if r["petss_point"] in pts}
    print(f"petss: run {cycle:%Y-%m-%d %HZ}, {len(pts)} points for {len(out)} stations")
    return cycle, out


def etss_surge(now, table):
    """(cycle, {station: (times, surge ft)}) from the ETSS bulletin (no tide), or (None, {})."""
    try:
        cycle, surge, _, _ = etss.fetch(now, table, None)
        print(f"etss: run {cycle:%Y-%m-%d %HZ} at {len(surge)} stations")
        return cycle, surge
    except Exception as e:
        print(f"etss: unavailable ({e})")
        return None, {}


def _get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "StormSense (VIMS)"}),
                                        timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def tidewatch(table):
    """{station: (times, ft MLLW)}: Tidewatch's 36-hour forecast (px30) from its NAVD88 f36 CSV, put
    on the station's MLLW with StormSense's NAVD88 offset (Tidewatch's own MLLW for USGS gauges is a
    fixed 1.645 ft below NAVD88)."""
    out = {}
    try:
        listing = _get(TIDEWATCH_URL).decode("utf-8", "replace")
    except Exception as e:
        print(f"tidewatch: web4 unavailable ({e})")
        return out
    names = set(re.findall(r'href="([^"?/]+)"', listing))
    for st, r in table.items():
        tw = r["tidewatch_id"]
        name = f"{tw}_2018_f36_NAVD.csv"
        if not tw or name not in names or r["navd88"] is None:
            continue
        try:
            rows = list(csv.reader(io.StringIO(_get(TIDEWATCH_URL + name).decode())))
            i = rows[0].index("px30")
            pairs = [(datetime.strptime(x[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc), float(x[i]))
                     for x in rows[1:] if len(x) > i and x[i] not in ("", "nan")]
        except Exception as e:
            print(f"tidewatch {tw}: {e}")
            continue
        if len(pairs) > 2:
            t, v = zip(*pairs)
            out[st] = (np.array(t), np.array(v) + r["navd88"])
    print(f"tidewatch: 36-hour forecasts for {len(out)} stations")
    return out
