"""Which gauge speaks for which road piece and building.

To start, each feature takes the nearest StormSense gauge in a straight line (suspect and stand-in
stations left out). That already cuts a HUC-12 watershed into smaller zones where gauges are dense.
`tether_overrides.csv` (huc12, station, note) is the table Derek edits: a row sends everything in that
HUC-12 to the named gauge, for places where the nearest gauge sits on the wrong water. Several gauges joined by
"|" mean the nearest of those, and a code ending in * covers a whole basin.
"""
import csv
import json
import os
import urllib.parse
import urllib.request

import numpy as np

HERE = os.path.dirname(__file__)
STATIONS = os.path.join(HERE, "..", "stormsense", "stations.csv")
DATUMS = os.path.join(HERE, "..", "stormsense", "data", "datums")
OVERRIDES = os.path.join(HERE, "tether_overrides.csv")
WBD = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/Watershed_Boundary_Dataset_HUC_12s/FeatureServer/0"


def stations(path=STATIONS):
    """Gauges a feature may be tethered to: located, not a stand-in for another gauge, observations trusted."""
    out = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not r["lat"] or not r["lon"] or r.get("alias_of") or r.get("suspect_obs"):
                continue
            out.append({"station": r["station"], "name": r["name"], "lat": float(r["lat"]), "lon": float(r["lon"])})
    return out


def high_tide_ft(station, folder=None):
    """Mean higher high water at a gauge, ft NAVD88, from its datum sheet (stormsense/data/datums); None without
    one. From 0.21 ft at Windmill Point and 0.52 at Tangier to 1.15 at Sewells Point and 2.11 at Oyster."""
    path = os.path.join(folder or DATUMS, f"{station}.json")
    if not station or not os.path.exists(path):
        return None
    with open(path) as fh:
        d = json.load(fh)
    if d.get("MHHW") is None or d.get("NAVD88") is None:
        return None
    return float(d["MHHW"]) - float(d["NAVD88"])


def huc12(bounds_lonlat):
    """HUC-12 polygons (GeoJSON features with huc12 and name) touching (west, south, east, north)."""
    q = urllib.parse.urlencode({
        "geometry": ",".join(str(v) for v in bounds_lonlat), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "huc12,name", "outSR": 4326,
        "geometryPrecision": 6, "f": "geojson"}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{WBD}/query", data=q), timeout=180) as r:
        d = json.loads(r.read())
    if d.get("error"):
        raise RuntimeError(str(d["error"]))
    return d["features"]


def in_polygons(features, transform, x, y, key="huc12"):
    """The `key` of the polygon each point falls in ('' if none)."""
    import shapely
    from shapely.geometry import shape
    out = np.full(len(x), "", dtype=object)
    for f in features:
        g = shapely.transform(shapely.force_2d(shape(f["geometry"])), lambda c: np.column_stack(transform(c[:, 0], c[:, 1])))
        shapely.prepare(g)
        out[shapely.contains_xy(g, x, y) & (out == "")] = f["properties"][key]
    return out


def overrides(path=OVERRIDES):
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as fh:
        return {r["huc12"].strip(): r["station"].strip() for r in csv.DictReader(fh) if r.get("huc12") and r.get("station")}


def group(station):
    """The gauges a rule names. "WACH|CHIV|CCHV" means the nearest of these: the gauges on that water, for a basin
    whose ground lies nearer to a gauge on other water (the sea side of the Eastern Shore, the Rappahannock side of
    the Northern Neck)."""
    return [t.strip() for t in str(station or "").split("|") if t.strip()]


NO_GAUGE = "-"       # in tether_overrides.csv: no StormSense gauge stands for this water, so its ground is left out


def rule(huc12, forced):
    """The station tether_overrides.csv gives a HUC-12 ("" for NO_GAUGE), or None where it has no row. A row's
    huc12 can end in * to cover every HUC-12 that starts with it; a full code wins over a prefix, a longer prefix
    over a shorter one."""
    if not forced:
        return None
    s = forced.get(huc12)
    if s is None:
        for k in sorted((k for k in forced if k.endswith("*") and str(huc12).startswith(k[:-1])), key=len, reverse=True):
            s = forced[k]
            break
    return None if s is None else ("" if s == NO_GAUGE else s)


def nearest(x, y, sts, transform, huc=None, forced=None):
    """Station id and distance (km) for each point. sts from stations(); forced: overrides(). A point in a
    HUC-12 that the overrides mark as having no gauge gets station "" and distance NaN."""
    from scipy.spatial import cKDTree
    sx, sy = transform(np.array([s["lon"] for s in sts]), np.array([s["lat"] for s in sts]))
    ids = np.array([s["station"] for s in sts], dtype=object)
    d, k = cKDTree(np.column_stack([sx, sy])).query(np.column_stack([x, y]))
    st, km = ids[k].copy(), d / 1000.0
    if huc is not None and forced:
        where = {s["station"]: i for i, s in enumerate(sts)}
        huc = np.asarray(huc, dtype=object)
        for h in sorted(set(huc.tolist())):
            s = rule(h, forced)
            if s is None:
                continue
            m = huc == h
            if s == "":
                st[m], km[m] = "", np.nan
                continue
            names = group(s)
            bad = [t for t in names if t not in where]
            if bad:
                raise ValueError(f"tether_overrides.csv: {', '.join(bad)} is not a station that can be tethered to")
            if len(names) == 1:
                st[m] = s
                km[m] = np.hypot(x[m] - sx[where[s]], y[m] - sy[where[s]]) / 1000.0
            else:                                            # the nearest gauge on this water
                own = np.array([where[t] for t in names])
                dd, kk = cKDTree(np.column_stack([sx[own], sy[own]])).query(np.column_stack([x[m], y[m]]))
                st[m], km[m] = ids[own[kk]], dd / 1000.0
    return st, km
