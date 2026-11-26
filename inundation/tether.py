"""Which gauge speaks for which road piece and building.

To start, each feature takes the nearest StormSense gauge in a straight line (suspect and stand-in
stations left out). That already cuts a HUC-12 watershed into smaller zones where gauges are dense.
`tether_overrides.csv` (huc12, station, note) is the table Derek edits: a row sends everything in that
HUC-12 to the named gauge, for places where the nearest gauge sits on the wrong water.
"""
import csv
import json
import os
import urllib.parse
import urllib.request

import numpy as np

HERE = os.path.dirname(__file__)
STATIONS = os.path.join(HERE, "..", "stormsense", "stations.csv")
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


def nearest(x, y, sts, transform, huc=None, forced=None):
    """Station id and distance (km) for each point. sts from stations(); forced: {huc12: station}."""
    from scipy.spatial import cKDTree
    sx, sy = transform(np.array([s["lon"] for s in sts]), np.array([s["lat"] for s in sts]))
    ids = np.array([s["station"] for s in sts], dtype=object)
    d, k = cKDTree(np.column_stack([sx, sy])).query(np.column_stack([x, y]))
    st, km = ids[k].copy(), d / 1000.0
    if huc is not None and forced:
        where = {s["station"]: i for i, s in enumerate(sts)}
        for h, s in forced.items():
            if s not in where:
                raise ValueError(f"tether_overrides.csv: {s} is not a station that can be tethered to")
            m = huc == h
            st[m] = s
            km[m] = np.hypot(x[m] - sx[where[s]], y[m] - sy[where[s]]) / 1000.0
    return st, km
