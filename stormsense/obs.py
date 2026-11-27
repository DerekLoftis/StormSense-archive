"""Gauge observations for StormSense, straight from each gauge's own source (no Tidewatch/web4),
in feet above the station's MLLW, quality-controlled.

    NOAA CO-OPS      6-minute water level (MLLW)            api.tidesandcurrents.noaa.gov
    USGS NWIS        instantaneous 62620, ft NAVD88         waterservices.usgs.gov (new OGC API as fallback)
    VIMS THREDDS     BRDH radar gauge, 1-minute             tds.vims.edu (MLLW = 9.71 - value)
    archives         USLN-X (FloodVISION-AI), CNRC (GreenStream), ft NAVD88, kept in tidewatch_latest
    NWS NWPS         observed stage of gauges with no other public feed (VDEM, city and county
                     gauges), about 30 days, in the gauge's datum (stations.csv converts it)

A station lists its sources in order (stations.csv obs column); the first that returns readings
is used. NAVD88 heights go to MLLW with stations.csv's navd88_above_mllw_ft (NOAA published datums,
or VDatum at USGS and VIMS gauges).

    python -m stormsense.obs OUT_DIR [--days 3] [--stations SWPT,USJT]   one CSV per station
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "StormSense (VIMS)"}
COOPS = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?application=VIMS_StormSense&units=english"
         "&time_zone=gmt&format=json&datum=MLLW&station={id}&product={product}&begin_date={a}&end_date={b}")
NWIS = ("https://waterservices.usgs.gov/nwis/iv/?format=json&sites={site}&parameterCd={pc}"
        "&startDT={a}&endDT={b}&siteStatus=all")
NWPS = "https://api.water.noaa.gov/nwps/v1/gauges/{lid}/stageflow/observed"
OGC = ("https://api.waterdata.usgs.gov/ogcapi/v0/collections/continuous/items?f=json&limit=10000"
       "&monitoring_location_id=USGS-{site}&parameter_code={pc}&datetime={a}/{b}")
# USLN-X/CNRC archives, read from both places and merged while Tidewatch's capture moves to its private
# repo: the tidewatch_latest Pages site (obs/<folder>/) and tidewatch_latest's main branch (<folder>/)
ARCHIVE_URLS = ("https://derekloftis.github.io/tidewatch_latest/obs/{folder}/{name}",
                "https://raw.githubusercontent.com/DerekLoftis/tidewatch_latest/main/{folder}/{name}")
ARCHIVE_COLUMN = {"floodvision": ("ml_ft_navd88", "v2"), "greenstream": ("level_ft_navd88", None)}
GAUGE_MLLW = {"BRDH": 9.71}  # VIMS radar gauges: MLLW lies this far below the sensor (ft)
NAVD_PARAM = "62620"  # estuary or ocean water surface elevation above NAVD88, ft
DEFAULT_LAT = 37.0  # for the few USGS sites stations.csv has no position for (nodal factors only)


def get(url, tries=3, timeout=90):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(4 * (i + 1))


def stations():
    """{station: row} from stations.csv (see stations.py), lat_f defaulted for nodal factors."""
    from . import stations as S
    out = S.load()
    for r in out.values():
        r["lat_f"] = r["lat_f"] if r["lat_f"] is not None else DEFAULT_LAT
    return out


def utc(s):
    """Datetime from an ISO-ish string with or without an offset, as UTC."""
    s = s.replace("Z", "+00:00")
    t = datetime.fromisoformat(s)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _chunks(a, b, days):
    while a < b:
        c = min(b, a + timedelta(days=days))
        yield a, c
        a = c


# --- sources ---------------------------------------------------------------------
def coops(noaa_id, a, b, product="water_level"):
    """CO-OPS heights ft above MLLW between a and b (6-minute water_level, or hourly_height)."""
    days = 30 if product == "water_level" else 360
    t_all, v_all = [], []
    for x, y in _chunks(a, b, days):
        url = COOPS.format(id=noaa_id, product=product, a=x.strftime("%Y%m%d %H:%M"), b=y.strftime("%Y%m%d %H:%M"))
        js = json.loads(get(url.replace(" ", "%20")))
        if "error" in js:
            if "No data" in str(js["error"]):
                continue
            raise RuntimeError(js["error"].get("message", js["error"]))
        for r in js.get("data", []):
            if r.get("v") not in ("", None):
                t_all.append(datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc))
                v_all.append(float(r["v"]))
    return np.array(t_all), np.array(v_all)


def coops_predictions(noaa_id, a, b):
    """NOAA's published 6-minute tide predictions, ft MLLW (to check our own harmonic fits)."""
    t_all, v_all = [], []
    for x, y in _chunks(a, b, 30):
        url = COOPS.format(id=noaa_id, product="predictions", a=x.strftime("%Y%m%d %H:%M"), b=y.strftime("%Y%m%d %H:%M"))
        for r in json.loads(get(url.replace(" ", "%20"))).get("predictions", []):
            t_all.append(datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc))
            v_all.append(float(r["v"]))
    return np.array(t_all), np.array(v_all)


def parse_nwis(raw, pc=NAVD_PARAM):
    """(times, values) of one parameter from an NWIS IV JSON reply (first series that has data)."""
    for ts in json.loads(raw).get("value", {}).get("timeSeries", []):
        if ts["variable"]["variableCode"][0]["value"] != pc:
            continue
        nodata = ts["variable"].get("noDataValue")
        for block in ts.get("values", []):
            pts = [(utc(p["dateTime"]), float(p["value"])) for p in block.get("value", [])
                   if p.get("value") not in (None, "") and float(p["value"]) != nodata]
            if pts:
                t, v = zip(*pts)
                return np.array(t), np.array(v)
    return np.array([]), np.array([])


def parse_ogc(raw):
    """(times, values, next link) from one page of the USGS OGC API continuous collection."""
    js = json.loads(raw)
    pts = [(utc(f["properties"]["time"]), float(f["properties"]["value"])) for f in js.get("features", [])
           if f["properties"].get("value") not in (None, "")]
    nxt = next((ln["href"] for ln in js.get("links", []) if ln.get("rel") == "next"), None)
    t, v = (zip(*pts) if pts else ((), ()))
    return np.array(t), np.array(v), nxt


def nwis(site, a, b, pc=NAVD_PARAM):
    """USGS instantaneous values (ft NAVD88 for 62620) between a and b; the legacy NWIS service
    first, the OGC API if that fails (NWIS is being retired)."""
    fmt = "%Y-%m-%dT%H:%MZ"
    try:
        t_all, v_all = [], []
        for x, y in _chunks(a, b, 120):
            t, v = parse_nwis(get(NWIS.format(site=site, pc=pc, a=x.strftime(fmt), b=y.strftime(fmt))), pc)
            t_all += list(t)
            v_all += list(v)
        return np.array(t_all), np.array(v_all)
    except Exception as e:
        legacy = e
    t_all, v_all = [], []
    url = OGC.format(site=site, pc=pc, a=a.strftime("%Y-%m-%dT%H:%M:%SZ"), b=b.strftime("%Y-%m-%dT%H:%M:%SZ"))
    try:
        while url:
            t, v, url = parse_ogc(get(url))
            t_all += list(t)
            v_all += list(v)
    except Exception as e:
        raise RuntimeError(f"NWIS failed ({legacy}); OGC API failed ({e})")
    o = np.argsort(np.array([x.timestamp() for x in t_all]))
    return np.array(t_all)[o], np.array(v_all)[o]


def thredds(url, gauge_mllw, a, b):
    """A VIMS radar gauge's 1-minute record over OPeNDAP between a and b, ft MLLW."""
    dds = get(url + ".dds").decode()
    n = int(re.search(r"time\[time = (\d+)\]", dds).group(1))
    last = float(get(url + f".ascii?time%5B{n - 1}:1:{n - 1}%5D").decode().split()[-1].strip(","))
    end = datetime.fromtimestamp(last, timezone.utc)
    i1 = max(0, n - 1 - int((end - b).total_seconds() // 60 * 0.95)) if b < end else n - 1
    i0 = max(0, i1 - int((b - a).total_seconds() // 60 * 1.2))
    rng = f"%5B{i0}:1:{i1}%5D"  # Tomcat refuses raw brackets in a query
    txt = get(url + f".ascii?time{rng},values{rng}", timeout=300).decode()
    cols = []
    for name in ("time", "values"):
        m = re.search(r"^" + name + r"\[\d+\]\s*\n(.*?)(?:\n\s*\n|\Z)", txt, re.S | re.M)
        cols.append(np.array([float(x) for x in m.group(1).replace("\n", ",").split(",") if x.strip()]))
    secs, dist = cols
    ok = (secs > 0) & (dist > -900)
    t = np.array([datetime.fromtimestamp(x, timezone.utc).replace(second=0, microsecond=0) for x in secs[ok]])
    v = gauge_mllw - dist[ok]
    sel = (t >= a) & (t <= b)
    return t[sel], v[sel]


def archive(source_id, a, b):
    """USLN-X / CNRC monthly archive CSVs published by Tidewatch, ft NAVD88 between a and b."""
    folder, st = source_id.split("/")
    col, model = ARCHIVE_COLUMN[folder]
    rows = {}
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        for url in ARCHIVE_URLS:
            try:
                text = get(url.format(folder=folder, name=f"{st}_{y:04d}-{m:02d}.csv"), tries=1).decode()
            except Exception:
                continue
            for r in csv.DictReader(io.StringIO(text)):
                if r.get(col) and (model is None or r.get("model", model) == model):
                    rows.setdefault(r["time_utc"], float(r[col]))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    t = np.array([utc(k) for k in sorted(rows)])
    v = np.array([rows[k] for k in sorted(rows)])
    if not len(t):
        return t, v
    sel = (t >= a) & (t <= b)
    return t[sel], v[sel]


def nwps(lid, a, b):
    """NWS NWPS observed stage (the gauge's own datum, ft) between a and b; NWPS keeps about 30 days."""
    js = json.loads(get(NWPS.format(lid=lid)))
    pts = []
    for d in js.get("data") or []:
        v = d.get("primary")
        if v is None or float(v) <= -900:
            continue
        pts.append((utc(d["validTime"]), float(v)))
    pts.sort()
    t = np.array([p[0] for p in pts])
    v = np.array([p[1] for p in pts])
    if not len(t):
        return t, v
    sel = (t >= a) & (t <= b)
    return t[sel], v[sel]


def stage_to_mllw(info, t, v):
    """NWPS stage to ft MLLW with the station's offset, and the earlier offset before the gauge
    switched datums (datum_change_utc)."""
    off = np.full(len(t), info["stage_to_mllw"])
    if info.get("datum_change_utc") and info.get("stage_to_mllw_before") is not None:
        change = utc(info["datum_change_utc"])
        off[np.array([x < change for x in t], bool)] = info["stage_to_mllw_before"]
    return v + off


# --- quality control ---------------------------------------------------------------
QC_FLAGS = {0: "ok", 1: "range", 2: "spike", 3: "flatline", 4: "step"}
# Step/plateau check (YRCG 26 Nov 2026: NOAA Yorktown's real-time feed on web4 and NWPS jumped from
# ~5 ft to ~15.4 ft NAVD88 for 12 minutes and again for 72 minutes, a target close under the radar).
# A rolling median cannot catch a plateau longer than half its window, so a reading block that jumps
# away and later jumps back is dropped as a whole. Real 6-minute changes reach 0.8 ft (RDIV, HPPV,
# USLN-X during that storm); the tide and surge move at most ~1.5 ft an hour.
STEP_FT = 1.5        # a jump between neighbouring readings larger than this (plus STEP_RATE) is suspect
STEP_RATE = 1.5      # ft per hour the water may move between two readings
STEP_GAP = timedelta(hours=3)        # readings further apart than this are not compared
STEP_HOLD = timedelta(hours=6)       # a jump not undone within this long is a real shift: kept


def _spike_ok(v, window=15, tol=0.5):
    if len(v) <= window:
        return np.ones(len(v), bool)
    import pandas as pd
    med = pd.Series(v).rolling(window, center=True, min_periods=max(3, window // 3)).median().to_numpy()
    return np.abs(v - med) <= tol


def _step_ok(t, v, step=None, rate=None, gap=None, hold=None):
    """False for blocks of readings that jump away from the water level and come back: a jump
    bigger than `step` ft (plus `rate` ft/h of elapsed time) between neighbouring readings starts a
    block, and the block ends at the first later reading that jumps back (by at least half the first
    jump) to within the allowance of the last reading before the jump. A block still open at the end of the record is dropped too (the
    forecast must not start from it); one that has not come back after `hold` is a real shift and
    is kept."""
    step = STEP_FT if step is None else step
    rate = STEP_RATE if rate is None else rate
    gap = STEP_GAP if gap is None else gap
    hold = STEP_HOLD if hold is None else hold
    n = len(v)
    keep = np.ones(n, bool)
    if n < 3:
        return keep
    sec = np.array([x.timestamp() for x in t], float)

    def allow(i, j):
        return step + rate * abs(sec[j] - sec[i]) / 3600.0

    i = 1
    while i < n:
        a = i - 1  # the last reading before the jump
        if sec[i] - sec[a] > gap.total_seconds() or abs(v[i] - v[a]) <= allow(a, i):
            i += 1
            continue
        end = None  # first reading back near the level before the jump
        j = i + 1
        up = v[i] > v[a]
        while j < n and sec[j] - sec[a] <= hold.total_seconds():
            back = (v[j] < v[j - 1]) if up else (v[j] > v[j - 1])
            if back and abs(v[j] - v[j - 1]) >= 0.5 * abs(v[i] - v[a]) and abs(v[j] - v[a]) <= allow(a, j):
                end = j
                break
            j += 1
        if end is not None:
            keep[i:end] = False
            i = end + 1
        elif j >= n:  # the record ends inside the block: drop it for now
            keep[i:] = False
            break
        else:  # never came back within `hold`: a real change of level, keep it
            i += 1
    return keep


def _flat_ok(t, v, max_same=timedelta(minutes=48)):
    keep = np.ones(len(v), bool)
    if len(v) < 3:
        return keep
    start = 0
    for i in range(1, len(v) + 1):
        if i == len(v) or v[i] != v[start]:
            if t[i - 1] - t[start] > max_same:
                keep[start:i] = False
            start = i
    return keep


def despike(t, v, window=15, tol=0.5):
    """Drop values more than tol ft off the running median of `window` points."""
    keep = _spike_ok(v, window, tol)
    return t[keep], v[keep]


def flatline(t, v, max_same=timedelta(minutes=48)):
    """Drop runs where the value doesn't change for longer than max_same: a tidal gauge always
    moves, so a held value is a stuck or repeated reading (USBB repeats values in ~18-minute steps)."""
    keep = _flat_ok(t, v, max_same)
    return t[keep], v[keep]


def qc_flags(t, v, lo=-4.0, hi=14.0):
    """(times, values, flags) sorted and de-duplicated, every reading kept and flagged with a
    QC_FLAGS code: out of a plausible range (ft MLLW), spike, a block that steps away and back
    (longer than the spike check sees), or part of a stuck run."""
    if not len(t):
        return t, v, np.zeros(0, int)
    s = np.array([x.timestamp() for x in t])
    o = np.argsort(s, kind="stable")
    _, first = np.unique(s[o], return_index=True)
    t, v = t[o][first], np.asarray(v, float)[o][first]
    flags = np.zeros(len(t), int)
    flags[~(np.isfinite(v) & (v > lo) & (v < hi))] = 1
    idx = np.where(flags == 0)[0]
    flags[idx[~_spike_ok(v[idx])]] = 2
    idx = np.where(flags == 0)[0]
    flags[idx[~_step_ok(t[idx], v[idx])]] = 4
    idx = np.where(flags == 0)[0]
    flags[idx[~_flat_ok(t[idx], v[idx])]] = 3
    return t, v, flags


def qc(t, v, lo=-4.0, hi=14.0):
    """Sorted, de-duplicated, in a plausible range (ft MLLW), despiked, no step blocks, no stuck runs."""
    t, v, flags = qc_flags(t, v, lo, hi)
    return t[flags == 0], v[flags == 0]


MAX_GAP = timedelta(minutes=30)     # a longer silence is an outage: a gap in the record, never a line across it
BRIDGE_N = 12                       # this many readings in one straight line (72 minutes) are such a line


def without_bridges(v, n=BRIDGE_N, tol=0.0021):
    """v (readings on the six-minute clock, NaN where there is none) with the straight lines taken out.
    Runs archived before 2026-11-27 interpolated the gauge straight across an outage, so a gauge that was
    down through a storm's peak seemed to have recorded a slow, steady rise. n or more readings in a line,
    to the tables' three decimals, are set to NaN; the reading at each end of the line is real and kept,
    except a line the series starts in, whose start is part of the line."""
    v = np.array(v, float)
    if len(v) < max(n, 3):
        return v
    line = np.abs(np.diff(v, 2)) <= tol       # False next to a NaN
    seen = np.flatnonzero(~np.isnan(v))
    out, i = v.copy(), 0
    while i < len(line):
        if not line[i]:
            i += 1
            continue
        j = i
        while j < len(line) and line[j]:
            j += 1
        if j - i + 2 >= n:                    # readings i .. j+1 are in one line
            out[i + (0 if i == seen[0] else 1):j + 1] = np.nan
        i = j
    return out


def on_six_minutes(t, v, max_gap=MAX_GAP):
    """Series on the 6-minute clock: values on it kept, others interpolated across gaps up to
    max_gap (USGS gauges report every 15 minutes, VIMS gauges every minute)."""
    if len(t) < 2:
        return t, v
    s = np.array([x.timestamp() for x in t])
    g0 = np.ceil(s[0] / 360) * 360
    grid = np.arange(g0, s[-1] + 1, 360)
    out = np.interp(grid, s, v)
    j = np.searchsorted(s, grid)
    j = np.clip(j, 1, len(s) - 1)
    gap = s[j] - s[j - 1]
    exact = np.isin(grid, s)
    ok = exact | (gap <= max_gap.total_seconds())
    tt = np.array([datetime.fromtimestamp(x, timezone.utc) for x in grid[ok]])
    return tt, out[ok]


# --- one station -------------------------------------------------------------------
def read_source(st, info, src, sid, a, b):
    """(times, ft MLLW) from one source of station st, as the source reports them."""
    if src == "coops":
        return coops(sid, a, b)
    if src == "nwis":
        t, v = nwis(sid, a, b)
        return t, v + info["navd88"]
    if src == "thredds":
        return thredds(sid, GAUGE_MLLW[st], a, b)
    if src == "archive":
        t, v = archive(sid, a, b)
        return t, v + info["navd88"]
    if src == "nwps":
        t, v = nwps(sid, a, b)
        return t, stage_to_mllw(info, t, v)
    raise ValueError(f"unknown source {src}")


def fetch_raw(st, a, b, info=None, used=None):
    """(times, ft MLLW) for station st between a and b as its first working source reports them
    (its own time step, no QC); `used`, a dict, gets the source that answered. Raises if no source
    of the station returned readings."""
    info = info or stations()[st]
    if not info["chain"]:
        raise ValueError(f"{st}: no observation source ({info['note'] or 'none listed'})")
    errors = []
    for src, sid in info["chain"]:
        try:
            t, v = read_source(st, info, src, sid, a, b)
        except Exception as e:
            errors.append(f"{src}:{sid}: {str(e)[:120]}")
            continue
        if len(t):
            name = f"{src}:{sid}"
            t, v, name = _fill_stale(st, info, (src, sid), t, v, b, name)
            if used is not None:
                used[st] = name
            return t, v
        errors.append(f"{src}:{sid}: no readings")
    raise RuntimeError("; ".join(errors))


# A source whose newest reading is older than this is stale: the later sources in the chain fill in
# after it (Nansemond at Suffolk, Nov 2026: USGS stopped at the storm's peak while NWPS kept reporting).
STALE = timedelta(hours=2)


def _fill_stale(st, info, first, t, v, b, name):
    """t, v (and the source name) with the readings of the station's later sources appended after the
    last reading of the first source, when the first source has gone stale before b."""
    if t[-1] >= b - STALE:
        return t, v, name
    chain = info["chain"]
    for src, sid in chain[chain.index(first) + 1:]:
        try:
            t2, v2 = read_source(st, info, src, sid, t[-1], b)
        except Exception:
            continue
        keep = np.array([x > t[-1] for x in t2], bool) if len(t2) else np.zeros(0, bool)
        if keep.any():
            return (np.concatenate([t, np.asarray(t2)[keep]]), np.concatenate([v, np.asarray(v2)[keep]]),
                    f"{name} to {t[-1].strftime('%Y-%m-%dT%H:%MZ')}, then {src}:{sid}")
    return t, v, name


def fetch(st, a, b, info=None, used=None):
    """(times, ft MLLW) for station st between a and b, quality-controlled, on the 6-minute clock.
    Raises if the station has no source of its own."""
    return on_six_minutes(*qc(*fetch_raw(st, a, b, info, used)))


def write_csv(path, t, v):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date_time_z", "water_level_ft_mllw"])
        w.writerows([x.strftime("%Y-%m-%dT%H:%MZ"), f"{y:.3f}"] for x, y in zip(t, v))


def read_csv(path):
    rows = list(csv.reader(open(path)))[1:]
    return (np.array([datetime.strptime(r[0], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc) for r in rows]),
            np.array([float(r[1]) for r in rows]))


def main(argv):
    out = argv[0]
    days = float(argv[argv.index("--days") + 1]) if "--days" in argv else 3.0
    table = stations()
    names = argv[argv.index("--stations") + 1].split(",") if "--stations" in argv else sorted(table)
    os.makedirs(out, exist_ok=True)
    b = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    a = b - timedelta(days=days)
    report = []
    for st in names:
        try:
            t, v = fetch(st, a, b, table[st])
        except Exception as e:
            report.append((st, table[st]["obs"], 0, None, str(e)[:160]))
            continue
        if len(t):
            write_csv(os.path.join(out, f"{st}_obs.csv"), t, v)
        report.append((st, table[st]["obs"], len(t), t[-1].strftime("%Y-%m-%dT%H:%MZ") if len(t) else None, ""))
    with open(os.path.join(out, "report.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "source", "n", "last", "error"])
        w.writerows(report)
    for r in report:
        print(",".join("" if x is None else str(x) for x in r))
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
