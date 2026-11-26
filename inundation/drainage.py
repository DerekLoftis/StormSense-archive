"""Stormwater pipes as a way for tidal water to reach low ground.

The bathtub in depth.py only lets water travel over the surface. In the cities much of the nuisance flooding comes
up through the storm drains instead: at high tide the outfalls are under water, the pipes fill to the level of the
tide, and water comes out of every inlet whose rim is lower. This module turns a city's drainage layers into
"networks" (sets of openings joined by pipes and ditches) that depth.connect_index treats as level connections:

    at a water level L, if any opening of a network is under water, every other opening of that network whose
    ground is at or below L is a source too.

It is a static rule, the same idea as Norfolk's TITAN: no head loss, no pipe capacity, no rain. Openings marked with
a flap valve or tide gate do not pass water backwards, and pipes do not carry water under a flood barrier: a network
is cut where it enters a leveed area (barriers.py). Stormwater pump stations elsewhere are not treated as barriers
(most lift water out of an underpass and leave the pipes open to the tide; TITAN floods through them too).

Each city's layers and field names are in drainage_sources.json. The layers are read from the city's own ArcGIS
services (vgin.query pages them), in WGS84.
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_M = 0.5          # pipe ends and structures closer than this are the same node


def sources(path=None):
    with open(path or os.path.join(HERE, "drainage_sources.json")) as f:
        return json.load(f)


def fetch(src, log=None):
    """Download a city's drainage layers. Returns {"pipes": [...], "ditches": [...], "structures": [...]}
    as GeoJSON features (absent layers give empty lists)."""
    from . import vgin
    out = {}
    for kind in ("pipes", "ditches", "structures"):
        out[kind] = []
        for lyr in src.get(kind, []):
            feats = vgin.query(lyr["url"], lyr.get("where", "1=1"), lyr.get("fields", "*"), log)
            n = vgin.count(lyr["url"], lyr.get("where", "1=1"))
            if len(feats) != n:
                raise RuntimeError(f"{kind}: got {len(feats)} of {n} from {lyr['url']}")
            for f in feats:
                f["properties"]["_layer"] = lyr.get("name", kind)
            out[kind] += feats
            if log:
                log(f"  drainage {kind}: {len(feats):,} from {lyr['url'].split('/rest/services/')[-1]}")
    return out


def _lines(features):
    for f in features:
        g = f.get("geometry") or {}
        cs = g.get("coordinates") or []
        for part in (cs if g.get("type") == "MultiLineString" else [cs]):
            if len(part) >= 2:
                yield f, part


def _points(features):
    for f in features:
        g = f.get("geometry") or {}
        cs = g.get("coordinates") or []
        if g.get("type") == "Point" and len(cs) >= 2 and cs[0] is not None:
            yield f, cs


def _is(value, words):
    v = str(value or "").strip().lower()
    return bool(v) and any(w in v for w in words)


def network(layers, transform, src=None, snap=SNAP_M, protected=None):
    """Nodes of the drainage networks in map coordinates.

    Returns a dict of arrays, one row per node: x, y, net (network number), open (water can pass between the
    pipe and the surface here), zone (the leveed area it lies in, 0 none) and counts for the log. Nodes are the
    structures, the pipe ends and every vertex of the open ditches. `protected` is a list of leveed-area polygons
    in map coordinates: a network that runs into one is cut there into separate networks.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    src = src or {}
    closed_types = [w.lower() for w in src.get("closed_types", [])]
    type_field = src.get("structure_type_field")
    valve_field, valve_words = src.get("valve_field"), [w.lower() for w in src.get("valve_words", [])]

    xs, ys, is_open, edges = [], [], [], []

    def add(lon, lat, opening):
        xs.append(lon); ys.append(lat); is_open.append(opening)
        return len(xs) - 1

    n_struct = 0
    for f, c in _points(layers.get("structures", [])):
        p = f["properties"]
        opening = True
        if type_field and _is(p.get(type_field), closed_types):
            opening = False
        if valve_field and _is(p.get(valve_field), valve_words):
            opening = False
        add(c[0], c[1], opening); n_struct += 1
    n_pipe = 0
    for f, part in _lines(layers.get("pipes", [])):
        # a pipe end with no structure on it is a free end; whether it is an opening is settled after snapping
        a, b = add(part[0][0], part[0][1], None), add(part[-1][0], part[-1][1], None)
        edges.append((a, b)); n_pipe += 1
    n_ditch = 0
    for f, part in _lines(layers.get("ditches", [])):
        ids = [add(q[0], q[1], True) for q in part]          # an open channel: every vertex is at the surface
        edges += list(zip(ids[:-1], ids[1:])); n_ditch += 1
    if not xs:
        return None
    x, y = transform(np.asarray(xs, float), np.asarray(ys, float))
    x, y = np.asarray(x), np.asarray(y)
    n = len(x)
    pairs = cKDTree(np.c_[x, y]).query_pairs(snap, output_type="ndarray")
    e = np.vstack([np.asarray(edges, int).reshape(-1, 2), pairs.reshape(-1, 2)])
    # 1 nodes that sit on each other (within snap) become one junction
    nj, junction = connected_components(coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)), directed=False)
    state = np.full(nj, -1, int)                             # -1 only free pipe ends, 0 closed structure, 1 opening
    for i, o in enumerate(is_open):
        if o is not None:
            j = junction[i]
            state[j] = max(state[j], 1 if o else 0) if state[j] >= 0 else (1 if o else 0)
    # a structure that is closed wins over nothing, an opening wins over a closed lid at the same spot
    degree = np.bincount(junction[np.asarray(edges, int).ravel()], minlength=nj) if edges else np.zeros(nj, int)
    free_end = (state == -1) & (degree == 1)                 # a pipe that just stops: an outlet or an inlet without a record
    opening = (state == 1) | free_end
    # 2 junctions joined by pipes and ditches form networks
    nn, net = connected_components(coo_matrix((np.ones(len(e)), (junction[e[:, 0]], junction[e[:, 1]])), shape=(nj, nj)), directed=False)
    jx = np.bincount(junction, weights=x, minlength=nj) / np.bincount(junction, minlength=nj)
    jy = np.bincount(junction, weights=y, minlength=nj) / np.bincount(junction, minlength=nj)
    zone = np.zeros(nj, int)
    cut = 0
    if protected:
        from . import barriers
        zone = barriers.zone(protected, jx, jy)
        cut = int(np.isin(np.unique(net[zone > 0]), net[zone == 0]).sum())     # networks with nodes on both sides of a barrier
        _, net = np.unique(np.column_stack([net, zone]), axis=0, return_inverse=True)
        net = np.asarray(net).ravel()
    size = np.bincount(net)
    return {"x": jx, "y": jy, "net": net, "open": opening, "zone": zone,
            "counts": {"structures": n_struct, "pipes": n_pipe, "ditches": n_ditch, "junctions": int(nj), "openings": int(opening.sum()),
                       "networks": int((size > 1).sum()), "cut_at_barriers": cut}}


def links(net, dem_ft, origin, window=1):
    """The openings as cells of the DEM, for depth.connect_index: (rows, cols, network number).

    Each opening takes the lowest cell within `window` cells of it (a catch basin sits at the low point of the gutter
    and its mapped position can be a metre off). Openings off the grid are left out.
    """
    if net is None:
        return None
    keep = net["open"]
    x0, ytop = origin
    col = np.floor(net["x"][keep] - x0).astype(int)
    row = np.floor(ytop - net["y"][keep]).astype(int)
    nid = net["net"][keep]
    h, w = dem_ft.shape
    ok = (row >= window) & (row < h - window) & (col >= window) & (col < w - window)
    row, col, nid = row[ok], col[ok], nid[ok]
    best_r, best_c = row.copy(), col.copy()
    best = np.asarray(dem_ft[row, col], np.float32)
    best = np.where(np.isfinite(best), best, np.inf)
    for dr in range(-window, window + 1):
        for dc in range(-window, window + 1):
            v = np.asarray(dem_ft[row + dr, col + dc], np.float32)
            better = np.isfinite(v) & (v < best)
            best_r[better], best_c[better], best[better] = (row + dr)[better], (col + dc)[better], v[better]
    good = np.isfinite(best)
    return best_r[good], best_c[good], nid[good]
