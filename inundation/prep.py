"""One-time preparation of an area: every 5 m road piece and every building gets its ground level, the
water level at which water first reaches it, its HUC-12 and its gauge.

    python -m inundation.prep --fips 51710 --name norfolk --out prepared/ --work work/ [--dem a.tif b.tif]

Writes <out>/<name>_roads.csv.gz, <name>_buildings.csv.gz (the tables every forecast run reads),
<name>.gpkg (the same with geometry, layers `road_pieces` and `buildings`, for ArcGIS; --gdb adds a
file geodatabase), page/ (the map page, see page.py), and
<name>_tether.csv (which gauge covers how much of each HUC-12; overrides go in tether_overrides.csv).

Road pieces: ground and connect level are the middle value of five points along the piece, so a piece
counts as under d ft of water once most of its length is. The bare-earth elevation model has no bridge
decks, so pieces on bridges (`bridge` > 0, see bridges()) are kept in the table but never flagged. Buildings: ground is the middle value over the footprint (`ground_ft`), `low_ft` its
low side (10th percentile); a building counts as flooded once the water stands 0.5 ft over `ground_ft`
(`wet_low_ft` is the level for 0.5 ft at the low side instead). Footprints standing in the water
(`over_water`: piers, boathouses) are never flagged.
"""
import argparse
import gzip
import json
import os
import time

import numpy as np

from . import FLOODED_FT, PIECE_M, PONDED_FT, depth, page, segments, tether, vgin

OVER_WATER_FT = 1.0   # ground below this (ft NAVD88; MHHW at Sewells Point is 1.15) cannot be a road surface
APPROACH_M = 60       # pieces of the same street this close to an over-water piece are on the bridge too
RCL_BRIDGE = 2        # VGIN RCL SEG_TYPE of bridges and overpasses
BUFFER_M = 500


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def cached(path, make):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    d = make()
    with open(path, "w") as fh:
        json.dump(d, fh)
    return d


def utm_epsg(lon):
    return 26900 + int((lon + 180) // 6) + 1      # NAD83 / UTM zone, the CRS of the CoNED tiles


def bounds_of(feature, transform, buffer=BUFFER_M):
    g = feature["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    xs, ys = [], []
    for poly in polys:
        for ring in poly:
            a = np.asarray(ring, float)
            x, y = transform(a[:, 0], a[:, 1])
            xs.append(x); ys.append(y)
    xs, ys = np.concatenate(xs), np.concatenate(ys)
    return (int(np.floor(xs.min() - buffer)), int(np.floor(ys.min() - buffer)),
            int(np.ceil(xs.max() + buffer)), int(np.ceil(ys.max() + buffer)))


def middle(a):
    """Row-wise median treating NaN as 'never' (+inf): NaN when most of the row never floods."""
    m = np.median(np.where(np.isfinite(a), a, np.inf), axis=1)
    return np.where(np.isfinite(m), m, np.nan)


def bridges(pieces, ground):
    """0 road on the ground; 1 the elevation model shows water or a cut under it (bridge deck, tunnel);
    2 a bridge or overpass in VGIN's own coding; 3 the same street within APPROACH_M of a 1 (the spans
    over the banks, where the bare-earth model shows the shore under the deck)."""
    from scipy.spatial import cKDTree
    code = np.zeros(len(ground), np.int8)
    seg = np.array([int(t) if t is not None else -1 for t in pieces["SEG_TYPE"]])
    code[seg == RCL_BRIDGE] = 2
    over = (ground < OVER_WATER_FT) | ~np.isfinite(ground)
    code[over] = 1
    if over.any():
        xy = np.column_stack([pieces["xm"], pieces["ym"]])
        street = np.array(["" if t is None else str(t) for t in pieces["ST_FULL"]], dtype=object)
        rid = np.asarray(pieces["road"])
        oi = np.flatnonzero(over)
        tree = cKDTree(xy[oi])
        cand = np.flatnonzero(~over)
        for i, near in zip(cand, tree.query_ball_point(xy[cand], APPROACH_M)):
            if near and any(rid[oi[j]] == rid[i] or (street[i] and street[oi[j]] == street[i]) for j in near):
                code[i] = 3
    return code


def road_table(pieces, dem, cidx, origin):
    g, c = depth.sample(dem, cidx, origin, pieces["sx"], pieces["sy"])
    ground, connect = middle(g), middle(c)
    return {
        "ground_ft": ground, "connect_ft": connect,
        "ponded_ft": depth.first_level(ground, connect, PONDED_FT),
        "flooded_ft": depth.first_level(ground, connect, FLOODED_FT),
        "bridge": bridges(pieces, ground),
    }


def building_table(features, transform, dem, cidx, origin, levels=depth.LEVELS_FT):
    """Ground (median and low side) and connect level over each footprint, from the cells it touches."""
    from rasterio import features as rfeat
    from rasterio.transform import from_origin
    from shapely.geometry import shape
    import shapely
    n = len(features)
    geoms = []
    for f in features:
        try:
            geoms.append(shapely.transform(shapely.force_2d(shape(f["geometry"])),
                                           lambda c: np.column_stack(transform(c[:, 0], c[:, 1]))))
        except Exception:
            geoms.append(None)
    ids = rfeat.rasterize(((g, i + 1) for i, g in enumerate(geoms) if g is not None and not g.is_empty),
                          out_shape=dem.shape, transform=from_origin(origin[0], origin[1], 1, 1),
                          fill=0, all_touched=True, dtype="int32")
    cell = np.flatnonzero(ids)
    b = ids.ravel()[cell] - 1
    del ids
    z = dem.ravel()[cell]
    ci = cidx.ravel()[cell]
    c = np.where(ci == depth.NEVER, np.inf, levels[np.minimum(ci, len(levels) - 1)])
    ok = np.isfinite(z)
    b, z, c = b[ok], z[ok], c[ok]
    count = np.bincount(b, minlength=n)
    start = np.concatenate([[0], np.cumsum(count)[:-1]])
    has = count > 0

    def pick(values, q):
        o = np.lexsort((values, b))
        v = values[o]
        out = np.full(n, np.nan)
        out[has] = v[(start + np.floor(q * (count - 1) + 0.5).astype(int))[has]]
        return out

    ground, low, connect = pick(z, 0.5), pick(z, 0.1), pick(c, 0.5)
    connect[~np.isfinite(connect)] = np.nan
    # footprints that touched no cell (off the grid): read the centroid
    miss = np.flatnonzero(~has)
    if len(miss):
        cx = np.array([geoms[i].centroid.x if geoms[i] is not None and not geoms[i].is_empty else np.nan for i in miss])
        cy = np.array([geoms[i].centroid.y if geoms[i] is not None and not geoms[i].is_empty else np.nan for i in miss])
        okc = np.isfinite(cx)
        g1, c1 = depth.sample(dem, cidx, origin, cx[okc], cy[okc])
        ground[miss[okc]], low[miss[okc]], connect[miss[okc]] = g1, g1, c1
    cen = np.array([[g.centroid.x, g.centroid.y] if g is not None and not g.is_empty else [np.nan, np.nan] for g in geoms])
    area = np.array([g.area if g is not None else np.nan for g in geoms])
    return {
        "x": cen[:, 0], "y": cen[:, 1], "area_m2": area, "cells": count,
        "ground_ft": ground, "low_ft": low, "connect_ft": connect,
        "ponded_ft": depth.first_level(ground, connect, PONDED_FT),
        "flooded_ft": depth.first_level(ground, connect, FLOODED_FT),
        "wet_low_ft": depth.first_level(low, connect, FLOODED_FT),
        "over_water": (ground < OVER_WATER_FT).astype(int),     # piers, boathouses: never flagged
    }, geoms


def write_csv(path, cols, fmt):
    names = list(cols)
    n = len(cols[names[0]])
    with gzip.open(path, "wt", newline="") as fh:
        fh.write(",".join(names) + "\n")
        rows = []
        for k in names:
            v, f = cols[k], fmt.get(k)
            if f:
                rows.append(["" if not np.isfinite(x) else f % x for x in np.asarray(v, float)])
            else:
                rows.append(["" if x is None else str(x).replace(",", " ") for x in v])
        for i in range(n):
            fh.write(",".join(r[i] for r in rows) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fips", required=True, help="five-digit locality FIPS, e.g. 51710 for Norfolk")
    ap.add_argument("--name", required=True, help="short name used in file names, e.g. norfolk")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True, help="where downloads and the elevation grid are kept")
    ap.add_argument("--dem", nargs="*", help="local CoNED tiles; default reads them from NOAA's bucket")
    ap.add_argument("--no-gpkg", action="store_true")
    ap.add_argument("--gdb", action="store_true", help="also write a file geodatabase (<name>.gdb) for ArcGIS Pro")
    ap.add_argument("--no-page", action="store_true")
    ap.add_argument("--title", help="the area's name as the page shows it; default the locality's name")
    a = ap.parse_args(argv)
    from pyproj import Transformer
    os.makedirs(a.out, exist_ok=True); os.makedirs(a.work, exist_ok=True)

    loc = cached(f"{a.work}/locality.geojson", lambda: vgin.locality(a.fips))
    ring = np.asarray((loc["geometry"]["coordinates"][0] if loc["geometry"]["type"] == "Polygon"
                       else loc["geometry"]["coordinates"][0][0]), float)
    epsg = utm_epsg(ring[:, 0].mean())
    fwd = Transformer.from_crs(4326, epsg, always_xy=True).transform
    inv = Transformer.from_crs(epsg, 4326, always_xy=True).transform
    bounds = bounds_of(loc, fwd)
    log(loc["properties"].get("NAMELSAD"), "EPSG", epsg, "bounds", bounds)

    roads = cached(f"{a.work}/roads_raw.geojson", lambda: {"type": "FeatureCollection", "features": vgin.roads(a.fips)})["features"]
    bldgs = cached(f"{a.work}/buildings_raw.geojson", lambda: {"type": "FeatureCollection", "features": vgin.buildings(a.fips)})["features"]
    log(len(roads), "road segments,", len(bldgs), "buildings (VGIN)")
    pieces = segments.split(roads, fwd)
    log(len(pieces["road"]), f"pieces of at most {PIECE_M:g} m,", round(pieces["length_m"].sum() / 1000, 1), "km")

    dem_path, cidx_path = f"{a.work}/dem_ft.npy", f"{a.work}/cidx.npy"
    origin = (bounds[0], bounds[3])
    if os.path.exists(dem_path) and os.path.exists(cidx_path):
        dem, cidx = np.load(dem_path, mmap_mode="r"), np.load(cidx_path, mmap_mode="r")
    else:
        tiles = a.dem or ["/vsicurl/" + u for u in depth.coned_tiles(bounds)]
        log("elevation tiles:", ", ".join(os.path.basename(t) for t in tiles))
        dem, _ = depth.read_dem(tiles, bounds)
        np.save(dem_path, dem)
        cidx = depth.connect_index(dem, log=log)
        np.save(cidx_path, cidx)
    if dem.shape != (bounds[3] - bounds[1], bounds[2] - bounds[0]):
        raise SystemExit(f"{dem_path} does not match the area's bounds; delete it and run again")

    rt = road_table(pieces, dem, cidx, origin)
    bt, bgeoms = building_table(bldgs, fwd, dem, cidx, origin)
    log("ground and connect levels read")

    w, s = inv(bounds[0], bounds[1]); e, n = inv(bounds[2], bounds[3])
    hucs = cached(f"{a.work}/huc12.geojson", lambda: {"type": "FeatureCollection", "features": tether.huc12((w, s, e, n))})["features"]
    names = {f["properties"]["huc12"]: f["properties"]["name"].strip() for f in hucs}
    sts = tether.stations()
    forced = tether.overrides()
    rt["huc12"] = tether.in_polygons(hucs, fwd, pieces["xm"], pieces["ym"])
    bt["huc12"] = tether.in_polygons(hucs, fwd, bt["x"], bt["y"])
    rt["station"], rt["station_km"] = tether.nearest(pieces["xm"], pieces["ym"], sts, fwd, rt["huc12"], forced)
    okb = np.isfinite(bt["x"])
    bt["station"] = np.full(len(bldgs), "", dtype=object); bt["station_km"] = np.full(len(bldgs), np.nan)
    bt["station"][okb], bt["station_km"][okb] = tether.nearest(bt["x"][okb], bt["y"][okb], sts, fwd, bt["huc12"][okb], forced)

    lon, lat = inv(pieces["xm"], pieces["ym"])
    road_cols = {
        "piece": np.arange(len(lon)), "rcl_oid": pieces["OBJECTID"], "rcl_id": pieces["RCL_ID"], "seq": pieces["seq"],
        "street": pieces["ST_FULL"], "mtfcc": pieces["MTFCC"], "seg_type": pieces["SEG_TYPE"],
        "length_m": pieces["length_m"], "lon": lon, "lat": lat, **rt}
    blon, blat = inv(np.where(okb, bt["x"], 0), np.where(okb, bt["y"], 0))
    bldg_cols = {
        "building": np.arange(len(bldgs)), "vgin_oid": [f["properties"].get("OBJECTID") for f in bldgs],
        "source_id": [f["properties"].get("SOURCEFEATUREID") for f in bldgs],
        "lon": np.where(okb, blon, np.nan), "lat": np.where(okb, blat, np.nan),
        **{k: v for k, v in bt.items() if k not in ("x", "y")}}
    f2, f6 = "%.2f", "%.6f"
    fmt = {"ground_ft": f2, "low_ft": f2, "connect_ft": f2, "ponded_ft": f2, "flooded_ft": f2, "wet_low_ft": f2,
           "length_m": f2, "lon": f6, "lat": f6, "station_km": f2, "area_m2": "%.0f"}
    write_csv(f"{a.out}/{a.name}_roads.csv.gz", road_cols, fmt)
    write_csv(f"{a.out}/{a.name}_buildings.csv.gz", bldg_cols, fmt)

    # the tether table: what each gauge covers in each HUC-12
    rows = {}
    for kind, h, st, km in (("road_pieces", rt["huc12"], rt["station"], rt["station_km"]),
                            ("buildings", bt["huc12"][okb], bt["station"][okb], bt["station_km"][okb])):
        for hh, ss, kk in zip(h, st, km):
            r = rows.setdefault((hh, ss), {"road_pieces": 0, "buildings": 0, "max_km": 0.0})
            r[kind] += 1
            r["max_km"] = max(r["max_km"], kk)
    stn = {s["station"]: s["name"] for s in sts}
    with open(f"{a.out}/{a.name}_tether.csv", "w") as fh:
        fh.write("huc12,huc12_name,station,station_name,road_pieces,buildings,farthest_km,forced\n")
        for (hh, ss), r in sorted(rows.items()):
            fh.write(f"{hh},{names.get(hh, '')},{ss},\"{stn.get(ss, '')}\",{r['road_pieces']},{r['buildings']},"
                     f"{r['max_km']:.1f},{'yes' if forced.get(hh) == ss else ''}\n")

    if not a.no_gpkg:
        write_layers(f"{a.out}/{a.name}.gpkg", pieces, road_cols, bgeoms, bldg_cols, epsg)
    if a.gdb:
        write_layers(f"{a.out}/{a.name}.gdb", pieces, road_cols, bgeoms, bldg_cols, epsg)
    sources = {"depth": "NOAA/USGS CoNED Chesapeake Bay topobathy, 1 m (2016)", "roads": "VGIN road centerlines (VBMP RCL)",
               "buildings": "VGIN building footprints"}
    if not a.no_page:
        used = sorted(set(rt["station"]) | set(bt["station"][okb]))
        page.write_static(f"{a.out}/page", a.name, a.title or loc["properties"].get("NAME") or a.name, bounds, epsg, pieces,
                          road_cols, roads, fwd, bgeoms, bldg_cols, dem, cidx, [s for s in sts if s["station"] in used], sources)
    with open(f"{a.out}/{a.name}_prep.json", "w") as fh:
        json.dump({"fips": a.fips, "name": a.name, "locality": loc["properties"].get("NAMELSAD"), "epsg": epsg,
                   "bounds": bounds, "levels_ft": [float(depth.LEVELS_FT[0]), float(depth.LEVELS_FT[-1]), 0.25],
                   "depth_source": "NOAA/USGS CoNED Chesapeake Bay topobathy 1 m (2016), water joined to tidal water",
                   "roads": "VGIN VBMP_RCL", "buildings": "VGIN VA_Building_Footprints",
                   "road_segments": len(roads), "road_pieces": int(len(lon)), "buildings_n": len(bldgs),
                   "piece_m": PIECE_M, "ponded_ft": PONDED_FT, "flooded_ft": FLOODED_FT,
                   "prepared_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}, fh, indent=1)
    log("wrote", a.out)


def write_layers(path, pieces, road_cols, bgeoms, bldg_cols, epsg):
    """Both layers with geometry, in the projected CRS, for ArcGIS Pro and hosted layers: a GeoPackage
    (.gpkg) or a file geodatabase (.gdb)."""
    import shutil
    import pyogrio
    import shapely
    driver = "OpenFileGDB" if path.endswith(".gdb") else "GPKG"
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)

    def fields(cols):
        names, arrays = [], []
        for k, v in cols.items():
            v = np.asarray(v)
            if v.dtype == object:
                v = np.array(["" if x is None else str(x) for x in v])
                if k in ("rcl_oid", "rcl_id", "seg_type", "vgin_oid"):
                    v = np.array([int(float(x)) if x not in ("", "None") else -1 for x in v], dtype=np.int32)
            elif v.dtype.kind in "iub":
                v = v.astype(np.int32)
            names.append(k); arrays.append(v)
        return names, arrays

    counts = np.array([len(l) for l in pieces["lines"]])
    lines = shapely.to_wkb(shapely.linestrings(np.vstack(pieces["lines"]), indices=np.repeat(np.arange(len(counts)), counts)))
    n, a = fields(road_cols)
    pyogrio.raw.write(path, lines, a, n, layer="road_pieces", driver=driver, geometry_type="LineString", crs=f"EPSG:{epsg}")
    keep = np.array([g is not None and not g.is_empty for g in bgeoms])
    polys = shapely.to_wkb(np.array([shapely.multipolygons([q for q in getattr(g, "geoms", [g]) if q.geom_type == "Polygon"])
                                     for g, k in zip(bgeoms, keep) if k], dtype=object))
    n, a = fields(bldg_cols)
    pyogrio.raw.write(path, polys, [x[keep] for x in a], n, layer="buildings", driver=driver,
                      geometry_type="MultiPolygon", crs=f"EPSG:{epsg}", append=True)


if __name__ == "__main__":
    main()
