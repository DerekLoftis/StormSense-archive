"""The inundation site: every prepared area's map page and one map of them all.

    python -m inundation.site --prepared <folder of <name>/ folders> --latest <StormSense Latest site> --out site/inundation

<name>/ is what inundation.prep wrote for an area of inundation/areas.csv (its two tables, <name>_prep.json and
page/). For each one found, inundation.status reads the published forecast, and the area's page goes to
<out>/<name>/ with its tables under <out>/<name>/data/. <out>/index.html is the map of all areas (templates/
statewide.html): it draws every area's depth tiles over a satellite or street basemap, and counts roads and
buildings from `curves`, each gauge's totals at every level from -2 to 10 ft, so it never loads the areas' own
road and building tables. `.github/workflows/inundation.yml` runs this after every StormSense run.
"""
import argparse
import csv
import json
import os
import shutil

import numpy as np

from . import depth, page, status, tether

HERE = os.path.dirname(__file__)
AREAS = os.path.join(HERE, "areas.csv")
M_PER_MILE = 1609.344
OVERVIEW_SMALL = 512       # the statewide map's far view of an area, pixels on the longer side


def areas(path=AREAS):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def curves(roads, bldgs, prep):
    """{station: {"f": road miles flooded, "p": ponded, "b": buildings flooded, "d": damage to homes in dollars}},
    each a list over depth.LEVELS_FT: what that gauge's roads and buildings come to with the water at that level."""
    rskip, bskip = roads["bridge"] > 0, bldgs["over_water"] > 0
    est = None
    if "value_bldg" in bldgs:
        from . import damage
        home = (bldgs["residential"] > 0) & ~bskip
        val = np.where(home, bldgs["value_bldg"], np.nan)
        surveyed = bldgs["floor_source"] == "certificate" if "floor_source" in bldgs else np.zeros(len(val), bool)
        floor = np.where(surveyed, bldgs["first_floor_ft"], np.nan)
        firm = (prep.get("values") or {}).get("firm_year", 9999)

        def est(d, m):
            return float(damage.estimate(d, val[m], bldgs["year_built"][m], bldgs["curve"][m], firm, first_floor=floor[m])[0].sum())
    out = {}
    for st in sorted((set(roads["station"]) | set(bldgs["station"])) - {""}):
        rm, bm = (roads["station"] == st) & ~rskip, (bldgs["station"] == st) & ~bskip
        rg, rc, rl = roads["ground_ft"][rm], roads["connect_ft"][rm], roads["length_m"][rm]
        bg, bc = bldgs["ground_ft"][bm], bldgs["connect_ft"][bm]
        c = {"f": [], "p": [], "b": []}
        if est:
            c["d"] = []
        for lv in depth.LEVELS_FT:
            s = status.state_of(status.depth_at(float(lv), rg, rc))
            c["f"].append(round(float(rl[s == 2].sum() / M_PER_MILE), 3))
            c["p"].append(round(float(rl[s == 1].sum() / M_PER_MILE), 3))
            d = status.depth_at(float(lv), bg, bc)
            c["b"].append(int((status.state_of(d) == 2).sum()))
            if est:
                c["d"].append(int(round(est(d, bm), -2)))
        out[st] = c
    return out


def outline(prepared, name, st):
    """[[[lon, lat], ...], ...] the locality's rings (prep's <name>_outline.geojson), else the page's frame."""
    path = os.path.join(prepared, f"{name}_outline.geojson")
    if os.path.exists(path):
        with open(path) as fh:
            g = json.load(fh)
        g = g.get("geometry", g)
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        return [[[round(x, 5), round(y, 5)] for x, y in ring] for poly in polys for ring in poly[:1]]
    from pyproj import Transformer
    inv = Transformer.from_crs(st["epsg"], 4326, always_xy=True).transform
    x0, y1 = st["x0"], st["ytop"]
    x1, y0 = x0 + st["width_m"], y1 - st["height_m"]
    return [[[round(v, 5) for v in inv(x, y)] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]]


def small_overview(page_dir, raster):
    """tiles/z1.png: the overview thinned to OVERVIEW_SMALL pixels, for the far view of the map of all areas."""
    from PIL import Image
    ov = raster["overview"]
    k = int(np.ceil(max(ov["width"], ov["height"]) / OVERVIEW_SMALL))
    if k <= 1:
        return dict(ov)
    a = np.asarray(Image.open(os.path.join(page_dir, ov["file"])).convert("RGB"))[k // 2::k, k // 2::k]
    Image.fromarray(np.ascontiguousarray(a), "RGB").save(os.path.join(page_dir, raster["dir"], "z1.png"), optimize=True)
    return {"file": raster["dir"] + "/z1.png", "factor": ov["factor"] * k, "width": int(a.shape[1]), "height": int(a.shape[0])}


def build(prepared, latest, out, log=print):
    os.makedirs(out, exist_ok=True)
    doc, levels, summaries = [], {}, {}
    known = {s["station"]: s for s in tether.stations()}
    for a in areas():
        name, src = a["name"], os.path.join(prepared, a["name"])
        if not os.path.exists(os.path.join(src, f"{name}_prep.json")):
            continue
        dst = os.path.join(out, name)
        log(f"== {a['title']}")
        page.write_index(os.path.join(src, "page"), a["title"])
        summary = status.main(["--prepared", src, "--name", name, "--site", latest, "--out", os.path.join(dst, "data"),
                               "--page", os.path.join(src, "page")])
        with open(os.path.join(src, "page", "static.json")) as fh:
            st = json.load(fh)
        with open(os.path.join(src, f"{name}_prep.json")) as fh:
            prep = json.load(fh)
        st["raster"]["small"] = small_overview(os.path.join(src, "page"), st["raster"])
        for f in os.listdir(os.path.join(src, "page")):
            p = os.path.join(src, "page", f)
            if os.path.isdir(p):
                shutil.copytree(p, os.path.join(dst, f), dirs_exist_ok=True)
            elif f != "page.html":
                shutil.copy(p, dst)
        roads = status.read_table(os.path.join(src, f"{name}_roads.csv.gz"))
        bldgs = status.read_table(os.path.join(src, f"{name}_buildings.csv.gz"))
        rings = outline(src, name, st)
        xs, ys = [p[0] for r in rings for p in r], [p[1] for r in rings for p in r]
        doc.append({"name": name, "title": a["title"], "fips": a["fips"], "region": a["region"], "epsg": st["epsg"],
                    "x0": st["x0"], "ytop": st["ytop"], "raster": st["raster"], "stations": [g["id"] for g in st["stations"]],
                    "outline": rings, "bbox": [min(xs), min(ys), max(xs), max(ys)], "curves": curves(roads, bldgs, prep),
                    "values": bool(st.get("values")), "roads_total_mi": st["totals"]["road_miles"],
                    "buildings_total": st["totals"]["buildings"], "totals": summary["totals"]})
        summaries[name] = summary
        for g in st["stations"]:
            if g["id"] not in levels:
                try:
                    levels[g["id"]] = status.station_levels(latest, g["id"])
                except Exception as e:
                    log(f"{g['id']}: no forecast read ({e})")
    if not doc:
        raise SystemExit(f"no prepared area under {prepared}")
    plot = "SWPT"       # Derek, 2026-11-27: the statewide map's forecast location is Sewells Point
    if plot not in levels:
        levels[plot] = status.station_levels(latest, plot)
    made = max(s["made_utc"] for s in summaries.values())
    page.write_levels(out, levels, {"made_utc": made, "stations_without_forecast": sorted({m for s in summaries.values() for m in s["stations_without_forecast"]})})
    gauge, ref = page.plot_gauge(known[plot]["lat"], known[plot]["lon"])
    with open(os.path.join(out, "areas.json"), "w") as fh:
        json.dump({"made_utc": made, "plot_gauge": gauge, "mllw_ref": ref,
                   "levels": {"start": float(depth.LEVELS_FT[0]), "step": 0.25, "n": int(len(depth.LEVELS_FT))},
                   "ponded_ft": status.PONDED_FT, "flooded_ft": status.FLOODED_FT, "disclaimer": page.DISCLAIMER,
                   "stations": {k: {"name": known[k]["name"], "lat": known[k]["lat"], "lon": known[k]["lon"]} for k in levels if k in known},
                   "areas": doc}, fh, separators=(",", ":"))
    for logo in ("stormsense_logo.png", "ccrm_logo.png"):
        shutil.copy(os.path.join(HERE, "..", "crest", "branding", logo), out)
    with open(os.path.join(HERE, "templates", "statewide.html")) as fh:
        html = fh.read()
    for key, f in (("__LEAFLET__", "leaflet.js"), ("__LEAFLET_CSS__", "leaflet.css")):
        with open(os.path.join(HERE, "..", "crest", "jslib", f)) as fh:
            html = html.replace(key, fh.read())
    with open(os.path.join(out, "index.html"), "w") as fh:
        fh.write('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                 '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                 '</head>\n<body>\n' + html + "\n</body>\n</html>\n")
    log(f"{len(doc)} areas written to {out}")
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prepared", required=True, help="folder holding one <name>/ folder per prepared area")
    ap.add_argument("--latest", required=True, help="StormSense Latest site directory (fore72/, 7day/)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    build(a.prepared, a.latest, a.out)


if __name__ == "__main__":
    main()
