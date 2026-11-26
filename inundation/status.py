"""What the gauges and the 72-hour forecast put under water, for a prepared area.

    python -m inundation.status --prepared prepared/ --name norfolk --site <StormSense Latest site dir> --out out/

Reads each tethered gauge's published fore72 (NAVD88) and its last observation, and for every road piece
and building compares the water level with the levels stored by inundation.prep:

    state 1 "ponded"   at least 0.25 ft of water (roads: yellow)
    state 2 "flooded"  more than 0.5 ft (roads: orange, buildings: red)

for now (the last observation), for the forecast's highest water, and through the 72 hours (first and
last time flooded, hours flooded). `chance` is the chance of reaching the flooded level within 72 hours,
read from the 10-90% band at the forecast's peak (a normal spread; approximate).

Writes <name>_roads_status.csv and <name>_buildings_status.csv (only features that are wet now, at the
forecast peak, or at the 90% level), <name>_summary.json and <name>_timeline.csv. With --layers (the
area's .gpkg or .gdb from prep) the two status tables are also written as GeoJSON with their geometry,
ready to publish as hosted layers; with --page, levels.json for the area's map page.
"""
import argparse
import csv
import gzip
import json
import math
import os
from datetime import datetime, timezone

import numpy as np

from . import FLOODED_FT, PONDED_FT

M_PER_MILE = 1609.344
SPREAD = 2.5631   # p90 - p10 of a normal distribution, in standard deviations


def read_table(path):
    with gzip.open(path, "rt", newline="") as fh:
        rows = list(csv.reader(fh))
    head, rows = rows[0], rows[1:]
    cols = {k: [r[i] for r in rows] for i, k in enumerate(head)}

    def num(k):
        return np.array([float(v) if v != "" else np.nan for v in cols[k]])
    for k in head:
        if k in ("ground_ft", "low_ft", "connect_ft", "ponded_ft", "flooded_ft", "wet_low_ft", "length_m",
                 "lon", "lat", "station_km", "area_m2", "bridge", "over_water", "connect_surface_ft", "via_pipes",
                 "year_built", "value_total", "value_impr", "value_bldg", "zhvi", "first_floor_ft", "residential"):
            cols[k] = num(k)
        else:
            cols[k] = np.array(cols[k], dtype=object)
    return cols


def parse_time(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def station_levels(site, st):
    """Forecast (times, median, p10, p90) in ft NAVD88 and the last observation, from the published site."""
    with open(os.path.join(site, "fore72", f"{st}_fore72_NAVD.json")) as fh:
        d = json.load(fh)
    if d.get("datum") != "NAVD88" or d.get("units") != "feet":
        raise ValueError(f"{st}: fore72 is {d.get('datum')} {d.get('units')}, expected NAVD88 feet")
    rows = [r for r in d["data"] if r.get("SSforecast_ft") is not None]
    out = {
        "station": st, "updated": d.get("updated"), "guidance": d.get("guidance"),
        "t": [parse_time(r["date_time_z"]) for r in rows],
        "f": np.array([r["SSforecast_ft"] for r in rows], float),
        "p10": np.array([r.get("SSforecast_p10_ft") if r.get("SSforecast_p10_ft") is not None else r["SSforecast_ft"] for r in rows], float),
        "p90": np.array([r.get("SSforecast_p90_ft") if r.get("SSforecast_p90_ft") is not None else r["SSforecast_ft"] for r in rows], float),
        "flood_levels_ft": d.get("flood_levels_ft") or {}, "obs_ft": None, "obs_time": None,
    }
    p = os.path.join(site, "7day", f"{st}_7day_NAVD.json")
    if os.path.exists(p):
        with open(p) as fh:
            o = json.load(fh)
        for r in reversed(o.get("data", [])):
            if r.get("water_level_ft") is not None:
                out["obs_ft"], out["obs_time"] = float(r["water_level_ft"]), parse_time(r["date_time_z"])
                break
    k = int(np.argmax(out["f"]))
    out.update(peak_ft=float(out["f"][k]), peak_time=out["t"][k], peak_p10_ft=float(out["p10"][k]), peak_p90_ft=float(out["p90"][k]))
    return out


def depth_at(level, ground, connect):
    """Water depth (ft) over features at a water level; 0 where the water has not reached them."""
    d = level - ground
    return np.where(np.isfinite(connect) & (level >= connect) & (d > 0), d, 0.0)


def state_of(depth):
    return np.where(depth > FLOODED_FT, 2, np.where(depth >= PONDED_FT, 1, 0)).astype(np.int8)


def normal_cdf(z):
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))


def evaluate(cols, levels, skip):
    """Per-feature results for one table. levels: {station: station_levels()}; skip: boolean mask of
    features never flagged (bridges, piers)."""
    n = len(cols["station"])
    ground, connect = cols["ground_ft"], cols["connect_ft"]
    res = {k: np.zeros(n) for k in ("depth_now_ft", "depth_peak_ft", "depth_p90_ft", "hours_flooded", "chance")}
    res.update(state_now=np.zeros(n, np.int8), state_peak=np.zeros(n, np.int8), state_p90=np.zeros(n, np.int8),
               first=np.full(n, None, dtype=object), last=np.full(n, None, dtype=object),
               level_now_ft=np.full(n, np.nan), level_peak_ft=np.full(n, np.nan), has_forecast=np.zeros(n, bool))
    for st, lv in levels.items():
        m = (cols["station"] == st) & ~skip
        if not m.any():
            continue
        idx = np.flatnonzero(m)
        g, c = ground[idx], connect[idx]
        res["has_forecast"][idx] = True
        if lv["obs_ft"] is not None:
            d = depth_at(lv["obs_ft"], g, c)
            res["depth_now_ft"][idx], res["state_now"][idx], res["level_now_ft"][idx] = d, state_of(d), lv["obs_ft"]
        d = depth_at(lv["peak_ft"], g, c)
        res["depth_peak_ft"][idx], res["state_peak"][idx], res["level_peak_ft"][idx] = d, state_of(d), lv["peak_ft"]
        d90 = depth_at(lv["peak_p90_ft"], g, c)
        res["depth_p90_ft"][idx], res["state_p90"][idx] = d90, state_of(d90)
        # level at which the feature is flooded (just over FLOODED_FT), and when the forecast is above it
        need = np.where(np.isfinite(c), np.maximum(g + FLOODED_FT, c), np.inf)
        f, t = lv["f"], lv["t"]
        step_h = (t[1] - t[0]).total_seconds() / 3600 if len(t) > 1 else 0.1
        hit = np.flatnonzero(need < lv["peak_ft"])
        if len(hit):
            above = f[None, :] > need[hit, None]
            first, last = above.argmax(axis=1), above.shape[1] - 1 - above[:, ::-1].argmax(axis=1)
            res["hours_flooded"][idx[hit]] = above.sum(axis=1) * step_h
            res["first"][idx[hit]] = [t[i] for i in first]
            res["last"][idx[hit]] = [t[i] for i in last]
        sd = max((lv["peak_p90_ft"] - lv["peak_p10_ft"]) / SPREAD, 0.02)
        res["chance"][idx] = np.where(np.isfinite(need), 1.0 - normal_cdf((need - lv["peak_ft"]) / sd), 0.0)
    return res


def timeline(roads, bldgs, levels, rskip, bskip, step_min=30):
    """Road miles ponded and flooded and buildings flooded at each step of the forecast, per station."""
    rows = []
    for st, lv in sorted(levels.items()):
        rm = (roads["station"] == st) & ~rskip
        bm = (bldgs["station"] == st) & ~bskip
        rg, rc, rl = roads["ground_ft"][rm], roads["connect_ft"][rm], roads["length_m"][rm]
        bg, bc = bldgs["ground_ft"][bm], bldgs["connect_ft"][bm]
        every = max(1, int(round(step_min * 60 / max((lv["t"][1] - lv["t"][0]).total_seconds(), 1)))) if len(lv["t"]) > 1 else 1
        for t, f in list(zip(lv["t"], lv["f"]))[::every]:
            s = state_of(depth_at(f, rg, rc))
            rows.append((t.strftime("%Y-%m-%dT%H:%MZ"), st, round(float(f), 2),
                         round(float(rl[s == 1].sum() / M_PER_MILE), 2), round(float(rl[s == 2].sum() / M_PER_MILE), 2),
                         int((state_of(depth_at(f, bg, bc)) == 2).sum())))
    return rows


def write_geojson(path, layers, layer, keep, names, rows):
    """The kept features with their geometry (WGS84). Row order in the layer is the table's order."""
    import pyogrio
    import shapely
    from pyproj import Transformer
    meta, _, geoms, _ = pyogrio.raw.read(layers, layer=layer, columns=[])
    fwd = Transformer.from_crs(meta["crs"], 4326, always_xy=True).transform
    g = shapely.transform(shapely.from_wkb(geoms[keep]), lambda c: np.column_stack(fwd(c[:, 0], c[:, 1])))
    g = shapely.set_precision(g, 1e-7)
    numeric = {n for n in names if n.endswith(("_ft", "_usd", "_pct")) or n in ("length_m", "area_m2", "hours_flooded", "chance", "lon", "lat")}

    def val(n, v):
        if n in numeric:
            return float(v) if v != "" else None
        if isinstance(v, (int, np.integer)) or (n in ("piece", "building", "vgin_oid", "year_built", "via_pipes") and str(v).lstrip("-").isdigit()):
            return int(v)
        return v
    with open(path, "w") as fh:
        fh.write('{"type":"FeatureCollection","features":[\n')
        for k, (geom, row) in enumerate(zip(shapely.to_geojson(g), rows)):
            props = {n: val(n, v) for n, v in zip(names, row)}
            fh.write(("," if k else "") + '{"type":"Feature","properties":' + json.dumps(props) + ',"geometry":' + geom + "}\n")
        fh.write("]}\n")


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%MZ") if t is not None else ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prepared", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--site", required=True, help="StormSense Latest site directory (fore72/, 7day/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--page", help="the area's page directory (from prep); levels.json is written there")
    ap.add_argument("--layers", help="the area's .gpkg or .gdb (from prep): also write the status tables as GeoJSON")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    prep = {}
    if os.path.exists(f"{a.prepared}/{a.name}_prep.json"):
        with open(f"{a.prepared}/{a.name}_prep.json") as fh:
            prep = json.load(fh)
    prep["values"] = prep.get("values") or {}
    roads = read_table(f"{a.prepared}/{a.name}_roads.csv.gz")
    bldgs = read_table(f"{a.prepared}/{a.name}_buildings.csv.gz")
    rskip, bskip = roads["bridge"] > 0, bldgs["over_water"] > 0
    levels, missing = {}, []
    for st in sorted((set(roads["station"]) | set(bldgs["station"])) - {""}):       # "": ground no gauge stands for
        try:
            levels[st] = station_levels(a.site, st)
        except Exception as e:
            missing.append(st)
            print(f"{st}: no forecast read ({e}); its features are left out")
    rres, bres = evaluate(roads, levels, rskip), evaluate(bldgs, levels, bskip)

    def write(kind, cols, res, id_col, extra, layer, more=None):
        more = more or {}
        keep = np.flatnonzero((res["state_now"] > 0) | (res["state_peak"] > 0) | (res["state_p90"] > 0))
        names = [id_col, *extra, "station", "ground_ft", "connect_ft", "level_now_ft", "depth_now_ft", "state_now",
                 "level_peak_ft", "depth_peak_ft", "state_peak", "depth_p90_ft", "state_p90", "first_flooded_utc",
                 "last_flooded_utc", "hours_flooded", "chance", "lon", "lat", *more]
        rows = [[cols[id_col][i], *[cols[k][i] for k in extra], cols["station"][i],
                 f"{cols['ground_ft'][i]:.2f}", "" if not np.isfinite(cols["connect_ft"][i]) else f"{cols['connect_ft'][i]:.2f}",
                 "" if not np.isfinite(res["level_now_ft"][i]) else f"{res['level_now_ft'][i]:.2f}",
                 f"{res['depth_now_ft'][i]:.2f}", int(res["state_now"][i]), f"{res['level_peak_ft'][i]:.2f}",
                 f"{res['depth_peak_ft'][i]:.2f}", int(res["state_peak"][i]), f"{res['depth_p90_ft'][i]:.2f}",
                 int(res["state_p90"][i]), iso(res["first"][i]), iso(res["last"][i]), f"{res['hours_flooded'][i]:.1f}",
                 f"{res['chance'][i]:.2f}", f"{cols['lon'][i]:.6f}", f"{cols['lat'][i]:.6f}", *[v[i] for v in more.values()]] for i in keep]
        with open(f"{a.out}/{a.name}_{kind}_status.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(names)
            w.writerows(rows)
        if a.layers:
            write_geojson(f"{a.out}/{a.name}_{kind}_status.geojson", a.layers, layer, keep, names, rows)
        return keep

    def whole(v, f="%.0f"):
        return ["" if not np.isfinite(x) else f % x for x in np.asarray(v, float)]

    rmore, bmore, dmg = {}, {"label": np.where(bres["state_peak"] == 2, "Flooded Building", "")}, None
    if "via_pipes" in roads:
        rmore["via_pipes"] = whole(roads["via_pipes"])
        bmore["via_pipes"] = whole(bldgs["via_pipes"])
    if "value_bldg" in bldgs:
        from . import damage
        firm = prep.get("values", {}).get("firm_year", 9999)
        home = (bldgs["residential"] > 0) & ~bskip
        val = np.where(home, bldgs["value_bldg"], np.nan)
        dmg = {}
        surveyed = bldgs["floor_source"] == "certificate" if "floor_source" in bldgs else np.zeros(len(val), bool)
        floor = np.where(surveyed, bldgs["first_floor_ft"], np.nan)      # from an elevation certificate; the rest is assumed
        for when in ("peak", "p90"):
            usd, frac, above, ffh = damage.estimate(bres[f"depth_{when}_ft"], val, bldgs["year_built"], bldgs["curve"], firm, first_floor=floor)
            dmg[when] = {"usd": usd, "frac": frac, "above": above, "ffh": ffh}
        slab = damage.estimate(bres["depth_peak_ft"], val, bldgs["year_built"], bldgs["curve"], firm, "slab", first_floor=floor)[0]
        year = prep["values"]["tax_year"]
        bmore.update({
            "use": bldgs["use"], "year_built": whole(bldgs["year_built"]), "tax_year": np.full(len(val), year, object),
            "assessed_total_usd": whole(bldgs["value_total"]), "assessed_building_usd": whole(bldgs["value_bldg"]),
            "zillow_zip_typical_usd": whole(bldgs["zhvi"]), "first_floor_height_ft": whole(np.where(home, dmg["peak"]["ffh"], np.nan), "%.1f"),
            "first_floor_source": np.where(home, np.where(surveyed, "elevation certificate", "assumed"), ""),
            "depth_above_floor_peak_ft": whole(np.where(home, dmg["peak"]["above"], np.nan), "%.2f"),
            "damage_peak_pct": whole(np.where(home, 100 * dmg["peak"]["frac"], np.nan), "%.1f"),
            "damage_peak_usd": whole(np.where(home, dmg["peak"]["usd"], np.nan)),
            "damage_p90_usd": whole(np.where(home, dmg["p90"]["usd"], np.nan)),
            "damage_peak_if_slab_usd": whole(np.where(home, slab, np.nan))})
        dmg["slab"] = slab
    rkeep = write("roads", roads, rres, "piece", ["street", "mtfcc", "length_m"], "road_pieces", rmore)
    bkeep = write("buildings", bldgs, bres, "building", ["vgin_oid", "area_m2"], "buildings", bmore)

    def total(st=None):
        rm = (~rskip) & rres["has_forecast"] & ((roads["station"] == st) if st else True)
        bm = (~bskip) & bres["has_forecast"] & ((bldgs["station"] == st) if st else True)
        out = {}
        for when in ("now", "peak", "p90"):
            s, b = rres[f"state_{when}"], bres[f"state_{when}"]
            out[when] = {"road_miles_flooded": round(float(roads["length_m"][rm & (s == 2)].sum() / M_PER_MILE), 2),
                         "road_miles_ponded": round(float(roads["length_m"][rm & (s == 1)].sum() / M_PER_MILE), 2),
                         "buildings_flooded": int((bm & (b == 2)).sum())}
            if "connect_surface_ft" in roads:        # flooded at this level only because the storm drains carry the water there
                def only_pipes(cols, res):
                    over = cols["connect_surface_ft"]
                    return ~(res[f"depth_{when}_ft"] + cols["ground_ft"] >= over)        # NaN: the surface never reaches it
                out[when]["road_miles_flooded_only_via_pipes"] = round(float(roads["length_m"][rm & (s == 2) & only_pipes(roads, rres)].sum() / M_PER_MILE), 2)
                out[when]["buildings_flooded_only_via_pipes"] = int((bm & (b == 2) & only_pipes(bldgs, bres)).sum())
            if dmg and when in dmg:
                out[when]["homes_damaged"] = int((bm & (dmg[when]["usd"] > 0)).sum())
                out[when]["home_damage_usd"] = int(round(float(dmg[when]["usd"][bm].sum()), -3))
                if when == "peak":
                    out[when]["home_damage_if_slab_usd"] = int(round(float(dmg["slab"][bm].sum()), -3))
        return out

    summary = {"area": a.name, "made_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
               "ponded_ft": PONDED_FT, "flooded_ft": FLOODED_FT, "stations_without_forecast": missing,
               "totals": total(), "stations": {}}
    if dmg:
        from . import damage
        summary["damage"] = {"curves": damage.CITATION, "value": f"{prep['values'].get('assessment', 'assessed')} building (improvement) value",
                             "first_floor": "Hazus default heights above ground for a crawlspace: 3 ft" + (
                                 f" for homes built up to the first flood insurance rate map ({prep['values']['firm_year']}), 4 ft after"
                                 if prep["values"].get("firm_year") else "") + "; 'if_slab' assumes 1 ft"
                             + (f"; {int((surveyed & home).sum()):,} homes use the surveyed first floor of their elevation certificate (HRPDC)"
                                if surveyed.any() else ""),
                             "homes_with_elevation_certificate": int((surveyed & home).sum()),
                             "scope": prep["values"].get("home_rule") or "single-family homes, duplexes to fourplexes; other buildings are not estimated"}
    for st, lv in levels.items():
        summary["stations"][st] = {
            "forecast_updated": lv["updated"], "guidance": lv["guidance"],
            "obs_ft": lv["obs_ft"], "obs_time": iso(lv["obs_time"]), "peak_ft": round(lv["peak_ft"], 2),
            "peak_time": iso(lv["peak_time"]), "peak_p10_ft": round(lv["peak_p10_ft"], 2),
            "peak_p90_ft": round(lv["peak_p90_ft"], 2), "flood_levels_ft": lv["flood_levels_ft"], **total(st)}
    with open(f"{a.out}/{a.name}_summary.json", "w") as fh:
        json.dump(summary, fh, indent=1)
    with open(f"{a.out}/{a.name}_timeline.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", "station", "forecast_ft_navd88", "road_miles_ponded", "road_miles_flooded", "buildings_flooded"])
        w.writerows(timeline(roads, bldgs, levels, rskip, bskip))
    if a.page:
        from . import page
        page.write_levels(a.page, levels, summary)
    print(json.dumps(summary["totals"]))
    print(f"{len(rkeep)} road pieces and {len(bkeep)} buildings written to {a.out}")
    return summary


if __name__ == "__main__":
    main()
