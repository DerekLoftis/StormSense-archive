"""Historical floods: the 72 hours around a storm's highest water at Sewells Point, at every gauge of the time.

    python -m inundation.history noaa --event 2003-isabel          one storm from NOAA's records
    python -m inundation.history noaa --all                        every storm in events.json with source "noaa"
    python -m inundation.history marks --all                       USGS high water marks and sensor peaks (below)
    python -m inundation.history stormsense --event 2026-thanksgiving-noreaster --runs ensemble_*.tar.gz

`events/events.json` lists the storms. For each, the highest water at Sewells Point (NOAA 8638610, the
longest record in Virginia, hourly since 1927) inside the storm's search dates is the peak, and the window
is 36 hours before it to 36 hours after. `events/<id>/observations.csv.gz` then holds every gauge that
recorded any of it (ft NAVD88, six-minute where the gauge has it, else hourly) and `event.json` the peak
and the gauges. NOAA storms come from the CO-OPS API (verified water levels of the Virginia stations,
active or historic). A StormSense storm comes from the archived runs (`ensemble_<cycle>.tar.gz`): the
gauges' own observations as each run saw them, and every forecast published in the run-up
(`forecasts.csv.gz`), so the storm can be shown again long after the run archives have rolled on.

`for_page` gives a map page each storm's level at each of its gauges: the gauge's own record when it has
one, else the nearest gauge that recorded the storm (in 1933 that is Sewells Point for everything).
"""
import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(__file__)
EVENTS = os.path.join(HERE, "events")
SEWELLS = "8638610"
HALF_H = 36
API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
UA = {"User-Agent": "StormSense (VIMS)"}
FEV = "https://api.waterdata.usgs.gov/stnservices"         # USGS Flood Event Viewer (Short-Term Network) services
NCAT = "https://geodesy.noaa.gov/api/ncat/llh"             # NOAA NGS coordinate conversion (VERTCON for NGVD29)
FT_PER_M = 3.280839895
MARK_COLS = ["id", "kind", "lat", "lon", "elev_ft_navd88", "elev_ft", "datum", "quality", "within_ft", "type",
             "environment", "wave", "county", "waterbody", "place", "time_z", "source"]
MARK_NEAR_M = 60        # a report's mark and the viewer's mark this close, at the same height to 0.1 ft, are one mark
MARK_OVER_FT = 6.0      # a mark this far above every gauge's highest water is a river's or a wave's: not on the map
STEP_MIN = 6            # the page's time step for every storm
NEAR_PEAK_H = 1.5       # a gauge stands for itself only if it recorded the hours around the peak
MIN_COVER = 0.6         # ... and this share of the window that anyone recorded


def events():
    """The storms, newest first: the order of the pages' Historical Floods menu (Derek, 2026-11-27)."""
    with open(os.path.join(EVENTS, "events.json")) as fh:
        return sorted(json.load(fh), key=lambda e: e["search"][0], reverse=True)


def parse(s):
    return datetime.strptime(s[:16].replace("T", " "), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%MZ")


def _get(url, tries=3, timeout=60):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500:                  # NOAA answers 400 where a station has no such data: not worth a second try
                return e.read()
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def coops(station, a, b, product, datum="NAVD"):
    """[(time, ft)] of one station between two times, or [] when NOAA has none (or not on that datum)."""
    q = {"begin_date": a.strftime("%Y%m%d %H:%M"), "end_date": b.strftime("%Y%m%d %H:%M"), "station": station,
         "product": product, "datum": datum, "units": "english", "time_zone": "gmt", "format": "json",
         "application": "StormSense"}
    try:
        js = json.loads(_get(API + "?" + urllib.parse.urlencode(q)))
    except Exception:
        return []
    out = []
    for r in js.get("data", []):
        try:
            out.append((parse(r["t"]), float(r["v"])))
        except (ValueError, KeyError):
            pass
    return out


_DATUMS = {}


def datums(station):
    """A NOAA station's datums in feet on its own station datum: {"MLLW": .., "MSL": .., "NAVD88": .. or None}."""
    if station not in _DATUMS:
        try:
            js = json.loads(_get(f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{station}/datums.json?units=english"))
            _DATUMS[station] = {d["name"]: d["value"] for d in js.get("datums", [])}
        except Exception:
            _DATUMS[station] = {}
    return _DATUMS[station]


# readings older than 120 days are served by nwis.waterservices.usgs.gov (waterservices.usgs.gov redirects there)
NWIS = "https://nwis.waterservices.usgs.gov/nwis/iv/?format=json&sites={site}&parameterCd=62620&startDT={a}&endDT={b}"


def usgs(site, a, b):
    """[(time, ft NAVD88)] of a USGS tide gauge (parameter 62620), thinned to six minutes; [] when it has none.
    A service that cannot be reached raises: that is not the same as a gauge without a record."""
    body = _get(NWIS.format(site=site, a=a.strftime("%Y-%m-%dT%H:%MZ"), b=b.strftime("%Y-%m-%dT%H:%MZ")))
    try:
        js = json.loads(body)
    except ValueError:
        return []
    out = {}
    for ts in js.get("value", {}).get("timeSeries", []):
        for blk in ts.get("values", []):
            for r in blk.get("value", []):
                try:
                    v = float(r["value"])
                    t = datetime.fromisoformat(r["dateTime"]).astimezone(timezone.utc).replace(second=0, microsecond=0)
                except (ValueError, KeyError):
                    continue
                if v > -100 and t.minute % 6 == 0:
                    out[t] = v
    return sorted(out.items())


def virginia_stations(extra=()):
    """NOAA water level stations in Virginia, active and historic, plus the ids in `extra`: {id: (name, lat, lon)}."""
    out = {}
    for kind in ("waterlevels", "historicwl"):
        try:
            js = json.loads(_get(f"{MDAPI}?type={kind}"))
        except Exception as e:
            print(f"station list {kind} unavailable: {e}")
            continue
        for s in js.get("stations", []):
            if s.get("state") == "VA" or str(s.get("id")) in extra:
                out.setdefault(str(s["id"]), (s.get("name", ""), float(s["lat"]), float(s["lng"])))
    return out


def peak_at_sewells(a, b):
    """(time, ft NAVD88, step in minutes) of the highest water at Sewells Point between two days."""
    rec, step = coops(SEWELLS, a, b, "water_level"), 6
    if len(rec) < 24:
        rec, step = coops(SEWELLS, a, b, "hourly_height"), 60
    if not rec:
        raise SystemExit(f"no Sewells Point record {a:%Y-%m-%d} to {b:%Y-%m-%d}")
    t, v = max(rec, key=lambda r: r[1])
    return t, v, step


def write_event(ev, peak_t, peak_ft, rows, gauges, extra=None):
    """rows: (gauge id, time, ft). gauges: {id: {name, lat, lon, step_min, ...}}."""
    d = os.path.join(EVENTS, ev["id"])
    os.makedirs(d, exist_ok=True)
    with gzip.open(os.path.join(d, "observations.csv.gz"), "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gauge", "date_time_z", "water_level_ft_navd88"])
        for g, t, v in sorted(rows):
            w.writerow([g, iso(t), f"{v:.2f}"])
    by = {}
    for g, t, v in rows:
        m = by.setdefault(g, [v, t, 0, t, t])
        if v > m[0]:
            m[0], m[1] = v, t
        m[2] += 1
        m[3], m[4] = min(m[3], t), max(m[4], t)
    doc = {"id": ev["id"], "name": ev["name"], "source": ev.get("source", "noaa"), "note": ev.get("note"),
           "peak_time_z": iso(peak_t), "peak_ft_navd88": round(peak_ft, 2), "peak_gauge": "Sewells Point",
           "window": [iso(peak_t - timedelta(hours=HALF_H)), iso(peak_t + timedelta(hours=HALF_H))],
           "datum": "ft NAVD88",
           "gauges": {g: {**gauges[g], "max_ft": round(by[g][0], 2), "max_time_z": iso(by[g][1]), "readings": by[g][2],
                          "first_z": iso(by[g][3]), "last_z": iso(by[g][4])} for g in sorted(by)},
           "made_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    doc.update(extra or {})
    with open(os.path.join(d, "event.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    return doc


def fetch_noaa(ev, stations, log=print, own=None, usgs_sites=None):
    """own: {NOAA id: NAVD88 above MLLW in ft} as StormSense uses it for its own NOAA gauges.
    usgs_sites: {StormSense station: (USGS site, name, lat, lon)}, asked for storms since 2007."""
    a, b = (datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc) for s in ev["search"])
    peak_t, peak_ft, _ = peak_at_sewells(a, b + timedelta(days=1))
    w0, w1 = peak_t - timedelta(hours=HALF_H), peak_t + timedelta(hours=HALF_H)
    rows, gauges, todo = [], {}, []

    def both(sid, datum):
        rec, step = coops(sid, w0, w1, "water_level", datum), 6
        if len(rec) < 24:
            rec, step = coops(sid, w0, w1, "hourly_height", datum), 60
        return rec, step
    for sid, (name, lat, lon) in sorted(stations.items()):
        rec, step = both(sid, "NAVD")
        if len(rec) < 6:
            rec, step = both(sid, "MLLW")
            if len(rec) >= 6:
                todo.append((sid, name, lat, lon, rec, step))     # recorded the storm, but NOAA has no NAVD88 for it
            continue
        d = datums(sid)
        gauges[sid] = {"name": name, "lat": lat, "lon": lon, "step_min": step, "agency": "NOAA CO-OPS", "datum": "NAVD88 (NOAA)",
                       "navd88_above_msl_ft": round(d["NAVD88"] - d["MSL"], 3) if d.get("NAVD88") is not None and d.get("MSL") is not None else None}
        rows += [(sid, t, v) for t, v in rec]
        time.sleep(0.1)
    for sid, name, lat, lon, rec, step in todo:
        d = datums(sid)
        if own and sid in own:
            off, how = own[sid], "NAVD88 from MLLW with StormSense's offset for this gauge"
        else:
            ref = [(_km(lat, lon, g["lat"], g["lon"]), k) for k, g in gauges.items() if g.get("navd88_above_msl_ft") is not None]
            if not ref or d.get("MSL") is None or d.get("MLLW") is None:
                continue
            km, k = min(ref)
            off = (d["MSL"] - d["MLLW"]) + gauges[k]["navd88_above_msl_ft"]
            how = f"NAVD88 estimated: this gauge's mean sea level plus the NAVD88-to-mean-sea-level difference at {gauges[k]['name']} ({km:.0f} km away)"
        gauges[sid] = {"name": name, "lat": lat, "lon": lon, "step_min": step, "agency": "NOAA CO-OPS", "datum": how,
                       "navd88_above_mllw_ft": round(off, 3), "navd_estimated": True}
        rows += [(sid, t, v - off) for t, v in rec]
    if usgs_sites and peak_t.year >= 2007:
        for st, (site, name, lat, lon) in sorted(usgs_sites.items()):
            try:
                rec = usgs(site, w0, w1)
            except Exception as e:
                log(f"{ev['id']}: USGS gauges not read ({e}); NOAA's only")
                break
            if len(rec) >= 24:
                gauges[st] = {"name": name, "lat": lat, "lon": lon, "step_min": 6, "agency": f"USGS {site}", "datum": "NAVD88 (USGS, parameter 62620)"}
                rows += [(st, t, v) for t, v in rec]
    doc = write_event(ev, peak_t, peak_ft, rows, gauges,
                      {"record": "NOAA CO-OPS verified water levels (api.tidesandcurrents.noaa.gov) and, since 2007, USGS tide gauges "
                                 "(waterservices.usgs.gov); ft NAVD88"})
    log(f"{ev['id']}: Sewells Point {peak_ft:.2f} ft NAVD88 at {iso(peak_t)}, {len(doc['gauges'])} gauges: "
        + ", ".join(f"{g['name']} {g['max_ft']:.2f}" + ("*" if g.get("navd_estimated") else "") for g in doc["gauges"].values()))
    return doc


BRIDGE_N = 12           # this many readings in one straight line are a gap the run's table drew across


def unbridged(rec):
    """[(time, ft)] without the straight lines an ens72 table draws across an outage: its observed_ft is interpolated
    onto the six-minute clock with no limit, so a gauge that was down through a storm's peak would otherwise seem to
    have recorded a slow, steady rise. The readings at both ends of a line are kept."""
    rec = sorted(rec)
    if len(rec) < BRIDGE_N:
        return rec
    v = np.array([x for _, x in rec])
    sec = np.array([(t - rec[0][0]).total_seconds() for t, _ in rec])
    even = np.abs(np.diff(sec, 2)) < 1                     # equal time steps
    line = even & (np.abs(np.diff(v, 2)) <= 0.0021)        # three readings in a line, to the table's three decimals
    drop = np.zeros(len(rec), bool)
    i = 0
    while i < len(line):
        if not line[i]:
            i += 1
            continue
        j = i
        while j < len(line) and line[j]:
            j += 1
        if j - i + 2 >= BRIDGE_N:                          # readings i .. j+1 are collinear
            drop[i + 1:j + 1] = True
        i = j
    return [r for r, d in zip(rec, drop) if not d]


def fetch_stormsense(ev, tarballs, stations_csv, log=print):
    """Observations and forecasts of a storm StormSense ran through, from the archived runs' ens72 tables (NAVD88)."""
    obs, fc, sts = {}, [], {}
    with open(stations_csv) as fh:
        for r in csv.DictReader(fh):
            sts[r["station"]] = r
    for path in sorted(tarballs):
        with tarfile.open(path) as tf:
            names = tf.getnames()
            latest = json.load(tf.extractfile(next(n for n in names if n.endswith("latest.json"))))
            cycle = latest["cycle"]
            for n in names:
                if not n.endswith("_ens72_NAVD.csv"):
                    continue
                st = os.path.basename(n)[:-len("_ens72_NAVD.csv")]
                for r in csv.DictReader(io.TextIOWrapper(tf.extractfile(n))):
                    t = r["date_time_z"]
                    if r.get("observed_ft"):
                        obs[(st, t)] = float(r["observed_ft"])      # a later run's reading replaces an earlier one
                    if r.get("primary_ft"):
                        fc.append((cycle, st, t, r["primary_ft"], r.get("primary_p10_ft", ""), r.get("primary_p90_ft", "")))
        log(f"read {os.path.basename(path)}")
    a, b = (datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc) for s in ev["search"])
    sw = [(parse(t), v) for (st, t), v in obs.items() if st == "SWPT" and a <= parse(t) <= b + timedelta(days=1)]
    peak_t, peak_ft = max(sw, key=lambda r: r[1])
    w0, w1 = peak_t - timedelta(hours=HALF_H), peak_t + timedelta(hours=HALF_H)
    by = {}
    for (st, t), v in obs.items():
        if w0 - timedelta(hours=6) <= parse(t) <= w1 + timedelta(hours=6):
            by.setdefault(st, []).append((parse(t), v))
    rows, bridged = [], {}
    for st, rec in by.items():
        kept = [r for r in unbridged(rec) if w0 <= r[0] <= w1]
        n = sum(w0 <= t <= w1 for t, _ in rec) - len(kept)
        if n:
            bridged[st] = n
        rows += [(st, t, v) for t, v in kept]
    gauges = {st: {"name": sts[st]["name"], "lat": float(sts[st]["lat"]), "lon": float(sts[st]["lon"]), "step_min": 6,
                   "agency": "StormSense gauge record (" + sts[st]["obs"].split(";")[0].split(":")[0] + ")"}
              for st in {r[0] for r in rows} if st in sts and sts[st]["lat"] and not sts[st].get("alias_of")}
    rows = [r for r in rows if r[0] in gauges]
    d = os.path.join(EVENTS, ev["id"])
    os.makedirs(d, exist_ok=True)
    cycles = sorted({c for c, *_ in fc})
    keep = [r for r in fc if r[1] in gauges and w0 - timedelta(hours=36) <= parse(r[2]) <= w1]
    with gzip.open(os.path.join(d, "forecasts.csv.gz"), "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cycle", "station", "date_time_z", "forecast_ft_navd88", "p10_ft", "p90_ft"])
        w.writerows(sorted(keep))
    last = max(r[1] for r in rows)
    doc = write_event(ev, peak_t, peak_ft, rows, gauges, {
        "record": "StormSense's own gauge observations as archived with each forecast run (ensemble_<cycle>.tar.gz), NAVD88; "
                  "the straight lines those tables draw across a gauge's outage are left out",
        "bridged_readings_left_out": {k: bridged[k] for k in sorted(bridged) if k in gauges},
        "forecasts": {"file": "forecasts.csv.gz", "cycles": cycles, "what": "the published StormSense fore72 (primary) of each run, "
                      "with its 10% and 90% lines, for every gauge"},
        "complete": bool(last >= w1 - timedelta(minutes=30)), "observed_through_z": iso(last)})
    log(f"{ev['id']}: Sewells Point {peak_ft:.2f} ft NAVD88 at {iso(peak_t)}, {len(gauges)} gauges, {len(cycles)} forecast runs, "
        f"observed through {iso(last)}" + ("" if doc["complete"] else " (the 36 hours after the peak are not all in yet)"))
    return doc


# ---------- for the map page ----------

def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    x = (lon2 - lon1) * p * math.cos((lat1 + lat2) * p / 2)
    return 6371.0 * math.hypot(x, (lat2 - lat1) * p)


def load(event_id):
    d = os.path.join(EVENTS, event_id)
    with open(os.path.join(d, "event.json")) as fh:
        doc = json.load(fh)
    rec = {}
    with gzip.open(os.path.join(d, "observations.csv.gz"), "rt") as fh:
        for r in csv.DictReader(fh):
            rec.setdefault(r["gauge"], []).append((parse(r["date_time_z"]), float(r["water_level_ft_navd88"])))
    return doc, rec


def on_grid(rec, t0, n, step_min=STEP_MIN, max_gap_min=130):
    """A gauge's readings on the page's time grid: straight lines between readings, nothing across a longer gap."""
    sec = np.array([(t - t0).total_seconds() for t, _ in rec])
    val = np.array([v for _, v in rec])
    o = np.argsort(sec)
    sec, val = sec[o], val[o]
    grid = np.arange(n) * step_min * 60.0
    out = np.interp(grid, sec, val, left=np.nan, right=np.nan)
    k = np.clip(np.searchsorted(sec, grid), 1, len(sec) - 1)
    gap = sec[k] - sec[k - 1]
    out[(gap > max_gap_min * 60) & (grid > sec[k - 1]) & (grid < sec[k])] = np.nan
    return out


def forecasts(event_id, station):
    """{cycle: [(time, ft NAVD88)]}: every archived StormSense forecast of one gauge through a storm ({} if none kept)."""
    path = os.path.join(EVENTS, event_id, "forecasts.csv.gz")
    out = {}
    if os.path.exists(path):
        with gzip.open(path, "rt") as fh:
            for r in csv.DictReader(fh):
                if r["station"] == station and r["forecast_ft_navd88"]:
                    out.setdefault(r["cycle"], []).append((parse(r["date_time_z"]), float(r["forecast_ft_navd88"])))
    return out


def ngvd29_shift_ft(lat, lon, _cache={}):
    """NAVD88 minus NGVD29 at a place, in feet (NOAA NGS VERTCON through NCAT; about -0.8 ft at Sewells Point)."""
    key = (round(lat, 3), round(lon, 3))
    if key not in _cache:
        q = urllib.parse.urlencode({"lat": key[0], "lon": key[1], "inDatum": "nad83(2011)", "outDatum": "nad83(2011)",
                                    "inVertDatum": "NGVD29", "outVertDatum": "NAVD88", "orthoHt": "0.0"})
        doc = json.loads(_get(f"{NCAT}?{q}"))
        _cache[key] = float(doc["destOrthoht"]) * FT_PER_M
    return _cache[key]


def _within(quality):
    """'Good: +/- 0.10 ft' -> ('Good', 0.1)"""
    name, _, rest = (quality or "").partition(":")
    m = re.search(r"([0-9.]+)\s*ft", rest)
    return name.strip(), (float(m.group(1)) if m else None)


def fev_marks(event_no, state="VA", log=print):
    """Virginia's high water marks of one Flood Event Viewer event, as MARK_COLS rows. Marks without a surveyed
    elevation are counted and left out; NGVD29 elevations are moved to NAVD88 at the mark's own place."""
    rows = json.loads(_get(f"{FEV}/hwms/filteredhwms/?event={event_no}&states={state}", timeout=180))
    out, bare = [], 0
    for r in rows:
        if r.get("elev_ft") is None:
            bare += 1
            continue
        lat, lon, datum = float(r["latitude_dd"]), float(r["longitude_dd"]), (r.get("vertical_datum_name") or "").strip()
        if datum == "NAVD88":
            navd = float(r["elev_ft"])
        elif datum == "NGVD29":
            navd = float(r["elev_ft"]) + ngvd29_shift_ft(lat, lon)
        else:
            bare += 1
            continue
        q, within = _within(r.get("hwm_quality_name"))
        text = " ".join(str(r.get(k) or "") for k in ("location_description", "notes"))
        place = "; ".join(t.strip() for t in (r.get("site_description"), r.get("location_description"), r.get("notes")) if t and t.strip())
        out.append({"id": f"{r['site_number']}-{r['id']}", "kind": "hwm", "lat": round(lat, 6), "lon": round(lon, 6),
                    "elev_ft_navd88": round(navd, 2), "elev_ft": r["elev_ft"], "datum": datum, "quality": q,
                    "within_ft": "" if within is None else within, "type": (r.get("hwm_type_name") or "").split(" (")[0],
                    "environment": r.get("environment") or "", "wave": "yes" if re.search(r"wave|run-?up", text, re.I) else "",
                    "county": (r.get("county_name") or "").title(), "waterbody": r.get("waterbody") or "",
                    "place": re.sub(r"\s+", " ", place)[:300], "time_z": "",
                    "source": f"USGS Flood Event Viewer, event {r.get('event_name') or event_no}"})
    log(f"  Flood Event Viewer event {event_no}: {len(rows)} marks in {state}, {len(out)} with a surveyed elevation ({bare} without)")
    return out


def report_table(url, state="Virginia"):
    """One state's rows of a USGS open-file report's table (xlsx): [{column heading: value}]."""
    import io
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("reading a report's tables needs openpyxl (pip install openpyxl)")
    ws = openpyxl.load_workbook(io.BytesIO(_get(url, timeout=180)), read_only=True, data_only=True).worksheets[0]
    head, out = None, []
    for r in ws.iter_rows(values_only=True):
        if not r or r[0] is None:
            continue
        if str(r[0]).strip().startswith("Site Id"):
            head = [re.sub(r"\s+", " ", str(c)).strip().lower() if c is not None else "" for c in r]
        elif head and len(r) > 1 and str(r[1]).strip() == state:
            out.append(dict(zip(head, r)))
    return out


def _col(row, *starts):
    for k, v in row.items():
        if any(k.startswith(s) for s in starts):
            return v


def report_marks(rep, log=print):
    """A report's high water marks and storm-tide sensor peaks in Virginia (ft NAVD88), as MARK_COLS rows."""
    out = []
    for kind, key in (("hwm", "hwm"), ("sensor", "sensors")):
        if not rep.get(key):
            continue
        n = 0
        for r in report_table(rep[key]):
            try:
                elev, lat, lon = float(_col(r, "peak storm")), float(_col(r, "latitude")), -abs(float(_col(r, "longitude")))
            except (TypeError, ValueError):
                continue                                   # "none": the sensor recorded no surge
            if kind == "sensor" and "water level" not in str(_col(r, "site type") or "").lower():
                continue                                   # the wave sensor beside it repeats the site
            when = _col(r, "peak storm-tide date", "peak storm- tide date", "peak storm-tide estimated", "peak storm- tide estimated")
            wave = str(_col(r, "wave") or "").strip().lower()
            out.append({"id": str(_col(r, "site id")).strip(), "kind": kind, "lat": round(lat, 6), "lon": round(lon, 6),
                        "elev_ft_navd88": round(elev, 2), "elev_ft": round(elev, 2), "datum": "NAVD88", "quality": "", "within_ft": "",
                        "type": "Storm-tide sensor" if kind == "sensor" else "", "environment": "",
                        "wave": wave if wave in ("yes", "no") else "", "county": str(_col(r, "county") or "").strip(),
                        "waterbody": "", "place": "",
                        "time_z": when.strftime("%Y-%m-%dT%H:%MZ") if kind == "sensor" and hasattr(when, "strftime") else "",
                        "source": rep["cite"]})
            n += 1
        log(f"  {rep['cite']}: {n} {'high water marks' if kind == 'hwm' else 'storm-tide sensor peaks'} in Virginia")
    return out


def merge_marks(report, viewer):
    """The report's rows first (its ids, its wave note), each taking the quality, type and place of the viewer's
    mark at the same spot and height; then the viewer's marks no report row matched."""
    left, out = list(viewer), []
    for m in report:
        twin = min((v for v in left if m["kind"] == "hwm" and abs(v["elev_ft_navd88"] - m["elev_ft_navd88"]) <= 0.055
                    and _km(m["lat"], m["lon"], v["lat"], v["lon"]) * 1000 <= MARK_NEAR_M),
                   key=lambda v: abs(v["elev_ft_navd88"] - m["elev_ft_navd88"]), default=None)   # the viewer keeps 0.1 ft
        if twin:
            left.remove(twin)
            m = {**m, **{k: twin[k] for k in ("quality", "within_ft", "type", "environment", "waterbody", "place")},
                 "wave": m["wave"] or twin["wave"], "source": f"{m['source']}; {twin['source']}"}
        out.append(m)
    return out + left


def fetch_marks(ev, log=print):
    """USGS high water marks and storm-tide sensor peaks for one storm -> events/<id>/marks.csv (ft NAVD88)."""
    spec = ev["marks"]
    log(f"{ev['name']}:")
    rows = merge_marks([m for rep in spec.get("reports", []) for m in report_marks(rep, log)],
                       fev_marks(spec["fev_event"], log=log) if spec.get("fev_event") else [])
    rows.sort(key=lambda m: (m["kind"], m["county"], m["id"]))
    os.makedirs(os.path.join(EVENTS, ev["id"]), exist_ok=True)
    with open(os.path.join(EVENTS, ev["id"], "marks.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, MARK_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    log(f"  {sum(m['kind'] == 'hwm' for m in rows)} high water marks and {sum(m['kind'] == 'sensor' for m in rows)} sensor peaks written")
    return rows


def marks(event_id):
    """The storm's surveyed marks (fetch_marks), numbers as floats; [] where it has none."""
    path = os.path.join(EVENTS, event_id, "marks.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for m in rows:
        for k in ("lat", "lon", "elev_ft_navd88"):
            m[k] = float(m[k])
        m["within_ft"] = float(m["within_ft"]) if m["within_ft"] else None
    return rows


def marks_for_page(event_id, frame, top_ft):
    """The marks inside a page's frame, in the page's units. frame: its static.json (epsg, x0, ytop, unit_m, width_m,
    height_m). Left off: a river's marks (the map is the tide's), and a mark more than MARK_OVER_FT above top_ft, the
    highest water any gauge recorded (a river's or a wave's run-up). Returns (marks, their sources, number left off)."""
    rows = marks(event_id)
    if not rows or not frame:
        return [], [], 0
    from pyproj import Transformer
    fwd = Transformer.from_crs(4326, frame["epsg"], always_xy=True).transform
    out, src, off = [], [], 0
    for m in rows:
        x, y = fwd(m["lon"], m["lat"])
        ux, uy = (x - frame["x0"]) / frame["unit_m"], (frame["ytop"] - y) / frame["unit_m"]
        if not (0 <= ux <= frame["width_m"] / frame["unit_m"] and 0 <= uy <= frame["height_m"] / frame["unit_m"]):
            continue
        if m["environment"] == "Riverine" or (top_ft is not None and m["elev_ft_navd88"] > top_ft + MARK_OVER_FT):
            off += 1
            continue
        if m["source"] not in src:
            src.append(m["source"])
        d = {"x": round(ux, 1), "y": round(uy, 1), "ft": m["elev_ft_navd88"], "k": "s" if m["kind"] == "sensor" else "h",
             "n": m["id"], "s": src.index(m["source"])}
        for k, v in (("q", m["quality"]), ("u", m["within_ft"]), ("t", m["type"] if m["kind"] == "hwm" else ""),
                     ("w", {"yes": 1, "no": 0}.get(m["wave"])), ("d", m["place"]), ("z", m["time_z"]),
                     ("o", f"{m['elev_ft']} ft {m['datum']}" if m["datum"] != "NAVD88" else "")):
            if v not in ("", None):
                d[k] = v
        out.append(d)
    return out, src, off


def for_page(stations, coops_of=None, ids=None, plot=None, frame=None):
    """stations: [{station, lat, lon}] (the page's gauges, and its plot gauge). coops_of: {station: NOAA id}.
    plot: the station of the page's plot, whose forecasts issued before the peak go along where the storm has them.
    frame: the page's static.json, for the USGS high water marks and sensor peaks that lie inside it.
    Returns the page's events document: per storm, each station's level through the 72 hours."""
    coops_of = coops_of or {}
    out = []
    for ev in events():
        if ids and ev["id"] not in ids:
            continue
        if not os.path.exists(os.path.join(EVENTS, ev["id"], "event.json")):
            continue
        doc, rec = load(ev["id"])
        t0 = parse(doc["window"][0])
        n = int(HALF_H * 2 * 60 / STEP_MIN) + 1
        peak_i = int(round((parse(doc["peak_time_z"]) - t0).total_seconds() / 60 / STEP_MIN))
        series = {g: on_grid(r, t0, n) for g, r in rec.items() if len(r) > 1}
        have = np.zeros(n, bool)
        for v in series.values():
            have |= np.isfinite(v)
        near = slice(max(0, peak_i - int(NEAR_PEAK_H * 60 / STEP_MIN)), peak_i + int(NEAR_PEAK_H * 60 / STEP_MIN) + 1)
        good = {g: v for g, v in series.items()
                if np.isfinite(v[near]).mean() > 0.7 and np.isfinite(v[have]).mean() >= MIN_COVER}
        sts = {}
        for s in stations:
            own = s["station"] if s["station"] in good else coops_of.get(s["station"])
            if own in good:
                g, km = own, 0.0
            elif good:
                g, km = min(((k, _km(s["lat"], s["lon"], doc["gauges"][k]["lat"], doc["gauges"][k]["lon"])) for k in good),
                            key=lambda r: r[1])
            else:
                continue
            sts[s["station"]] = {"f": [None if not np.isfinite(x) else round(float(x), 2) for x in good[g]],
                                 "from": doc["gauges"][g]["name"], "from_id": g, "own": km == 0.0, "km": round(km, 1)}
        runs = []
        if plot:
            for cyc, rec_f in sorted(forecasts(doc["id"], plot).items()):
                if parse(cyc.replace("Z", ":00Z")) < parse(doc["peak_time_z"]) and len(rec_f) > 1:
                    f = on_grid(rec_f, t0, n)
                    if np.isfinite(f).any():
                        runs.append({"cycle": cyc, "max_ft": round(float(np.nanmax(f)), 2),
                                     "f": [None if not np.isfinite(x) else round(float(x), 2) for x in f]})
        tops = [g["max_ft"] for k, g in doc["gauges"].items() if k in good and g.get("max_ft") is not None]
        mk, mk_src, mk_off = marks_for_page(doc["id"], frame, max(tops) if tops else None)
        out.append({"id": doc["id"], "name": doc["name"], "note": doc.get("note"), "t0": iso(t0)[:-1] + ":00Z",
                    **({"marks": mk, "mark_sources": mk_src} if mk else {}), **({"marks_left_off": mk_off} if mk_off else {}),
                    "step_min": STEP_MIN, "n": n, "peak_i": peak_i, "peak_time_z": doc["peak_time_z"],
                    "peak_ft": doc["peak_ft_navd88"], "gauges": len(good), "gauges_recorded": len(doc["gauges"]),
                    "complete": doc.get("complete", True), "observed_through_z": doc.get("observed_through_z"),
                    "record": doc.get("record"), "stations": sts, **({"runs": runs, "runs_at": plot} if runs else {})})
    return {"events": out}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", choices=["noaa", "stormsense", "marks"])
    ap.add_argument("--event")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--runs", nargs="*", default=[], help="archived run tarballs (ensemble_<cycle>.tar.gz)")
    ap.add_argument("--stations", default=os.path.join(HERE, "..", "stormsense", "stations.csv"))
    a = ap.parse_args(argv)
    if a.source == "marks":
        todo = [e for e in events() if e.get("marks") and (a.all or e["id"] == a.event)]
        if not todo:
            raise SystemExit("no such event in events.json with a \"marks\" entry")
        for ev in todo:
            fetch_marks(ev)
        return
    todo = [e for e in events() if e.get("source", "noaa") == a.source and (a.all or e["id"] == a.event)]
    if not todo:
        raise SystemExit("no such event in events.json for that source")
    if a.source == "stormsense":
        for ev in todo:
            fetch_stormsense(ev, a.runs, a.stations)
        return
    extra, own, sites = set(), {}, {}
    with open(a.stations) as fh:
        for r in csv.DictReader(fh):
            if not r["lat"] or r.get("alias_of"):
                continue
            for p in r["obs"].split(";"):
                kind, _, sid = p.strip().partition(":")
                if kind == "coops":
                    extra.add(sid)
                    if r.get("navd88_above_mllw_ft"):
                        own[sid] = float(r["navd88_above_mllw_ft"])
                elif kind == "nwis" and not r.get("suspect_obs"):
                    sites[r["station"]] = (sid, r["name"].replace(" (IN MLLW)", ""), float(r["lat"]), float(r["lon"]))
    stations = virginia_stations(extra)
    print(f"{len(stations)} NOAA water level stations in Virginia (active and historic) and StormSense's own NOAA gauges; "
          f"{len(sites)} USGS tide gauges")
    for ev in todo:
        fetch_noaa(ev, stations, own=own, usgs_sites=sites)


if __name__ == "__main__":
    main()
