"""Compare a prepared area's wet extent with the City of Norfolk's TITAN depth maps.

TITAN (https://gisshare.norfolk.gov/titan/) publishes one cached depth map per 0.5 ft of water level (NAVD88), with
the water driven through the city's stormwater pipes. This reads those tiles over a box and counts, cell by cell,
where TITAN and our surface-only and with-pipes extents agree. It needs prep's work directory (dem_ft.npy,
cidx.npy, cidx_piped.npy).

    python -m inundation.check_titan --work work --origin 379714 4092715 --epsg 26918 --levels 4.0 5.0 5.5 6.0

Downtown Norfolk and the Hague, 26 Nov 2026 (overlap = wet in both / wet in either, on land):
    level   surface only   with pipes and the floodwall
    4.0 ft      0.22            0.61
    5.0 ft      0.33            0.70
    5.5 ft      0.35            0.72
    6.0 ft      0.63            0.75
    6.5 ft      0.68            0.77
What is left is mostly terrain: TITAN's own DEM wets cells that the 2016 CoNED DEM has above the level.
"""
import argparse
import io
import os
import urllib.request

import numpy as np

from .depth import LEVELS_FT

TILES = "https://tiles.arcgis.com/tiles/fUAsosS8WXl3mXcn/arcgis/rest/services/Water_Depth_for_Elev_{level}_ft/MapServer/tile/{z}/{r}/{c}"
R0 = 20037508.342787
Z = 17                                              # 1.19 m in Web Mercator, about 0.96 m on the ground at Norfolk
BOX = (-76.3050, 36.8440, -76.2780, 36.8640)        # downtown, the Hague, St. Paul's


def titan_wet(level, r0, r1, c0, c1, cache):
    from PIL import Image
    out = np.zeros(((r1 - r0 + 1) * 256, (c1 - c0 + 1) * 256), bool)
    name = f"{level:.1f}".replace(".", "_")
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            path = os.path.join(cache, f"{name}_{Z}_{r}_{c}.png")
            if not os.path.exists(path):
                try:
                    req = urllib.request.Request(TILES.format(level=name, z=Z, r=r, c=c), headers={"User-Agent": "python-urllib"})
                    with urllib.request.urlopen(req, timeout=60) as f:
                        data = f.read()
                except Exception:                    # no tile: nothing wet there
                    data = b""
                with open(path, "wb") as f:
                    f.write(data)
            with open(path, "rb") as f:
                data = f.read()
            if data:
                a = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))[..., 3] > 40
                out[(r - r0) * 256:(r - r0 + 1) * 256, (c - c0) * 256:(c - c0 + 1) * 256] = a
    return out


def compare(work, origin, epsg, levels, box=BOX, log=print):
    from pyproj import Transformer
    res = 2 * R0 / 256 / 2 ** Z
    tm = Transformer.from_crs(4326, 3857, always_xy=True)
    x0, y0 = tm.transform(box[0], box[1]); x1, y1 = tm.transform(box[2], box[3])
    c0, c1 = int((x0 + R0) / (256 * res)), int((x1 + R0) / (256 * res))
    r0, r1 = int((R0 - y1) / (256 * res)), int((R0 - y0) / (256 * res))
    w, h = (c1 - c0 + 1) * 256, (r1 - r0 + 1) * 256
    mx, my = np.meshgrid(-R0 + (c0 * 256 + np.arange(w) + 0.5) * res, R0 - (r0 * 256 + np.arange(h) + 0.5) * res)
    ux, uy = Transformer.from_crs(3857, epsg, always_xy=True).transform(mx, my)
    col, row = np.floor(ux - origin[0]).astype(int), np.floor(origin[1] - uy).astype(int)
    dem = np.load(os.path.join(work, "dem_ft.npy"), mmap_mode="r")
    ok = (row >= 0) & (row < dem.shape[0]) & (col >= 0) & (col < dem.shape[1])
    ground = np.full((h, w), np.nan, np.float32)
    ground[ok] = dem[row[ok], col[ok]]
    land = ground > 0.5                              # leave the open water out of the score
    ours = {}
    for name, f in (("surface only", "cidx.npy"), ("with pipes", "cidx_piped.npy")):
        if os.path.exists(os.path.join(work, f)):
            a = np.full((h, w), 255, np.uint8)
            a[ok] = np.load(os.path.join(work, f), mmap_mode="r")[row[ok], col[ok]]
            ours[name] = a
    cache = os.path.join(work, "titan_tiles")
    os.makedirs(cache, exist_ok=True)
    rows = []
    for level in levels:
        t = titan_wet(level, r0, r1, c0, c1, cache)
        k = int(np.searchsorted(LEVELS_FT, level + 1e-6) - 1)
        for name, a in ours.items():
            o = a <= k
            both, mine, theirs = int((o & t & land).sum()), int((o & ~t & land).sum()), int((~o & t & land).sum())
            rows.append({"level_ft": level, "extent": name, "both": both, "only_ours": mine, "only_titan": theirs,
                         "overlap": round(both / max(1, both + mine + theirs), 3)})
            log(f"{level:4.1f} ft {name:13s} both {both:9,d}  only ours {mine:8,d}  only TITAN {theirs:8,d}  overlap {rows[-1]['overlap']:.2f}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True, help="prep's work directory")
    ap.add_argument("--origin", nargs=2, type=float, required=True, metavar=("X0", "YTOP"), help="west and north edge of the area's grid")
    ap.add_argument("--epsg", type=int, default=26918)
    ap.add_argument("--levels", nargs="+", type=float, default=[4.0, 5.0, 5.5, 6.0, 6.5])
    ap.add_argument("--box", nargs=4, type=float, default=BOX, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    a = ap.parse_args(argv)
    compare(a.work, a.origin, a.epsg, a.levels, tuple(a.box))


if __name__ == "__main__":
    main()
