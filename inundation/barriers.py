"""Flood barriers the bare-earth DEM does not show: floodwalls and levees from the USACE National Levee Database.

A floodwall is a few decimetres thick, so a 1 m bare-earth DEM has a gap where it stands, and the bathtub in depth.py
then floods the ground behind it (downtown Norfolk behind its floodwall, for one). The National Levee Database has
each system's line (walls, embankments and closure structures as one route) and its leveed area:

    https://levees.sec.usace.army.mil/  (feature service NLD2_PUBLIC_v1, layers System_Routes and Leveed_Areas)

Here the route is burned into the terrain as cells water cannot cross, and the leveed areas are handed to drainage.py
so that pipes do not carry water under the wall (tide gates and the system's pump station hold it back). Both are
assumptions of a working, closed system that is not overtopped at any level this tool evaluates (10 ft NAVD88 and
below): the gates are taken as shut. Water can still come round the end of a wall where the ground there is low.
"""
import json
import urllib.parse
import urllib.request

import numpy as np

NLD = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/NLD2_PUBLIC_v1/FeatureServer"
LAYERS = {"routes": 14, "areas": 17}
SOURCE = "USACE National Levee Database (system routes and leveed areas)"


def fetch(bbox, log=None):
    """{"routes": [...], "areas": [...]} GeoJSON features that touch bbox = (west, south, east, north), WGS84."""
    out = {}
    for name, layer in LAYERS.items():
        q = {"where": "1=1", "geometry": ",".join(f"{v:.6f}" for v in bbox), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
             "spatialRel": "esriSpatialRelIntersects", "outFields": "SYSTEM_ID,SYSTEM_NAME", "outSR": 4326, "f": "geojson"}
        req = urllib.request.Request(f"{NLD}/{layer}/query?" + urllib.parse.urlencode(q), headers={"User-Agent": "python-urllib"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
        if "features" not in d:
            raise RuntimeError(f"levee database {name}: {str(d)[:200]}")
        out[name] = d["features"]
        if log:
            log(f"  barriers {name}: {len(out[name])} ({', '.join(sorted({str(f['properties'].get('SYSTEM_NAME')) for f in out[name]})) or 'none'})")
    return out


def shapes(doc, transform):
    """(lines, polygons) in map coordinates from fetch()'s features."""
    import shapely
    from shapely.geometry import shape

    def move(f):
        g = shapely.force_2d(shape(f["geometry"]))
        return shapely.transform(g, lambda c: np.column_stack(transform(c[:, 0], c[:, 1])))
    lines = [move(f) for f in doc.get("routes", []) if f.get("geometry")]
    polys = [shapely.make_valid(move(f)) for f in doc.get("areas", []) if f.get("geometry")]
    return lines, polys


def cells(lines, shape, origin, half=1, step=0.5):
    """(rows, cols) of the DEM cells a wall stands on: every cell within `half` cells of the line, so that water
    cannot slip through diagonally. Empty arrays when there is nothing on the grid."""
    x0, ytop = origin
    h, w = shape
    rows, cols = [], []
    for line in lines:
        for part in getattr(line, "geoms", [line]):
            if part.geom_type != "LineString" or part.length == 0:
                continue
            xy = np.array([part.interpolate(d).coords[0] for d in np.append(np.arange(0, part.length, step), part.length)])
            r, c = np.floor(ytop - xy[:, 1]).astype(int), np.floor(xy[:, 0] - x0).astype(int)
            for dr in range(-half, half + 1):
                for dc in range(-half, half + 1):
                    rows.append(r + dr); cols.append(c + dc)
    if not rows:
        return np.zeros(0, int), np.zeros(0, int)
    r, c = np.concatenate(rows), np.concatenate(cols)
    ok = (r >= 0) & (r < h) & (c >= 0) & (c < w)
    rc = np.unique(np.column_stack([r[ok], c[ok]]), axis=0)
    return rc[:, 0], rc[:, 1]


def zone(polys, x, y):
    """For each point the number of the leveed area it lies in, from 1 (0: none)."""
    import shapely
    z = np.zeros(len(x), int)
    for k, p in enumerate(polys, 1):
        z[shapely.contains_xy(p, x, y) & (z == 0)] = k
    return z
