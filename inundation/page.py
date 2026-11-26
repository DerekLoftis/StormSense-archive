"""The area's map page: a self-contained page (no CDN, no map tiles) that colors roads and buildings
from the gauges' forecast as the reader moves through the 72 hours.

prep writes the parts that do not change (`write_static`): static.json (road pieces, road network,
buildings, gauges; coordinates in half metres from the area's north-west corner, packed as base64 typed
arrays), extent.png (a 5 m raster: red = index of the level at which the cell goes under, green = the
gauge it is tethered to, blue = under water at an ordinary high tide) and index.html. Each forecast run then only writes
levels.json (`write_levels`): every tethered gauge's forecast and last observation.
"""
import base64
import json
import os
import shutil
import time

import numpy as np

from . import FLOODED_FT, PONDED_FT, damage, depth

HERE = os.path.dirname(__file__)
UNIT_M = 0.5
CELL_M = 5
NO_LEVEL = 32767
WATER_FT = 1.25   # drawn as ordinary water: under water at about mean higher high water (1.15 ft NAVD88 at Sewells Point)
DISCLAIMER = ("StormSense(TM) Predictive Plots are computer-generated operational storm tide forecasts; "
              "the user assumes any and all risk.")


def b64(a, dtype):
    return base64.b64encode(np.ascontiguousarray(a, dtype=dtype).tobytes()).decode()


def units(v, origin):
    return np.clip(np.round((np.asarray(v, float) - origin) / UNIT_M), 0, 65535).astype(np.uint16)


def hundredths(a):
    a = np.asarray(a, float)
    return np.where(np.isfinite(a), np.clip(np.round(a * 100), -32000, 32000), NO_LEVEL).astype(np.int16)


def simplify(xy, tol):
    """Douglas-Peucker on an (n, 2) array."""
    if len(xy) < 3:
        return xy
    keep = np.zeros(len(xy), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(xy) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        p, q = xy[a], xy[b]
        d = q - p
        n = np.hypot(*d)
        seg = xy[a + 1:b]
        dist = np.abs(d[0] * (seg[:, 1] - p[1]) - d[1] * (seg[:, 0] - p[0])) / n if n > 0 else np.hypot(*(seg - p).T)
        k = int(dist.argmax())
        if dist[k] > tol:
            keep[a + 1 + k] = True
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return xy[keep]


def road_class(mtfcc):
    m = str(mtfcc or "")
    return 2 if m.startswith("S1100") else 1 if m.startswith("S1200") else 0


def raster(dem, cidx, origin, sts_xy, cell=CELL_M):
    """5 m picture of the 1 m grids. Level index: the median over each block, so a cell shows wet once
    half of it is. Water: wet at an ordinary high tide (WATER_FT). Zone: nearest gauge."""
    from scipy.spatial import cKDTree
    h, w = (dem.shape[0] // cell) * cell, (dem.shape[1] // cell) * cell
    nh, nw = h // cell, w // cell
    idx = np.empty((nh, nw), np.uint8)
    rows = max(1, 1000 // cell)
    for r in range(0, nh, rows):       # in strips, to keep memory flat
        blk = np.asarray(cidx[r * cell:min(r + rows, nh) * cell, :w])
        b = blk.reshape(-1, cell, nw, cell).transpose(0, 2, 1, 3).reshape(-1, nw, cell * cell)
        idx[r:r + b.shape[0]] = np.partition(b, (cell * cell) // 2, axis=2)[:, :, (cell * cell) // 2]
    water = idx <= int(np.searchsorted(depth.LEVELS_FT, WATER_FT))
    cx = origin[0] + (np.arange(nw) + 0.5) * cell
    cy = origin[1] - (np.arange(nh) + 0.5) * cell
    gx, gy = np.meshgrid(cx, cy)
    _, zone = cKDTree(sts_xy).query(np.column_stack([gx.ravel(), gy.ravel()]))
    rgb = np.dstack([idx, zone.reshape(nh, nw).astype(np.uint8), np.where(water, 255, 0).astype(np.uint8)])
    return rgb


def write_static(out, name, title, bounds, epsg, pieces, road_cols, roads_raw, fwd, bgeoms, bldg_cols, dem, cidx,
                 sts, sources, values=None):
    """sts: [{station, name, lat, lon}] the gauges features are tethered to, in the order used for indices.
    values: {"tax_year", "firm_year", "zillow_month"} when the building table has the value.attach columns."""
    from PIL import Image
    os.makedirs(out, exist_ok=True)
    x0, ytop = bounds[0], bounds[3]
    ux = lambda x: units(x, x0)                       # noqa: E731
    uy = lambda y: units(-(np.asarray(y, float) - ytop), 0)   # noqa: E731  (y runs down the page)
    order = {s["station"]: i for i, s in enumerate(sts)}
    sx, sy = fwd(np.array([s["lon"] for s in sts]), np.array([s["lat"] for s in sts]))

    # road pieces that can go under by the top level and are not on a bridge
    rc = road_cols
    keep = np.flatnonzero((np.asarray(rc["bridge"]) == 0) & np.isfinite(rc["connect_ft"]))
    pxy = np.column_stack([ux(pieces["x0"][keep]), uy(pieces["y0"][keep]), ux(pieces["x1"][keep]), uy(pieces["y1"][keep])])
    p = {"n": int(len(keep)), "xy": b64(pxy, "<u2"), "ground": b64(hundredths(rc["ground_ft"][keep]), "<i2"),
         "connect": b64(hundredths(rc["connect_ft"][keep]), "<i2"),
         "station": b64([order[s] for s in rc["station"][keep]], "u1"),
         "road": b64(np.asarray(pieces["road"])[keep], "<u2" if len(roads_raw) < 65536 else "<u4"),
         "road_bytes": 2 if len(roads_raw) < 65536 else 4,
         "len": b64(np.round(np.asarray(pieces["length_m"])[keep] * 100), "<u2"), "id": b64(keep, "<u4")}
    if "via_pipes" in rc:
        p["via"] = b64(np.asarray(rc["via_pipes"])[keep], "u1")

    # the whole road network, thinned, as the map's background
    counts, coords, cls, names = [], [], [], []
    road_of_piece = np.asarray(pieces["road"])
    on_bridge = np.bincount(road_of_piece, weights=(np.asarray(rc["bridge"]) > 0), minlength=len(roads_raw))
    n_piece = np.maximum(np.bincount(road_of_piece, minlength=len(roads_raw)), 1)
    for i, f in enumerate(roads_raw):
        g = f.get("geometry") or {}
        parts = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
        for part in parts:
            c = np.asarray(part, float)[:, :2]
            if len(c) < 2:
                continue
            x, y = fwd(c[:, 0], c[:, 1])
            s = simplify(np.column_stack([x, y]), 1.0)
            counts.append(len(s)); coords.append(np.column_stack([ux(s[:, 0]), uy(s[:, 1])]))
            cls.append(road_class(f["properties"].get("MTFCC")) + (4 if on_bridge[i] / n_piece[i] > 0.5 else 0))
        names.append(str(f["properties"].get("ST_FULL") or "").strip())
    uniq = sorted(set(names))
    where = {n: i for i, n in enumerate(uniq)}
    r = {"n": len(counts), "count": b64(counts, "<u2"), "xy": b64(np.vstack(coords), "<u2"), "cls": b64(cls, "u1"),
         "names": uniq, "name_of": b64([where[n] for n in names], "<u2")}

    # buildings that can go under and do not stand in the water
    bc = bldg_cols
    bkeep = [i for i in np.flatnonzero(np.isfinite(bc["connect_ft"]) & (np.asarray(bc["over_water"]) == 0))
             if bgeoms[i] is not None and not bgeoms[i].is_empty]
    bcount, bxy = [], []
    for i in bkeep:
        g = bgeoms[i]
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda q: q.area)
        ring = np.asarray(g.exterior.coords)[:, :2]
        tol = 0.4
        s = simplify(ring, tol)
        while len(s) > 255:
            tol *= 2
            s = simplify(ring, tol)
        bcount.append(len(s)); bxy.append(np.column_stack([ux(s[:, 0]), uy(s[:, 1])]))
    b = {"n": len(bkeep), "count": b64(bcount, "u1"), "xy": b64(np.vstack(bxy), "<u2"),
         "ground": b64(hundredths(np.asarray(bc["ground_ft"])[bkeep]), "<i2"),
         "connect": b64(hundredths(np.asarray(bc["connect_ft"])[bkeep]), "<i2"),
         "station": b64([order[s] for s in np.asarray(bc["station"])[bkeep]], "u1"), "id": b64(bkeep, "<u4")}
    if "via_pipes" in bc:
        b["via"] = b64(np.asarray(bc["via_pipes"])[bkeep], "u1")
    vdoc = None
    if values and "value_bldg" in bc:
        def money(k):
            v = np.asarray(bc[k], float)[bkeep]
            return b64(np.clip(np.where(np.isfinite(v), v, 0), 0, 4294967295), "<u4")
        yr = np.asarray(bc["year_built"], float)[bkeep]
        zips = sorted({z for z in np.asarray(bc["zip"])[bkeep] if z})[:255]
        zi = {z: i for i, z in enumerate(zips)}
        zh = {}
        for z, v in zip(np.asarray(bc["zip"])[bkeep], np.asarray(bc["zhvi"], float)[bkeep]):
            if z and np.isfinite(v):
                zh[z] = int(v)
        names = list(damage.STRUCTURE_PCT)
        use = [str(u or "").strip() for u in np.asarray(bc["use"])[bkeep]]
        uses = sorted({u for u in use if u})[:255]
        ui = {u: i for i, u in enumerate(uses)}
        b.update(year=b64(np.where(np.isfinite(yr), yr, 0), "<u2"), vtotal=money("value_total"), vbldg=money("value_bldg"),
                 res=b64(np.asarray(bc["residential"])[bkeep], "u1"),
                 curve=b64([names.index(c) if c in names else 0 for c in np.asarray(bc["curve"])[bkeep]], "u1"),
                 ffh=b64(np.nan_to_num(np.asarray(bc["first_floor_ft"], float)[bkeep], nan=3), "u1"),
                 zip=b64([zi.get(z, 255) for z in np.asarray(bc["zip"])[bkeep]], "u1"),
                 use=b64([ui.get(u, 255) for u in use], "u1"))
        vdoc = {**values, "zips": zips, "zhvi": [zh.get(z) for z in zips], "uses": uses,
                "damage": {"depth0": float(damage.DEPTH_FT[0]), "curves": [damage.STRUCTURE_PCT[k] for k in names], "citation": damage.CITATION}}

    rgb = raster(dem, cidx, (x0, ytop), np.column_stack([sx, sy]))
    Image.fromarray(rgb, "RGB").save(os.path.join(out, "extent.png"), optimize=True)

    ok_road = np.asarray(rc["bridge"]) == 0
    static = {
        "name": name, "title": title, "epsg": epsg, "x0": x0, "ytop": ytop, "unit_m": UNIT_M,
        "width_m": bounds[2] - bounds[0], "height_m": bounds[3] - bounds[1],
        "levels": {"start": float(depth.LEVELS_FT[0]), "step": 0.25, "n": int(len(depth.LEVELS_FT))},
        "ponded_ft": PONDED_FT, "flooded_ft": FLOODED_FT, "no_level": NO_LEVEL,
        "stations": [{"id": s["station"], "name": s["name"], "x": int(ux([sx[i]])[0]), "y": int(uy([sy[i]])[0]),
                      "inside": bool(bounds[0] <= sx[i] <= bounds[2] and bounds[1] <= sy[i] <= bounds[3])} for i, s in enumerate(sts)],
        "pieces": p, "roads": r, "buildings": b, "values": vdoc,
        "raster": {"file": "extent.png", "cell_m": CELL_M, "width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "totals": {"road_miles": round(float(np.asarray(rc["length_m"])[ok_road].sum() / 1609.344), 1),
                   "road_pieces": int(ok_road.sum()), "buildings": int((np.asarray(bc["over_water"]) == 0).sum())},
        "prepared_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "sources": sources, "disclaimer": DISCLAIMER,
    }
    with open(os.path.join(out, "static.json"), "w") as fh:
        json.dump(static, fh, separators=(",", ":"))
    for logo in ("stormsense_logo.png", "ccrm_logo.png"):
        shutil.copy(os.path.join(HERE, "..", "crest", "branding", logo), os.path.join(out, logo))
    write_index(out, title)
    return static


def write_index(out, title):
    with open(os.path.join(HERE, "templates", "map.html")) as fh:
        body = fh.read().replace("__AREA__", title)
    with open(os.path.join(out, "page.html"), "w") as fh:      # the bare page, for hosts that add their own shell
        fh.write(body)
    with open(os.path.join(out, "index.html"), "w") as fh:
        fh.write('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                 '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                 '</head>\n<body>\n' + body + "\n</body>\n</html>\n")


def write_levels(out, levels, summary):
    """levels: {station: status.station_levels()}. One time base for all gauges (the forecast's own step)."""
    ts = sorted({t for lv in levels.values() for t in lv["t"]})
    if not ts:
        return
    step = min((b - a).total_seconds() for a, b in zip(ts, ts[1:])) if len(ts) > 1 else 360
    t0 = ts[0]
    n = int(round((ts[-1] - t0).total_seconds() / step)) + 1
    grid = np.arange(n) * step

    def on_grid(lv, key):
        sec = np.array([(t - t0).total_seconds() for t in lv["t"]])
        v = np.interp(grid, sec, lv[key], left=np.nan, right=np.nan)
        return [None if not np.isfinite(x) else round(float(x), 2) for x in v]

    doc = {"made_utc": summary.get("made_utc"), "t0": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "step_min": step / 60, "n": n,
           "stations": {st: {"updated": lv["updated"], "guidance": lv["guidance"], "obs_ft": lv["obs_ft"],
                             "obs_time": lv["obs_time"].strftime("%Y-%m-%dT%H:%M:%SZ") if lv["obs_time"] else None,
                             "flood_levels_ft": lv["flood_levels_ft"],
                             "f": on_grid(lv, "f"), "p10": on_grid(lv, "p10"), "p90": on_grid(lv, "p90")}
                        for st, lv in levels.items()},
           "missing": summary.get("stations_without_forecast", [])}
    with open(os.path.join(out, "levels.json"), "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
