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

from . import FLOODED_FT, PIECE_M, PONDED_FT, barriers, damage, depth, drainage, ffe, page, segments, tether, value, vgin

OVER_WATER_FT = 1.0   # ground below this (ft NAVD88; MHHW at Sewells Point is 1.15) cannot be a road surface;
                      # lower where the gauge's own mean higher high water is lower (over_water_ft)
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


LAND_MARGIN_M = 1500


def land_bounds(bounds, xs, ys, margin=LAND_MARGIN_M):
    """The frame cut back to the roads and buildings plus a margin, never larger than `bounds`. A county's boundary
    runs far out into the bay and the ocean (Accomack's to the Maryland line in the Chesapeake and three miles off
    the beach), and open water that far from anything mapped only costs memory and disk."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    ok = np.isfinite(xs) & np.isfinite(ys)
    if not ok.any():
        return bounds
    xs, ys = xs[ok], ys[ok]
    return (max(bounds[0], int(np.floor(xs.min() - margin))), max(bounds[1], int(np.floor(ys.min() - margin))),
            min(bounds[2], int(np.ceil(xs.max() + margin))), min(bounds[3], int(np.ceil(ys.max() + margin))))


def first_lonlat(feature):
    """The first point of a feature's geometry (lon, lat), or None without one."""
    c = (feature.get("geometry") or {}).get("coordinates") or []
    while c and isinstance(c[0], (list, tuple)):
        if not isinstance(c[0][0], (list, tuple)):
            return c[0][0], c[0][1]
        c = c[0]
    return None


def first_points(features, transform):
    """One corner of each building's outline, in map coordinates: enough to know how far the buildings reach."""
    pts = [p for p in map(first_lonlat, features) if p is not None]
    if not pts:
        return np.zeros(0), np.zeros(0)
    lon, lat = np.asarray(pts, float).T
    x, y = transform(lon, lat)
    return np.asarray(x), np.asarray(y)


def parse_box(text):
    """'west,south,east,north' in degrees as four floats, or None for an empty text."""
    if not (text or "").strip():
        return None
    box = tuple(float(v) for v in text.replace(",", " ").split())
    if len(box) != 4 or not (box[0] < box[2] and box[1] < box[3]):
        raise ValueError(f"a box is west,south,east,north in degrees, not {text!r}")
    return box


def in_box(features, box):
    """The features whose first point lies in the box (west and south edges in, east and north edges out, so that
    boxes side by side share nothing and lose nothing). A locality too large for one frame is prepared as several
    areas of inundation/areas.csv, each with its `box` (Accomack: north, south and Tangier Island)."""
    w, s, e, n = box
    keep = []
    for f in features:
        p = first_lonlat(f)
        if p is not None and w <= p[0] < e and s <= p[1] < n:
            keep.append(f)
    return keep


LOW_STEP_M = 64


def near_low(roads, bldgs, grid, bounds, transform, margin=LAND_MARGIN_M, step=LOW_STEP_M):
    """An upland county cut down to what a mapped level can reach. `grid` is depth.tidal_low's numbered map of low
    ground over `bounds`. Returns the roads with a vertex, and the buildings with their first point, within `margin`
    of low ground; the frame around them and around the whole of each low part they lie near (the river they flood
    from has to be in the frame); and how many low parts that is."""
    from scipy import ndimage
    if not grid.any():
        return [], [], bounds, 0
    dist, (ir, ic) = ndimage.distance_transform_edt(grid == 0, sampling=step, return_indices=True)
    part = grid[ir, ic]                                             # each cell's nearest low part
    x0, y0, x1, y1 = bounds

    def cells(x, y):
        c = np.clip(((np.asarray(x, float) - x0) // step).astype(int), 0, grid.shape[1] - 1)
        r = np.clip(((y1 - np.asarray(y, float)) // step).astype(int), 0, grid.shape[0] - 1)
        return r, c

    used, kept_roads, xs, ys = set(), [], [], []
    for f in roads:
        c = (f.get("geometry") or {}).get("coordinates") or []
        while c and isinstance(c[0], (list, tuple)) and isinstance(c[0][0], (list, tuple)):
            c = [q for sub in c for q in sub]
        if not c:
            continue
        a = np.asarray(c, float)
        x, y = transform(a[:, 0], a[:, 1])
        r, cc = cells(x, y)
        near = dist[r, cc] <= margin
        if near.any():
            kept_roads.append(f); used.update(part[r, cc][near].tolist()); xs.append(x); ys.append(y)
    bx, by = first_points(bldgs, transform)
    ok = np.isfinite(bx) & np.isfinite(by)
    r, cc = cells(np.where(ok, bx, x0), np.where(ok, by, y0))
    near = ok & (dist[r, cc] <= margin)
    kept_bldgs = [f for f, k in zip(bldgs, near) if k]
    used.update(part[r, cc][near].tolist()); xs.append(bx[near]); ys.append(by[near])
    used.discard(0)
    if not kept_roads and not kept_bldgs:
        return [], [], bounds, 0
    rr, cc = np.nonzero(np.isin(grid, sorted(used)))
    xs.append(x0 + np.array([cc.min(), cc.max() + 1]) * step); ys.append(y1 - np.array([rr.max() + 1, rr.min()]) * step)
    return kept_roads, kept_bldgs, land_bounds(bounds, np.concatenate(xs), np.concatenate(ys), margin), len(used)


def middle(a):
    """Row-wise median treating NaN as 'never' (+inf): NaN when most of the row never floods."""
    m = np.median(np.where(np.isfinite(a), a, np.inf), axis=1)
    return np.where(np.isfinite(m), m, np.nan)


def over_water_ft(stations, high_tide=tether.high_tide_ft):
    """For each place, by its gauge: ground below this is water or marsh under a deck or a pier, not a road or a
    yard. OVER_WATER_FT, or the gauge's mean higher high water where that is lower: the bay's tide is small
    (0.52 ft NAVD88 at Tangier, 0.21 at Windmill Point), and Tangier Island's lanes and yards lie at 0.7 to 1.5 ft.
    With one limit of 1 ft, 1,219 of the island's 1,381 road pieces were bridges and 459 of 581 buildings piers."""
    known = {}
    out = np.full(len(stations), OVER_WATER_FT)
    for i, st in enumerate(stations):
        if st not in known:
            h = high_tide(st) if st else None
            known[st] = OVER_WATER_FT if h is None else min(OVER_WATER_FT, h)
        out[i] = known[st]
    return out


DECK_DROP_FT = 12.0   # the ground along a road falls this much within DECK_EDGE_M only where a deck leaves it
DECK_EDGE_M = 15.0    # (a grade of 24%: no road does that), after 20 m within DECK_LEVEL_FT of the abutment's height
DECK_MAX_M = 600.0    # the deck's far end is looked for this far along a highway or a ramp (MTFCC S11.., S12.., S1630)
DECK_LOCAL_M = 150.0  # and this far along any other road: a long low run between two bluffs is a street, not a viaduct
DECK_LEVEL_FT = 5.0   # a deck's two ends, and the road behind an abutment, lie within this of each other
DECK_APPROACH_M = 20.0
DECK_OPEN_M = 150.0   # a deck that leaves the locality's roads before its far end does so within this,
DECK_BORDER_M = 100.0  # and this close to the locality's boundary (elsewhere the road's end is a dead end);
DECK_BORDER_DROP_FT = 10.0   # there the fall of a low bridge's abutment is enough: this much
DECK_BORDER_EDGE_M = 20.0    # within this


def twins(pieces, tol=0.01):
    """For each piece the first piece that lies exactly on it: itself, unless VGIN holds its line twice. (Two
    carriageways that part at a point are not the same line, however close their first pieces are.)"""
    x0, y0, x1, y1 = (np.asarray(pieces[k], float) for k in ("x0", "y0", "x1", "y1"))
    if not len(x0):
        return np.arange(0)
    key = np.round(np.column_stack([x0 + x1, y0 + y1, np.abs(x1 - x0), np.abs(y1 - y0)]) / tol).astype(np.int64)
    _, first, group = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return first[np.ravel(group)]


DECK_STRAIGHT = -0.7  # two ends of a street go on in each other when their directions out of the ends are this opposed


def chain(pieces, tol=1.0, same=None):
    """(the two neighbours of each piece along its road, -1 where it has none; True where a line ends there and no
    other line starts; for a neighbour across a line's end that does not have the piece as its own neighbour, the
    side of the neighbour the piece lies on, by (piece, neighbour)). The neighbours are the pieces before and after
    it in its own line, and across a line's end the end of another line of the same street that starts there (VGIN
    cuts a street at every crossing and attribute change, and draws the parts in either direction): the one there
    is, or of several the one that goes straight on (within 45 degrees; where a street parts into two carriageways
    each of them goes on in the single one, which goes on in one of them). A loose end is where the roads of a
    locality stop: a dead end, or the middle of a bridge to the next locality. A line VGIN holds twice counts as one
    (`same`, see twins): the second copy is not a road that starts there, and nothing is joined to it."""
    from scipy.spatial import cKDTree
    road, part, seq = (np.asarray(pieces[k]) for k in ("road", "part", "seq"))
    n = len(road)
    same = twins(pieces) if same is None else same
    copy = same != np.arange(n)
    nb = np.full((n, 2), -1, np.int64)
    i = np.flatnonzero((road[1:] == road[:-1]) & (part[1:] == part[:-1]) & (seq[1:] == seq[:-1] + 1))
    nb[i, 1], nb[i + 1, 0] = i + 1, i
    street = np.array(["" if t is None else str(t) for t in pieces["ST_FULL"]], dtype=object)
    first, last = np.flatnonzero(nb[:, 0] < 0), np.flatnonzero(nb[:, 1] < 0)
    who = np.concatenate([first, last])
    side = np.concatenate([np.zeros(len(first), int), np.ones(len(last), int)])
    x0, y0, x1, y1 = (np.asarray(pieces[k], float) for k in ("x0", "y0", "x1", "y1"))
    xy = np.concatenate([np.column_stack([x0[first], y0[first]]), np.column_stack([x1[last], y1[last]])])
    out = np.concatenate([np.column_stack([x0[first] - x1[first], y0[first] - y1[first]]),      # the way out of each end
                          np.column_stack([x1[last] - x0[last], y1[last] - y0[last]])])
    out = out / np.maximum(np.hypot(out[:, 0], out[:, 1]), 1e-9)[:, None]
    loose = np.zeros((n, 2), bool)
    loose[who, side] = True
    via = {}
    if len(who) > 1:
        met = {}
        for a, b in cKDTree(xy).query_pairs(tol):
            if same[who[a]] == same[who[b]]:
                continue                                           # one piece's two ends, or the same line twice
            loose[who[a], side[a]] = loose[who[b], side[b]] = False
            if street[who[a]] and street[who[a]] == street[who[b]] and not copy[who[a]] and not copy[who[b]]:
                met.setdefault(a, []).append(b); met.setdefault(b, []).append(a)
        for a, others in met.items():
            if len(others) == 1 and len(met[others[0]]) == 1:      # two ends of one street, and no third
                nb[who[a], side[a]] = who[others[0]]
                continue
            b = min(others, key=lambda o: float(out[a] @ out[o]))
            if float(out[a] @ out[b]) <= DECK_STRAIGHT:
                nb[who[a], side[a]] = who[b]
        for a in met:                                              # joined one way only: where the walk comes in
            b = nb[who[a], side[a]]
            if b >= 0 and who[a] not in nb[b]:
                ends = [o for o in met[a] if who[o] == b]
                via[(int(who[a]), int(b))] = int(side[ends[0]])
    return nb, loose, via


def at_border(pieces, loc, fwd, within=DECK_BORDER_M):
    """True for the pieces within 100 m of the locality's boundary: there a road that stops goes on in the next
    locality's roads. Only the pieces at the ends of their lines are looked at."""
    import shapely
    from shapely.geometry import shape
    road, part, seq = (np.asarray(pieces[k]) for k in ("road", "part", "seq"))
    out = np.zeros(len(road), bool)
    if not len(road):
        return out
    same = (road[1:] == road[:-1]) & (part[1:] == part[:-1]) & (seq[1:] == seq[:-1] + 1)
    ends = np.flatnonzero(np.r_[True, ~same] | np.r_[~same, True])
    edge = shapely.transform(shapely.force_2d(shape(loc["geometry"])).boundary,
                             lambda c: np.column_stack(fwd(c[:, 0], c[:, 1])))
    shapely.prepare(edge)
    out[ends] = shapely.dwithin(edge, shapely.points(np.asarray(pieces["xm"], float)[ends],
                                                     np.asarray(pieces["ym"], float)[ends]), within)
    return out


def decks(pieces, ground, drop=DECK_DROP_FT, edge_m=DECK_EDGE_M, max_m=DECK_MAX_M, level=DECK_LEVEL_FT, open_m=DECK_OPEN_M,
          border=None, water_ft=OVER_WATER_FT):
    """True for the pieces under a deck the elevation data does not show: an overpass or a viaduct over low ground.

    A road that has run level (within 5 ft for 20 m) does not then fall 12 ft within 15 m. Where the ground under it
    does, the road has left the ground at an abutment, and the pieces beyond it are under the deck as far as the
    piece where the ground is back within 5 ft of the abutment's height (Interstate 95 by Pocahontas Island in
    Petersburg: 47 ft at the abutment, 3.3 to 4 ft under the deck for 120 m, then the slope up to 44 ft) or where the
    road is over water (ground below water_ft: Interstate 295 over the terrace north of the Appomattox), if there
    is one within 600 m along the same street (150 m where the road is not a highway or a ramp: River Walk in
    Middlesex runs 575 m at 5 ft between two bluffs). A road that comes down a bank and goes on low is not under a deck:
    nothing brings it back up. A road to a landing or across a ravine comes down on a grade (Beaverdam Road and
    Marina Drive in Surry fall 3 ft in every 5 m for 100 m and more). Neither is a street under another road's deck
    that the elevation data still has (one high piece in a low street).

    Each locality has its half of a bridge to the next one (over Four Mile Run between Arlington and Alexandria, over
    the Potomac to Washington): the roads stop in the middle with no other road there. Where that is within 150 m of
    the abutment and at the locality's boundary (`border`, see at_border; without it no road's end counts), the pieces
    between are under the deck, and there an abutment is a fall of 10 ft within 20 m (South Arlington Ridge Road:
    13 ft, then 11, 7, 4 and 2 ft over the run)."""
    ground = np.asarray(ground, float)
    same = twins(pieces)
    nb, loose, via = chain(pieces, same=same)
    step = np.asarray(pieces["length_m"], float)
    water = ~np.isfinite(ground) | (ground < water_ft)
    kind = pieces.get("MTFCC")
    span = np.full(len(ground), float(max_m))
    if kind is not None:
        span[[k is not None and not str(k).startswith(("S11", "S12", "S1630")) for k in kind]] = min(max_m, DECK_LOCAL_M)
    out = np.zeros(len(ground), bool)
    least = min(drop, DECK_BORDER_DROP_FT) if border is not None else drop
    reach = max(edge_m, DECK_BORDER_EDGE_M) if border is not None else edge_m
    fall = np.zeros(len(ground), bool)              # pieces with much lower ground within four pieces in array order
    for k in (1, 2, 3, 4):
        d = ground[:-k] - ground[k:]
        fall[:-k] |= d >= least
        fall[k:] |= -d >= least
    ends = (nb < 0).any(axis=1)
    for k in (1, 2, 3, 4):                          # and those near a line's end, whose neighbours are elsewhere
        ends[:-k] |= ends[k:]
        ends[k:] |= ends[:-k]

    def onward(at, came):
        """The piece after `at` coming from `came`, and the side of `at` it lies on."""
        if nb[at][0] == came:
            side = 1
        elif nb[at][1] == came:
            side = 0
        else:                                       # in from a carriageway that `at` does not go on in
            side = 1 - via.get((came, at), 1)
        return int(nb[at][side]), side

    for i in np.flatnonzero((fall | ends) & np.isfinite(ground)):
        for first in nb[i]:
            came, at, far, begin, steep = i, int(first), 0.0, None, False
            while at >= 0 and at != came and far + step[at] <= reach + 1e-6:       # the abutment
                far += step[at]
                if begin is None and ground[at] <= ground[i] - least:
                    begin = (came, at, far)
                if far <= edge_m + 1e-6 and ground[at] <= ground[i] - drop:
                    steep = True
                came, at = at, onward(at, came)[0]
            if begin is None:
                continue
            back, b0, b1, flat = 0.0, i, onward(i, int(first))[0], True            # the road behind the abutment:
            while b1 >= 0 and b1 != b0 and back < DECK_APPROACH_M - 1e-6:
                if not abs(ground[b1] - ground[i]) <= level:
                    flat = False
                    break
                back, b0, b1 = back + step[b1], b1, onward(b1, b0)[0]
            if not flat or back < DECK_APPROACH_M / 2 - 1e-6:                      # level for 20 m (10 m where it is short)
                continue
            came, at, far = begin
            under = [at]                            # the pieces on the slope before it stay on the ground
            while far <= span[i]:                   # along the low ground to where it comes back up
                nxt, side = onward(at, came)
                if nxt < 0:
                    if loose[at, side] and far <= open_m and border is not None and border[at]:
                        out[under] = True           # the locality's roads stop under the deck
                    break
                if nxt == at or nxt == i:
                    break
                came, at = at, nxt
                far += step[at]
                if ground[at] >= ground[i] - level or water[at]:
                    if steep:
                        out[under] = True
                    break
                under.append(at)
    return out[same] & ~water                       # the second copy of a line is under the deck its first is under


def bridges(pieces, ground, limit=OVER_WATER_FT, deck=None):
    """0 road on the ground; 1 the elevation model shows water or a cut under it (bridge deck, tunnel);
    2 a bridge or overpass in VGIN's own coding; 3 the same street within APPROACH_M of a 1 (the spans
    over the banks, where the bare-earth model shows the shore under the deck); 4 under a deck over low ground
    that the road's own profile gives away (decks, or `deck` where that is already known). limit: ground below
    it is water, one value or one per piece (over_water_ft)."""
    from scipy.spatial import cKDTree
    code = np.zeros(len(ground), np.int8)
    seg = np.array([int(t) if t is not None else -1 for t in pieces["SEG_TYPE"]])
    code[decks(pieces, ground) if deck is None else deck] = 4
    code[seg == RCL_BRIDGE] = 2
    over = (ground < limit) | ~np.isfinite(ground)
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


def via_pipes(connect, surface):
    """1 where water reaches the place through the storm drains before it does over the surface."""
    return (np.isfinite(connect) & (~np.isfinite(surface) | (connect < surface))).astype(int)


def road_table(pieces, dem, cidx, origin, surface=None, border=None):
    g, c = depth.sample(dem, cidx, origin, pieces["sx"], pieces["sy"])
    ground, connect = middle(g), middle(c)
    deck = decks(pieces, ground, border=border) & np.isfinite(connect)       # only where it matters: in reach of the water
    extra = {}
    if surface is not None:
        cs = middle(depth.sample(dem, surface, origin, pieces["sx"], pieces["sy"])[1])
        extra = {"connect_surface_ft": cs, "via_pipes": via_pipes(connect, cs)}
    return {
        **extra,
        "ground_ft": ground, "connect_ft": connect,
        "ponded_ft": depth.first_level(ground, connect, PONDED_FT),
        "flooded_ft": depth.first_level(ground, connect, FLOODED_FT),
        "deck": deck, "bridge": bridges(pieces, ground, deck=deck),
    }


BAND_ROWS = 4096


def building_table(features, transform, dem, cidx, origin, levels=depth.LEVELS_FT, surface=None):
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
    # the footprints are burned into the grid in bands of rows, so a city of two billion cells needs no grid of its own
    real = np.array([i for i, g in enumerate(geoms) if g is not None and not g.is_empty], int)
    tree = shapely.STRtree([geoms[i] for i in real]) if len(real) else None
    h, w = dem.shape
    bs, zs, cis, sis = [], [], [], []
    for r0 in range(0, h if tree is not None else 0, BAND_ROWS):
        r1 = min(r0 + BAND_ROWS, h)
        hit = np.sort(real[tree.query(shapely.box(origin[0], origin[1] - r1, origin[0] + w, origin[1] - r0))])
        if not len(hit):
            continue
        ids = rfeat.rasterize(((geoms[i], i + 1) for i in hit), out_shape=(r1 - r0, w),
                              transform=from_origin(origin[0], origin[1] - r0, 1, 1), fill=0, all_touched=True, dtype="int32")
        cell = np.flatnonzero(ids)
        bs.append(ids.ravel()[cell] - 1)
        del ids
        zs.append(np.asarray(dem[r0:r1]).ravel()[cell])
        cis.append(np.asarray(cidx[r0:r1]).ravel()[cell])
        if surface is not None:
            sis.append(np.asarray(surface[r0:r1]).ravel()[cell])
    b = np.concatenate(bs) if bs else np.zeros(0, int)
    z = np.concatenate(zs) if zs else np.zeros(0, np.float32)
    ci = np.concatenate(cis) if cis else np.zeros(0, np.uint8)
    c = np.where(ci == depth.NEVER, np.inf, levels[np.minimum(ci, len(levels) - 1)])
    ok = np.isfinite(z)
    if surface is not None:
        si = np.concatenate(sis) if sis else np.zeros(0, np.uint8)
        cs = np.where(si == depth.NEVER, np.inf, levels[np.minimum(si, len(levels) - 1)])[ok]
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
    extra = {}
    if surface is not None:
        csurf = pick(cs, 0.5)
        csurf[~np.isfinite(csurf)] = np.nan
    # footprints that touched no cell (off the grid): read the centroid
    miss = np.flatnonzero(~has)
    if len(miss):
        cx = np.array([geoms[i].centroid.x if geoms[i] is not None and not geoms[i].is_empty else np.nan for i in miss])
        cy = np.array([geoms[i].centroid.y if geoms[i] is not None and not geoms[i].is_empty else np.nan for i in miss])
        okc = np.isfinite(cx)
        g1, c1 = depth.sample(dem, cidx, origin, cx[okc], cy[okc])
        ground[miss[okc]], low[miss[okc]], connect[miss[okc]] = g1, g1, c1
        if surface is not None:
            csurf[miss[okc]] = depth.sample(dem, surface, origin, cx[okc], cy[okc])[1]
    if surface is not None:
        extra = {"connect_surface_ft": csurf, "via_pipes": via_pipes(connect, csurf)}
    cen = np.array([[g.centroid.x, g.centroid.y] if g is not None and not g.is_empty else [np.nan, np.nan] for g in geoms])
    area = np.array([g.area if g is not None else np.nan for g in geoms])
    return {
        "x": cen[:, 0], "y": cen[:, 1], "area_m2": area, "cells": count, **extra,
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
    ap.add_argument("--no-drainage", action="store_true", help="leave the stormwater pipes out (surface water only)")
    ap.add_argument("--no-barriers", action="store_true", help="leave the floodwalls and levees of the National Levee Database out")
    ap.add_argument("--no-values", action="store_true", help="leave out parcels, assessed values and damage inputs")
    ap.add_argument("--no-certificates", action="store_true", help="leave out HRPDC's elevation certificates (first floors are all assumed)")
    ap.add_argument("--low", action="store_true", help="only the roads and buildings within reach of the ground at or "
                    "below the highest level (a county that is mostly upland: Henrico, Fairfax)")
    ap.add_argument("--box", default="", help="west,south,east,north in degrees: only the locality's roads and buildings "
                    "that start inside (a part of a locality too large for one frame)")
    a = ap.parse_args(argv)
    from pyproj import Transformer
    os.makedirs(a.out, exist_ok=True); os.makedirs(a.work, exist_ok=True)

    loc = cached(f"{a.work}/locality.geojson", lambda: vgin.locality(a.fips))
    ring = np.asarray((loc["geometry"]["coordinates"][0] if loc["geometry"]["type"] == "Polygon"
                       else loc["geometry"]["coordinates"][0][0]), float)
    epsg = utm_epsg(ring[:, 0].mean())
    box = parse_box(a.box)
    write_outline(f"{a.out}/{a.name}_outline.geojson", loc, box=box)
    fwd = Transformer.from_crs(4326, epsg, always_xy=True).transform
    inv = Transformer.from_crs(epsg, 4326, always_xy=True).transform
    bounds = bounds_of(loc, fwd)
    log(loc["properties"].get("NAMELSAD"), "EPSG", epsg, "bounds", bounds)

    roads = cached(f"{a.work}/roads_raw.geojson", lambda: {"type": "FeatureCollection", "features": vgin.roads(a.fips)})["features"]
    bldgs = cached(f"{a.work}/buildings_raw.geojson", lambda: {"type": "FeatureCollection", "features": vgin.buildings(a.fips, log)})["features"]
    log(len(roads), "road segments,", len(bldgs), "buildings:", building_source(bldgs))
    if box:
        roads, bldgs = in_box(roads, box), in_box(bldgs, box)
        log(f"inside {box}: {len(roads)} road segments, {len(bldgs)} buildings")
        if not roads and not bldgs:
            raise SystemExit(f"nothing of {a.fips} lies inside {box}")
    pieces = segments.split(roads, fwd)
    log(len(pieces["road"]), f"pieces of at most {PIECE_M:g} m,", round(pieces["length_m"].sum() / 1000, 1), "km")
    bx, by = first_points(bldgs, fwd)
    whole = bounds
    bounds = land_bounds(bounds, np.concatenate([pieces["xm"], bx]), np.concatenate([pieces["ym"], by]))
    if bounds != whole:
        cut = 1 - (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) / ((whole[2] - whole[0]) * (whole[3] - whole[1]))
        log(f"frame cut back to the roads and buildings plus {LAND_MARGIN_M} m: {bounds} ({cut:.0%} smaller)")

    low_doc = None
    if a.low:
        n_roads, n_bldgs, km = len(roads), len(bldgs), pieces["length_m"].sum() / 1000
        grid = depth.tidal_low(depth.low_grid(a.dem or ["/vsicurl/" + u for u in depth.coned_tiles(bounds)], bounds, step=LOW_STEP_M))
        roads, bldgs, bounds, parts = near_low(roads, bldgs, grid, bounds, fwd)
        if not roads:
            raise SystemExit(f"{a.name}: no road within {LAND_MARGIN_M} m of tidal ground at or below {depth.LEVELS_FT[-1]:g} ft NAVD88")
        pieces = segments.split(roads, fwd)
        bx, by = first_points(bldgs, fwd)
        low_doc = {"limit_ft": float(depth.LEVELS_FT[-1]), "margin_m": LAND_MARGIN_M, "grid_m": LOW_STEP_M, "low_parts": parts,
                   "road_segments_all": n_roads, "road_km_all": round(float(km), 1), "buildings_all": n_bldgs}
        log(f"within {LAND_MARGIN_M} m of ground at or below {depth.LEVELS_FT[-1]:g} ft that the tide can reach: {len(roads):,} of "
            f"{n_roads:,} road segments and {len(bldgs):,} of {n_bldgs:,} buildings; frame {bounds}")

    dem_path, cidx_path = f"{a.work}/dem_ft.npy", f"{a.work}/cidx.npy"
    origin = (bounds[0], bounds[3])
    piped_path = f"{a.work}/cidx_piped.npy"
    dsrc = None if a.no_drainage else drainage.sources().get(a.fips)
    if os.path.exists(dem_path):
        dem = np.load(dem_path, mmap_mode="r")
    else:
        tiles = a.dem or ["/vsicurl/" + u for u in depth.coned_tiles(bounds)]
        log("elevation tiles:", ", ".join(os.path.basename(t) for t in tiles))
        dem, _ = depth.read_dem(tiles, bounds, into=dem_path + ".part.npy")      # built on disk: a city is 1 to 8 GB
        dem.flush(); del dem
        os.replace(dem_path + ".part.npy", dem_path)
        dem = np.load(dem_path, mmap_mode="r")
    if dem.shape != (bounds[3] - bounds[1], bounds[2] - bounds[0]):
        raise SystemExit(f"{dem_path} does not match the area's bounds; delete it and run again")
    wall, protected, bdoc = None, [], {"routes": [], "areas": []}
    if not a.no_barriers:
        west, south = inv(bounds[0], bounds[1]); east, north = inv(bounds[2], bounds[3])
        bdoc = cached(f"{a.work}/barriers.json", lambda: barriers.fetch((west, south, east, north), log))
        lines, protected = barriers.shapes(bdoc, fwd)
        wall = barriers.cells(lines, dem.shape, origin)
        log(f"flood barriers: {len(lines)} systems, {len(wall[0]):,} wall cells, {len(protected)} leveed areas")
    w, s = inv(bounds[0], bounds[1]); e, n = inv(bounds[2], bounds[3])
    hucs = cached(f"{a.work}/huc12.geojson", lambda: {"type": "FeatureCollection", "features": tether.huc12((w, s, e, n))})["features"]
    forced = tether.overrides()
    surface_ft = tidal_surface(dem, dem_path, log)
    if surface_ft is not None:
        dem = np.load(dem_path, mmap_mode="r")
    sea = sea_cells(dem, origin, hucs, forced, fwd, log)
    net = None
    if dsrc:
        layers = cached(f"{a.work}/drainage.json", lambda: drainage.fetch(dsrc, log))
        net = drainage.network(layers, fwd, dsrc, protected=protected)
        log("stormwater:", net["counts"] if net else "no features")
    surface = None
    if net is not None:
        if os.path.exists(cidx_path) and os.path.exists(piped_path):
            surface, cidx = np.load(cidx_path, mmap_mode="r"), np.load(piped_path, mmap_mode="r")
        else:
            surface, cidx = depth.connect_index(dem, seed=sea, log=log, links=drainage.links(net, dem, origin), blocked=wall, store=(cidx_path, piped_path))
            if not os.path.exists(piped_path):                    # the labelling method hands back arrays
                np.save(cidx_path, surface); np.save(piped_path, cidx)
    elif os.path.exists(cidx_path):
        cidx = np.load(cidx_path, mmap_mode="r")
    else:
        cidx = depth.connect_index(dem, seed=sea, log=log, blocked=wall, store=cidx_path)
        if not os.path.exists(cidx_path):
            np.save(cidx_path, cidx)

    rt = road_table(pieces, dem, cidx, origin, surface, border=at_border(pieces, loc, fwd))
    bt, bgeoms = building_table(bldgs, fwd, dem, cidx, origin, surface=surface)
    log("ground and connect levels read")
    vsrc = None if a.no_values else value.sources().get(a.fips)
    vcols, n_cert = {}, 0
    if vsrc:
        parcels = cached(f"{a.work}/parcels.geojson", lambda: {"type": "FeatureCollection", "features": value.fetch_parcels(vsrc)})["features"]
        if vsrc["assessment"]["type"] == "parcels":           # the parcel layer carries the values itself
            records = value.records_from_parcels(parcels, vsrc)
            if vsrc.get("use_from"):                          # the use codes are in another layer
                value.add_use(records, cached(f"{a.work}/use_codes.json", lambda: value.fetch_use(vsrc)))
        else:
            records = cached(f"{a.work}/assessment.json", lambda: value.fetch_assessment(vsrc))
        zhvi = cached(f"{a.work}/zhvi.json", lambda: {k: list(v) for k, v in value.fetch_zhvi().items()})
        vcols = value.attach(bgeoms, parcels, fwd, value.roll(records, vsrc), vsrc, zhvi)
        vcols["first_floor_ft"] = damage.first_floor_ft(vcols["year_built"], vsrc.get("firm_year"))
        vcols["floor_source"] = np.full(len(bldgs), "", object)
        if not a.no_certificates:                             # surveyed first floors where HRPDC has the certificate
            certs = cached(f"{a.work}/elevation_certificates.geojson", lambda: {"type": "FeatureCollection", "features": ffe.fetch()})["features"]
            floor = ffe.attach(certs, bgeoms, fwd)
            has = np.isfinite(floor) & np.isfinite(bt["ground_ft"])
            vcols["ffe_navd_ft"] = np.where(has, floor, np.nan)
            vcols["first_floor_ft"] = np.where(has, np.maximum(floor - bt["ground_ft"], 0), vcols["first_floor_ft"])
            vcols["floor_source"][has] = "certificate"
            n_cert = int(has.sum())
            log(f"first floors: {n_cert:,} buildings have an elevation certificate (HRPDC), median "
                f"{np.median(vcols['first_floor_ft'][has]) if n_cert else float('nan'):.1f} ft above the ground")
        log(f"values: {int(np.isfinite(vcols['value_bldg']).sum()):,} of {len(bldgs):,} buildings have an assessed building value "
            f"({vsrc['tax_year']}), {int(vcols['residential'].sum()):,} are homes")

    names = {f["properties"]["huc12"]: f["properties"]["name"].strip() for f in hucs}
    sts = tether.stations()
    rt["huc12"] = tether.in_polygons(hucs, fwd, pieces["xm"], pieces["ym"])
    bt["huc12"] = tether.in_polygons(hucs, fwd, bt["x"], bt["y"])
    rt["station"], rt["station_km"] = tether.nearest(pieces["xm"], pieces["ym"], sts, fwd, rt["huc12"], forced)
    okb = np.isfinite(bt["x"])
    bt["station"] = np.full(len(bldgs), "", dtype=object); bt["station_km"] = np.full(len(bldgs), np.nan)
    bt["station"][okb], bt["station_km"][okb] = tether.nearest(bt["x"][okb], bt["y"][okb], sts, fwd, bt["huc12"][okb], forced)
    # with the gauges known: what is water under a deck or a pier, by each gauge's own high tide
    rt["bridge"] = bridges(pieces, rt["ground_ft"], over_water_ft(rt["station"]), deck=rt["deck"])
    bt["over_water"] = (bt["ground_ft"] < over_water_ft(bt["station"])).astype(int)

    lon, lat = inv(pieces["xm"], pieces["ym"])
    road_cols = {
        "piece": np.arange(len(lon)), "rcl_oid": pieces["OBJECTID"], "rcl_id": pieces["RCL_ID"], "seq": pieces["seq"],
        "street": pieces["ST_FULL"], "mtfcc": pieces["MTFCC"], "seg_type": pieces["SEG_TYPE"],
        "length_m": pieces["length_m"], "lon": lon, "lat": lat, **rt}
    blon, blat = inv(np.where(okb, bt["x"], 0), np.where(okb, bt["y"], 0))
    bldg_cols = {
        "building": np.arange(len(bldgs)), "vgin_oid": [f["properties"].get("OBJECTID") for f in bldgs],
        "source_id": [f["properties"].get("SOURCEFEATUREID") for f in bldgs],
        "outline": ["FEMA" if f["properties"].get("FOOTPRINTS") else "VGIN" for f in bldgs],
        "lon": np.where(okb, blon, np.nan), "lat": np.where(okb, blat, np.nan),
        **{k: v for k, v in bt.items() if k not in ("x", "y")}, **vcols}
    f2, f6 = "%.2f", "%.6f"
    fmt = {"ground_ft": f2, "low_ft": f2, "connect_ft": f2, "ponded_ft": f2, "flooded_ft": f2, "wet_low_ft": f2,
           "length_m": f2, "lon": f6, "lat": f6, "station_km": f2, "area_m2": "%.0f", "connect_surface_ft": f2,
           "year_built": "%.0f", "value_total": "%.0f", "value_impr": "%.0f", "value_bldg": "%.0f", "zhvi": "%.0f", "first_floor_ft": "%.1f", "ffe_navd_ft": "%.2f"}
    write_csv(f"{a.out}/{a.name}_roads.csv.gz", road_cols, fmt)
    write_csv(f"{a.out}/{a.name}_buildings.csv.gz", bldg_cols, fmt)

    # the tether table: what each gauge covers in each HUC-12
    rows = {}
    for kind, h, st, km in (("road_pieces", rt["huc12"], rt["station"], rt["station_km"]),
                            ("buildings", bt["huc12"][okb], bt["station"][okb], bt["station_km"][okb])):
        for hh, ss, kk in zip(h, st, km):
            r = rows.setdefault((hh, ss), {"road_pieces": 0, "buildings": 0, "max_km": 0.0})
            r[kind] += 1
            r["max_km"] = max(r["max_km"], kk if np.isfinite(kk) else 0.0)
    stn = {s["station"]: s["name"] for s in sts}
    with open(f"{a.out}/{a.name}_tether.csv", "w") as fh:
        fh.write("huc12,huc12_name,station,station_name,road_pieces,buildings,farthest_km,forced\n")
        for (hh, ss), r in sorted(rows.items()):
            fh.write(f"{hh},{names.get(hh, '')},{ss},\"{stn.get(ss, '') if ss else 'no gauge: left out'}\",{r['road_pieces']},{r['buildings']},"
                     f"{r['max_km']:.1f},{'yes' if ss in tether.group(tether.rule(hh, forced)) else ''}\n")
    ungauged = sorted({names.get(hh, hh) for (hh, ss) in rows if ss == ""})
    n_ungauged = {k: sum(r[k] for (hh, ss), r in rows.items() if ss == "") for k in ("road_pieces", "buildings")}
    if ungauged:
        log(f"no gauge for {n_ungauged['road_pieces']:,} road pieces and {n_ungauged['buildings']:,} buildings in {', '.join(ungauged)}: left out")

    if not a.no_gpkg:
        write_layers(f"{a.out}/{a.name}.gpkg", pieces, road_cols, bgeoms, bldg_cols, epsg)
    if a.gdb:
        write_layers(f"{a.out}/{a.name}.gdb", pieces, road_cols, bgeoms, bldg_cols, epsg)
    sources = {"depth": "NOAA/USGS CoNED Chesapeake Bay topobathy, 1 m (2016)", "roads": "VGIN road centerlines (VBMP RCL)",
               "buildings": building_source(bldgs)}
    if net is not None:
        sources["drainage"] = dsrc.get("source", "city stormwater layers")
    if bdoc["routes"]:
        sources["barriers"] = barriers.SOURCE + ": " + ", ".join(sorted({str(f["properties"].get("SYSTEM_NAME")) for f in bdoc["routes"]}))
    if vsrc:
        sources["values"] = f"{vsrc['assessment']['name']}; {vsrc['tax_year_note']}"
        sources["zillow"] = "Zillow Home Value Index by ZIP code (typical home value), " + (next(iter(zhvi.values()))[1] if zhvi else "")
        sources["damage"] = damage.CITATION
        if n_cert:
            sources["first_floors"] = f"{ffe.SOURCE}: {n_cert:,} buildings"
    if not a.no_page:
        used = sorted((set(rt["station"]) | set(bt["station"][okb])) - {""})
        if ungauged:
            sources["ungauged"] = ", ".join(ungauged)
        if low_doc:
            sources["low_frame"] = (f"{len(roads):,} of the locality's {low_doc['road_segments_all']:,} road segments and "
                                    f"{len(bldgs):,} of its {low_doc['buildings_all']:,} buildings")
        page.write_static(f"{a.out}/page", a.name, a.title or loc["properties"].get("NAME") or a.name, bounds, epsg, pieces,
                          road_cols, roads, fwd, bgeoms, bldg_cols, dem, cidx, [s for s in sts if s["station"] in used], sources,
                          values=None if not vsrc else {"tax_year": vsrc["tax_year"], "firm_year": vsrc.get("firm_year"), "certificates": n_cert, "home_rule": vsrc.get("residential_note"),
                                                        "zillow_month": next(iter(zhvi.values()))[1] if zhvi else ""},
                          hucs=hucs, forced=forced)
    with open(f"{a.out}/{a.name}_prep.json", "w") as fh:
        json.dump({"fips": a.fips, "name": a.name, "locality": loc["properties"].get("NAMELSAD"), "epsg": epsg,
                   **({"box": list(box)} if box else {}),
                   "bounds": bounds, "levels_ft": [float(depth.LEVELS_FT[0]), float(depth.LEVELS_FT[-1]), 0.25],
                   "depth_source": "NOAA/USGS CoNED Chesapeake Bay topobathy 1 m (2016), water joined to tidal water",
                   "roads": "VGIN VBMP_RCL", "buildings": "VGIN VA_Building_Footprints",
                   "road_segments": len(roads), "road_pieces": int(len(lon)), "buildings_n": len(bldgs),
                   "buildings_fema": sum(1 for f in bldgs if f["properties"].get("FOOTPRINTS")),
                   "piece_m": PIECE_M, "ponded_ft": PONDED_FT, "flooded_ft": FLOODED_FT,
                   "drainage": None if net is None else {"source": dsrc.get("source"), **net["counts"],
                                                         **({"saved_layers": layers["saved"]} if layers.get("saved") else {})},
                   "barriers": sorted({str(f["properties"].get("SYSTEM_NAME")) for f in bdoc["routes"]}),
                   "values": None if not vsrc else {"tax_year": vsrc["tax_year"], "note": vsrc["tax_year_note"], "firm_year": vsrc.get("firm_year"),
                                                    "assessment": vsrc["assessment"]["name"], "damage_curves": damage.CITATION,
                                                    "elevation_certificates": n_cert, "first_floor_source": ffe.SOURCE if n_cert else None,
                                                    "home_rule": vsrc.get("residential_note")},
                   "ungauged": None if not ungauged else {"huc12": ungauged, **n_ungauged,
                                                          "why": "no StormSense gauge stands for this water (tether_overrides.csv)"},
                   "low_frame": low_doc,
                   "water_surface_ft": surface_ft,
                   "prepared_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}, fh, indent=1)
    log("wrote", a.out)


def tidal_cells(dem):
    """(a cell in each body of tidal water at the lowest level, the largest first; a cell in each body that is a
    pit; the grid they were found on; every how many cells that grid takes).

    depth.tidal_seeds names the largest body and those that run off the grid's edge. One of them is tidal water only
    if it lies in water that leaves the frame at an ordinary tide's height (depth.leaving_water)."""
    from . import spread
    step = spread.seed_step(dem)
    coarse = np.asarray(dem[::step, ::step])
    try:
        cells = depth.tidal_seeds(coarse, cell_m2=step * step)
    except ValueError:
        return [], [], coarse, step
    leaving = depth.leaving_water(coarse, cell_m2=step * step)
    return ([(r * step, c * step) for r, c in cells if leaving[r, c]],
            [(r * step, c * step) for r, c in cells if not leaving[r, c]], coarse, step)


def tidal_surface(dem, dem_path, log=lambda *_: None):
    """Lowers the tide's water to the lowest level in a frame whose elevation data has no tidal ground there, and
    returns the level the water was found at (None where the frame has tidal ground, as nearly all do).

    depth.water_surface finds the river as the lidar met it where it runs off the frame's edge, and the rest of it
    is the even water in the same low ground, at or below depth.SURFACE_TOP_FT (depth.flat_water: the reaches
    upstream lie a little higher, the James at Richmond from 0.25 ft at the city line to 1.8 ft below the falls,
    with 26 m of uneven water between two of them). Its cells are written into the saved grid at
    the lowest level, so the water spreads from the river, a deck or a dock over it stands over water, and a second
    run finds the sea."""
    from scipy import ndimage
    tidal, _, coarse, step = tidal_cells(dem)
    if tidal:
        return None
    lowest = depth.LEVELS_FT[0]
    level, water = depth.water_surface(coarse, cell_m2=step * step)
    if level is None:
        raise SystemExit(f"no tidal water in the frame: no ground at or below {lowest:g} ft that water joins to the frame's "
                         f"edge, and no water of {depth.EDGE_BODY_M2 / 1e4:g} ha that leaves the frame at or below "
                         f"{depth.SURFACE_TOP_FT:g} ft")
    h, w = dem.shape
    flat = np.zeros((h, w), bool)
    for r0 in range(0, h, 1024):                                 # in strips, each read with two rows either side
        a0, r1 = max(r0 - 2, 0), min(r0 + 1024, h)
        flat[r0:r1] = depth.flat_water(np.asarray(dem[a0:min(r1 + 2, h)]))[r0 - a0:r0 - a0 + r1 - r0]
    lab, _ = ndimage.label(coarse <= depth.SURFACE_TOP_FT, structure=np.ones((3, 3)))     # the low ground it lies in
    river = np.isin(lab, np.unique(lab[water]))
    if step > 1:
        river = ndimage.binary_dilation(river, structure=np.ones((3, 3)))
    grid = np.load(dem_path, mmap_mode="r+")
    n, rows = 0, 256 * step
    for r0 in range(0, h, rows):                                 # in strips: the grid is on disk
        part = grid[r0:r0 + rows]
        m = river[r0 // step:r0 // step + 256]
        if step > 1:                                             # the coarse cells' water, cell by cell
            m = np.repeat(np.repeat(m, step, 0), step, 1)[:part.shape[0], :part.shape[1]]
        m = m & (flat[r0:r0 + rows] | (np.asarray(part) <= level))
        part[m] = lowest
        n += int(m.sum())
    grid.flush()
    log(f"no tidal ground at or below {lowest:g} ft: the elevation data has the river's surface, not its bed. Tidal "
        f"water is the {n / 1e4:,.0f} ha that leave the frame at or below {level:+.2f} ft or are even water in the same low ground")
    return level


def sea_cells(dem, origin, hucs, forced, fwd, log=lambda *_: None):
    """The cells the water spreads from: one in each body of tidal water (depth.tidal_seeds: the largest, and every
    estuary that leaves the grid and meets it outside). A body that leaves the grid inside a watershed no gauge
    stands for (tether_overrides.csv `-`: Back Bay, the North Landing River, the Blackwater) is not the sea, and
    neither is a pit (tidal_cells)."""
    def where(q):
        return f"{origin[0] + q[1]:.0f} E {origin[1] - q[0]:.0f} N"

    cells, pits, _, _ = tidal_cells(dem)
    for q in pits:
        log(f"  the ground at or below {depth.LEVELS_FT[0]:g} ft at {where(q)} is a pit, not tidal water: no water joins it "
            f"to the frame's edge at or below {depth.SURFACE_TOP_FT:g} ft")
    if not cells:
        raise SystemExit("no tidal water in the frame: nothing to flood from")
    keep = cells[:1]
    if len(cells) > 1:
        r, c = (np.array([q[i] for q in cells[1:]], float) for i in (0, 1))
        huc = tether.in_polygons(hucs, fwd, origin[0] + c + 0.5, origin[1] - r - 0.5)
        for q, h in zip(cells[1:], huc):
            if tether.rule(h, forced) == "":
                log(f"  water leaving the grid in {h} is not tidal water here (no gauge stands for it)")
            else:
                keep.append(q)
    log(f"tidal water: {len(keep)} bod{'y' if len(keep) == 1 else 'ies'} at {depth.LEVELS_FT[0]:+.0f} ft"
        + (" (the largest, and those that run off the grid's edge)" if len(keep) > 1 else "")
        + ", from " + "; ".join(where(q) for q in keep[:6]))
    return keep


def building_source(bldgs):
    """Where the building outlines came from, in words (the page and <name>_prep.json say it)."""
    other = [f["properties"]["FOOTPRINTS"] for f in bldgs if f["properties"].get("FOOTPRINTS")]
    if not other:
        return "VGIN building footprints"
    if len(other) == len(bldgs):
        return other[0] + " building outlines (VGIN has none here)"
    return f"VGIN building footprints, and {len(other):,} {other[0]} outlines where VGIN has no footprint"


def write_outline(path, loc, tol_deg=0.0003, box=None):
    """The locality's boundary, thinned to about 30 m, for the map of all areas (inundation.site); with a box, the
    part of it inside."""
    import shapely
    from shapely.geometry import mapping, shape
    g = shapely.simplify(shapely.force_2d(shape(loc["geometry"])), tol_deg)
    if box:
        g = shapely.intersection(shapely.make_valid(g), shapely.box(*box))
        g = shapely.multipolygons([p for p in shapely.get_parts(g) if p.geom_type == "Polygon"])
    with open(path, "w") as fh:
        json.dump({"type": "Feature", "properties": {"name": loc["properties"].get("NAMELSAD")}, "geometry": mapping(g)}, fh)


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
