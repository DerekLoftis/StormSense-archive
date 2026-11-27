"""The area's map page: a self-contained page (no CDN, no map tiles) that colors roads and buildings
from the gauges' forecast as the reader moves through the 72 hours.

prep writes the parts that do not change (`write_static`): static.json (road pieces, road network,
buildings, gauges; coordinates in half metres from the area's north-west corner, packed as base64 typed
arrays), tiles/ (a 5 m raster cut into 1024-pixel PNG tiles under a small overview, so a phone never has to
hold the whole picture: red = index of the level at which the cell goes under, green = the gauge it is
tethered to (255: no gauge), blue = the ground, 0 where it is under water at an ordinary high tide) and
index.html. Each forecast run then only writes levels.json (`write_levels`): every tethered gauge's forecast
and last observation, the plot gauge's forecast, and events.json (the historical floods, `history.for_page`).

    python -m inundation.page --prepared <dir>/page --work <dir> [--title Norfolk]

redraws the raster tiles and the page of an area already prepared, from the grids prep left in its work folder.
"""
import base64
import json
import os
import shutil
import time

import numpy as np

from . import FLOODED_FT, PONDED_FT, damage, depth

HERE = os.path.dirname(__file__)
UNIT_M = 0.5      # the page's coordinates are whole units in 16 bits: half a metre reaches 32.7 km (unit_for)
CELL_M = 5
NO_LEVEL = 32767
WATER_FT = 1.25   # drawn as ordinary water: under water at about mean higher high water (1.15 ft NAVD88 at Sewells Point)
TILE = 1024       # raster tile, pixels
OVERVIEW_MAX = 2048
GROUND0_FT, GROUND_STEP_FT = -2.5, 0.05     # the tiles' blue channel: 1..255 is the ground from -2.5 ft in 0.05 ft steps
NO_ZONE = 255
DISCLAIMER = ("StormSense(TM) Predictive Plots are computer-generated operational storm tide forecasts; "
              "the user assumes any and all risk.")


def b64(a, dtype):
    return base64.b64encode(np.ascontiguousarray(a, dtype=dtype).tobytes()).decode()


def units(v, origin, unit=UNIT_M):
    return np.clip(np.round((np.asarray(v, float) - origin) / unit), 0, 65535).astype(np.uint16)


def unit_for(bounds):
    """The page's coordinate unit in metres: half a metre, doubled until the frame's longer side fits in 16 bits
    (a county 40 km long drew everything past 32.7 km on one line)."""
    side, unit = max(bounds[2] - bounds[0], bounds[3] - bounds[1]), UNIT_M
    while side / unit > 65535:
        unit *= 2
    return unit


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


def raster(dem, cidx, origin, sts_xy, cell=CELL_M, hucs=None, fwd=None, forced=None, ids=None):
    """5 m picture of the 1 m grids: (level index, gauge zone, ground code), each uint8 (rows, cols).
    Level index and ground: the median over each block, so a cell shows wet once half of it is. Ground code 0:
    wet at an ordinary high tide (WATER_FT). Zone: the nearest gauge, or the gauge tether_overrides.csv gives
    the cell's HUC-12; NO_ZONE where no gauge stands for the water (never drawn flooded)."""
    from scipy.spatial import cKDTree
    h, w = (dem.shape[0] // cell) * cell, (dem.shape[1] // cell) * cell
    nh, nw = h // cell, w // cell
    idx, gnd, zone = np.empty((nh, nw), np.uint8), np.empty((nh, nw), np.uint8), np.empty((nh, nw), np.uint8)
    rows, mid = max(1, 1000 // cell), (cell * cell) // 2
    tree = cKDTree(sts_xy)
    cx = origin[0] + (np.arange(nw) + 0.5) * cell
    for r in range(0, nh, rows):       # in strips, to keep memory flat
        r1 = min(r + rows, nh)
        b = np.asarray(cidx[r * cell:r1 * cell, :w]).reshape(-1, cell, nw, cell).transpose(0, 2, 1, 3).reshape(-1, nw, cell * cell)
        idx[r:r1] = np.partition(b, mid, axis=2)[:, :, mid]
        g = np.asarray(dem[r * cell:r1 * cell, :w], np.float32).reshape(-1, cell, nw, cell).transpose(0, 2, 1, 3).reshape(-1, nw, cell * cell)
        g = np.partition(np.where(np.isfinite(g), g, 9999.0), mid, axis=2)[:, :, mid]
        gnd[r:r1] = np.clip(np.round((g - GROUND0_FT) / GROUND_STEP_FT) + 1, 1, 255).astype(np.uint8)
        gx, gy = np.meshgrid(cx, origin[1] - (np.arange(r, r1) + 0.5) * cell)
        zone[r:r1] = tree.query(np.column_stack([gx.ravel(), gy.ravel()]))[1].reshape(r1 - r, nw)
    gnd[idx <= int(np.searchsorted(depth.LEVELS_FT, WATER_FT))] = 0
    if hucs and forced:
        import shapely
        from rasterio import features
        from rasterio.transform import from_origin
        from shapely.geometry import shape
        from . import tether
        tr = from_origin(origin[0], origin[1], cell, cell)
        where = {st: i for i, st in enumerate(ids or [])}
        for f in hucs:
            st = tether.rule(f["properties"]["huc12"], forced)
            if st is None or (st and st not in where):
                continue
            geom = shapely.transform(shapely.force_2d(shape(f["geometry"])), lambda c: np.column_stack(fwd(c[:, 0], c[:, 1])))
            m = features.rasterize([(geom, 1)], out_shape=(nh, nw), transform=tr, fill=0, dtype="uint8").astype(bool)
            zone[m] = where[st] if st else NO_ZONE
    return idx, zone, gnd


def write_raster(out, idx, zone, gnd, cell=CELL_M):
    """tiles/z0.png (the whole area, thinned so that it fits OVERVIEW_MAX) and tiles/t_<row>_<col>.png at full
    detail. Returns static.json's raster block."""
    from PIL import Image
    d = os.path.join(out, "tiles")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    nh, nw = idx.shape
    f = 1
    while max(nh, nw) / f > OVERVIEW_MAX:
        f *= 2
    if f > 1:                          # a block is wet once half of it is, as in the 5 m cells themselves
        oh, ow = -(-nh // f), -(-nw // f)

        def blocks(a, fill):
            p = np.full((oh * f, ow * f), fill, np.uint8)
            p[:nh, :nw] = a
            return p.reshape(oh, f, ow, f).transpose(0, 2, 1, 3).reshape(oh, ow, f * f)
        bi = blocks(idx, 255)
        k = np.argpartition(bi, (f * f) // 2, axis=2)[:, :, (f * f) // 2][..., None]
        over = np.dstack([np.take_along_axis(blocks(a, fill), k, axis=2)[..., 0] for a, fill in ((idx, 255), (zone, NO_ZONE), (gnd, 255))])
    else:
        over = np.dstack([idx, zone, gnd])
    Image.fromarray(over, "RGB").save(os.path.join(d, "z0.png"), optimize=True)
    rows, cols = -(-nh // TILE), -(-nw // TILE)
    for r in range(rows):
        for c in range(cols):
            sl = (slice(r * TILE, min((r + 1) * TILE, nh)), slice(c * TILE, min((c + 1) * TILE, nw)))
            Image.fromarray(np.dstack([idx[sl], zone[sl], gnd[sl]]), "RGB").save(os.path.join(d, f"t_{r}_{c}.png"), optimize=True)
    return {"v": 2, "cell_m": cell, "width": int(nw), "height": int(nh), "tile": TILE, "rows": rows, "cols": cols,
            "dir": "tiles", "overview": {"file": "tiles/z0.png", "factor": f, "width": int(over.shape[1]), "height": int(over.shape[0])},
            "ground0_ft": GROUND0_FT, "ground_step_ft": GROUND_STEP_FT, "no_zone": NO_ZONE}


NOAA_NAMES = {"8638610": "Sewells Point", "8639348": "Money Point", "8638901": "Chesapeake Bay Bridge Tunnel", "8637689": "Yorktown",
              "8632200": "Kiptopeke", "8636580": "Windmill Point", "8635750": "Lewisetta", "8631044": "Wachapreague",
              "8635027": "Dahlgren", "8594900": "Washington, DC", "8571421": "Bishops Head", "8577330": "Solomons Island"}


def plot_gauge(lat, lon, path=None, inside=()):
    """The NOAA tide gauge nearest a point, for the page's prediction plot, and Sewells Point's datum for the
    readings in feet above MLLW: ({id, name, noaa_id, lat, lon, navd88_above_mllw_ft}, the same for SWPT).
    inside: the area's own gauges within its frame; a NOAA gauge among them goes before any outside (Virginia
    Beach's plot is the Bay Bridge Tunnel's, not Money Point's up the Elizabeth River)."""
    import csv
    from . import tether
    best, ref = None, None
    with open(path or tether.STATIONS, newline="") as fh:
        for r in csv.DictReader(fh):
            noaa = [p.split(":")[1] for p in r["obs"].split(";") if p.strip().startswith("coops:")]
            if not noaa or not r["lat"] or r.get("alias_of") or r.get("suspect_obs"):
                continue
            g = {"id": r["station"], "name": NOAA_NAMES.get(noaa[0], r["name"].replace(" (IN MLLW)", "")), "noaa_id": noaa[0],
                 "lat": float(r["lat"]), "lon": float(r["lon"]),
                 "navd88_above_mllw_ft": float(r["navd88_above_mllw_ft"]) if r.get("navd88_above_mllw_ft") else None}
            km = np.hypot((g["lon"] - lon) * np.cos(np.radians(lat)), g["lat"] - lat) * 111.2
            key = (r["station"] not in inside, km)
            if best is None or key < best[0]:
                best = (key, g)
            if r["station"] == "SWPT":
                ref = g
    return (best[1] if best else None), ref


def write_static(out, name, title, bounds, epsg, pieces, road_cols, roads_raw, fwd, bgeoms, bldg_cols, dem, cidx,
                 sts, sources, values=None, hucs=None, forced=None):
    """sts: [{station, name, lat, lon}] the gauges features are tethered to, in the order used for indices.
    values: {"tax_year", "firm_year", "zillow_month"} when the building table has the value.attach columns."""
    os.makedirs(out, exist_ok=True)
    x0, ytop = bounds[0], bounds[3]
    unit = unit_for(bounds)
    ux = lambda x: units(x, x0, unit)                       # noqa: E731
    uy = lambda y: units(-(np.asarray(y, float) - ytop), 0, unit)   # noqa: E731  (y runs down the page)
    order = {s["station"]: i for i, s in enumerate(sts)}
    sx, sy = fwd(np.array([s["lon"] for s in sts]), np.array([s["lat"] for s in sts]))

    # road pieces that can go under by the top level and are not on a bridge
    rc = road_cols
    keep = np.flatnonzero((np.asarray(rc["bridge"]) == 0) & np.isfinite(rc["connect_ft"]) & (np.asarray(rc["station"], object) != ""))
    pxy = piece_xy(pieces, keep, ux, uy)
    p = {"n": int(len(keep)), "xy": b64(pxy, "<u2"), "ground": b64(hundredths(rc["ground_ft"][keep]), "<i2"),
         "connect": b64(hundredths(rc["connect_ft"][keep]), "<i2"),
         "station": b64([order[s] for s in rc["station"][keep]], "u1"),
         "road": b64(np.asarray(pieces["road"])[keep], "<u2" if len(roads_raw) < 65536 else "<u4"),
         "road_bytes": 2 if len(roads_raw) < 65536 else 4,
         "len": b64(np.round(np.asarray(pieces["length_m"])[keep] * 100), "<u2"), "id": b64(keep, "<u4")}
    if "via_pipes" in rc:
        p["via"] = b64(np.asarray(rc["via_pipes"])[keep], "u1")

    # the whole road network, thinned, as the map's background
    road_of_piece = np.asarray(pieces["road"])
    on_bridge = np.bincount(road_of_piece, weights=(np.asarray(rc["bridge"]) > 0), minlength=len(roads_raw))
    n_piece = np.maximum(np.bincount(road_of_piece, minlength=len(roads_raw)), 1)
    counts, coords, of = road_lines(roads_raw, fwd, ux, uy)
    cls = [road_class(roads_raw[i]["properties"].get("MTFCC")) + (4 if on_bridge[i] / n_piece[i] > 0.5 else 0) for i in of]
    names = [str(f["properties"].get("ST_FULL") or "").strip() for f in roads_raw]
    uniq = sorted(set(names))
    where = {n: i for i, n in enumerate(uniq)}
    r = {"n": len(counts), "count": b64(counts, "<u2"), "xy": b64(np.vstack(coords), "<u2"), "cls": b64(cls, "u1"),
         "names": uniq, "name_of": b64([where[n] for n in names], "<u2")}

    # buildings that can go under and do not stand in the water
    bc = bldg_cols
    bkeep = [i for i in np.flatnonzero(np.isfinite(bc["connect_ft"]) & (np.asarray(bc["over_water"]) == 0) & (np.asarray(bc["station"], object) != ""))
             if bgeoms[i] is not None and not bgeoms[i].is_empty]
    bcount, bxy = outlines(bgeoms, bkeep, ux, uy)
    b = {"n": len(bkeep), "count": b64(bcount, "u1"), "xy": b64(bxy, "<u2"),
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
                 ffh=b64(np.clip(np.round(10 * np.nan_to_num(np.asarray(bc["first_floor_ft"], float)[bkeep], nan=3)), 0, 255), "u1"),   # tenths of a foot
                 ec=b64(np.asarray([v == "certificate" for v in np.asarray(bc.get("floor_source", np.full(len(bc["first_floor_ft"]), "")), object)[bkeep]], int), "u1"),
                 zip=b64([zi.get(z, 255) for z in np.asarray(bc["zip"])[bkeep]], "u1"),
                 use=b64([ui.get(u, 255) for u in use], "u1"))
        vdoc = {**values, "zips": zips, "zhvi": [zh.get(z) for z in zips], "uses": uses,
                "damage": {"depth0": float(damage.DEPTH_FT[0]), "curves": [damage.STRUCTURE_PCT[k] for k in names], "citation": damage.CITATION}}

    rdoc = write_raster(out, *raster(dem, cidx, (x0, ytop), np.column_stack([sx, sy]), hucs=hucs, fwd=fwd, forced=forced,
                                     ids=[s["station"] for s in sts]))
    plot, ref = plot_gauge(*centre(bounds, epsg), inside=[s["station"] for i, s in enumerate(sts)
                                                          if bounds[0] <= sx[i] <= bounds[2] and bounds[1] <= sy[i] <= bounds[3]])

    ok_road = (np.asarray(rc["bridge"]) == 0) & (np.asarray(rc["station"]).astype(str) != "")      # no gauge: left out
    ok_bldg = (np.asarray(bc["over_water"]) == 0) & (np.asarray(bc["station"]).astype(str) != "")
    static = {
        "name": name, "title": title, "epsg": epsg, "x0": x0, "ytop": ytop, "unit_m": unit,
        "width_m": bounds[2] - bounds[0], "height_m": bounds[3] - bounds[1],
        "levels": {"start": float(depth.LEVELS_FT[0]), "step": 0.25, "n": int(len(depth.LEVELS_FT))},
        "ponded_ft": PONDED_FT, "flooded_ft": FLOODED_FT, "no_level": NO_LEVEL,
        "stations": [{"id": s["station"], "name": s["name"], "x": int(ux([sx[i]])[0]), "y": int(uy([sy[i]])[0]),
                      "inside": bool(bounds[0] <= sx[i] <= bounds[2] and bounds[1] <= sy[i] <= bounds[3])} for i, s in enumerate(sts)],
        "pieces": p, "roads": r, "buildings": b, "values": vdoc,
        "raster": rdoc, "plot_gauge": plot, "mllw_ref": ref,
        "totals": {"road_miles": round(float(np.asarray(rc["length_m"])[ok_road].sum() / 1609.344), 1),
                   "road_pieces": int(ok_road.sum()), "buildings": int(ok_bldg.sum())},
        "prepared_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "sources": sources, "disclaimer": DISCLAIMER,
    }
    with open(os.path.join(out, "static.json"), "w") as fh:
        json.dump(static, fh, separators=(",", ":"))
    for logo in ("stormsense_logo.png", "ccrm_logo.png"):
        shutil.copy(os.path.join(HERE, "..", "crest", "branding", logo), os.path.join(out, logo))
    write_index(out, title)
    return static


def piece_xy(pieces, keep, ux, uy):
    return np.column_stack([ux(pieces["x0"][keep]), uy(pieces["y0"][keep]), ux(pieces["x1"][keep]), uy(pieces["y1"][keep])])


def road_lines(roads_raw, fwd, ux, uy):
    """The whole road network, thinned: vertex counts, coordinates and the feature each line belongs to."""
    counts, coords, of = [], [], []
    for i, f in enumerate(roads_raw):
        g = f.get("geometry") or {}
        parts = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
        for part in parts:
            c = np.asarray(part, float)[:, :2]
            if len(c) < 2:
                continue
            x, y = fwd(c[:, 0], c[:, 1])
            s = simplify(np.column_stack([x, y]), 1.0)
            counts.append(len(s)); coords.append(np.column_stack([ux(s[:, 0]), uy(s[:, 1])])); of.append(i)
    return counts, coords, of


def outlines(bgeoms, bkeep, ux, uy):
    count, xy = [], []
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
        count.append(len(s)); xy.append(np.column_stack([ux(s[:, 0]), uy(s[:, 1])]))
    return count, (np.vstack(xy) if xy else np.zeros((0, 2)))      # an inland town may have no building 10 ft of water reaches


def reshape(page_dir, work):
    """Write a prepared page's coordinates again (road pieces, road lines, building outlines, gauges) in the unit
    its frame needs, from prep's saved downloads. Levels, values and the tiles are left as they are."""
    import shapely
    from pyproj import Transformer
    from shapely.geometry import shape
    from . import segments, tether
    with open(os.path.join(page_dir, "static.json")) as fh:
        st = json.load(fh)
    bounds = (st["x0"], st["ytop"] - st["height_m"], st["x0"] + st["width_m"], st["ytop"])
    unit = unit_for(bounds)
    ux = lambda x: units(x, st["x0"], unit)                             # noqa: E731
    uy = lambda y: units(-(np.asarray(y, float) - st["ytop"]), 0, unit)   # noqa: E731
    fwd = Transformer.from_crs(4326, st["epsg"], always_xy=True).transform

    def arr(b, dtype):
        return np.frombuffer(base64.b64decode(b), dtype)
    with open(os.path.join(work, "roads_raw.geojson")) as fh:
        roads_raw = json.load(fh)["features"]
    pieces = segments.split(roads_raw, fwd)
    p, r, b = st["pieces"], st["roads"], st["buildings"]
    p["xy"] = b64(piece_xy(pieces, arr(p["id"], "<u4"), ux, uy), "<u2")
    counts, coords, _ = road_lines(roads_raw, fwd, ux, uy)
    if len(counts) != r["n"]:
        raise SystemExit(f"{st['name']}: the saved roads no longer match the page ({len(counts)} lines, the page has {r['n']})")
    r["count"], r["xy"] = b64(counts, "<u2"), b64(np.vstack(coords), "<u2")
    with open(os.path.join(work, "buildings_raw.geojson")) as fh:
        raw = json.load(fh)["features"]
    bkeep = arr(b["id"], "<u4")
    geoms = {int(i): shapely.transform(shapely.force_2d(shape(raw[i]["geometry"])), lambda c: np.column_stack(fwd(c[:, 0], c[:, 1])))
             for i in bkeep}
    count, xy = outlines(geoms, [int(i) for i in bkeep], ux, uy)
    b["count"], b["xy"] = b64(count, "u1"), b64(xy, "<u2")
    known = {s["station"]: s for s in tether.stations()}
    for g in st["stations"]:
        x, y = fwd(known[g["id"]]["lon"], known[g["id"]]["lat"])
        g["x"], g["y"] = int(ux([x])[0]), int(uy([y])[0])
    st["unit_m"] = unit
    with open(os.path.join(page_dir, "static.json"), "w") as fh:
        json.dump(st, fh, separators=(",", ":"))
    write_index(page_dir, st["title"])
    return unit


def centre(bounds, epsg):
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(epsg, 4326, always_xy=True).transform((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    return lat, lon


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


def main(argv=None):
    """Redraw the raster tiles and the page of a prepared area from the grids in prep's work folder."""
    import argparse
    from pyproj import Transformer
    from . import tether
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--prepared", required=True, help="the area's page folder (with static.json)")
    ap.add_argument("--work", required=True, help="prep's work folder (dem_ft.npy, cidx.npy, cidx_piped.npy, huc12.geojson)")
    ap.add_argument("--title")
    ap.add_argument("--shapes", action="store_true", help="only write the page's coordinates again (page.reshape); needs no grids")
    a = ap.parse_args(argv)
    if a.shapes:
        print(f"{a.prepared}: coordinates written in units of {reshape(a.prepared, a.work)} m")
        return
    with open(os.path.join(a.prepared, "static.json")) as fh:
        st = json.load(fh)
    bounds = (st["x0"], st["ytop"] - st["height_m"], st["x0"] + st["width_m"], st["ytop"])
    fwd = Transformer.from_crs(4326, st["epsg"], always_xy=True).transform
    known = {s["station"]: s for s in tether.stations()}
    ids = [g["id"] for g in st["stations"]]
    sx, sy = fwd(np.array([known[i]["lon"] for i in ids]), np.array([known[i]["lat"] for i in ids]))
    dem = np.load(os.path.join(a.work, "dem_ft.npy"), mmap_mode="r")
    piped = os.path.join(a.work, "cidx_piped.npy")
    cidx = np.load(piped if os.path.exists(piped) else os.path.join(a.work, "cidx.npy"), mmap_mode="r")
    hucs = None
    if os.path.exists(os.path.join(a.work, "huc12.geojson")):
        with open(os.path.join(a.work, "huc12.geojson")) as fh:
            hucs = json.load(fh)["features"]
    st["raster"] = write_raster(a.prepared, *raster(dem, cidx, (st["x0"], st["ytop"]), np.column_stack([sx, sy]), hucs=hucs, fwd=fwd,
                                                    forced=tether.overrides(), ids=ids))
    st["plot_gauge"], st["mllw_ref"] = plot_gauge(*centre(bounds, st["epsg"]), inside=[g["id"] for g in st["stations"] if g.get("inside")])
    with open(os.path.join(a.prepared, "static.json"), "w") as fh:
        json.dump(st, fh, separators=(",", ":"))
    if os.path.exists(os.path.join(a.prepared, "extent.png")):
        os.remove(os.path.join(a.prepared, "extent.png"))
    write_index(a.prepared, a.title or st["title"])
    r = st["raster"]
    print(f"{st['name']}: {r['width']} x {r['height']} cells in {r['rows'] * r['cols']} tiles, overview 1/{r['overview']['factor']}; "
          f"plot gauge {st['plot_gauge']['id']} ({st['plot_gauge']['name']})")


if __name__ == "__main__":
    main()
