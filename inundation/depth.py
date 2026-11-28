"""Where the water is, and how deep, at each still-water level.

The levels are the ones Derek's HUC-12 depth grids use: -2 to 10 ft NAVD88 in 0.25 ft steps (49 levels).
Whatever the source, each sampled point ends up with two numbers:

    ground_ft    the ground under it, ft NAVD88
    connect_ft   the lowest of the levels at which water reaches it from tidal water (NaN: never by 10 ft)

so its depth at any water level L is  L - ground_ft  once  L >= connect_ft, and 0 before that. The level
at which it first holds d ft of water is therefore  max(ground_ft + d, connect_ft).

Two sources:
  * a 1 m elevation model (the pilot): NOAA/USGS CoNED Chesapeake Bay topobathy, the DEM behind VIMS's
    Chesapeake_Bay_Topobathy_DEM service, read from NOAA's public bucket. Water spreads over every cell
    at or below the level that is joined (8 neighbours) to tidal water, so a low spot behind higher
    ground stays dry until the ground between is overtopped.
  * a stack of depth grids, one per level (Derek's HUC-12 grids, once hosted): `from_depth_stack` turns
    the depths sampled at a point into the same two numbers.
"""
import os

import numpy as np

LEVELS_FT = np.round(np.arange(-2.0, 10.0 + 1e-9, 0.25), 2)
M_TO_FT = 1 / 0.3048
CONED = "https://noaa-nos-coastal-lidar-pds.s3.amazonaws.com/dem/Chesapeake_Coned_update_DEM_2016_8656"
NEVER = 255


def coned_tiles(bounds):
    """URLs of the CoNED 1 m tiles (EPSG:26918, m NAVD88) that touch bounds = (xmin, ymin, xmax, ymax)."""
    import re
    import urllib.parse
    import urllib.request
    import rasterio
    root, prefix = CONED.split("/dem/")
    keys, tok = [], None
    while True:
        u = f"{root}/?list-type=2&prefix=dem/{prefix}/" + (f"&continuation-token={urllib.parse.quote(tok)}" if tok else "")
        t = urllib.request.urlopen(u, timeout=120).read().decode()
        keys += re.findall(r"<Key>([^<]+\.tif)</Key>", t)
        m = re.search(r"<NextContinuationToken>([^<]+)", t)
        if not m:
            break
        tok = m.group(1)
    out = []
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        for k in keys:
            with rasterio.open(f"/vsicurl/{root}/{k}") as r:
                b = r.bounds
                if b.left < bounds[2] and b.right > bounds[0] and b.bottom < bounds[3] and b.top > bounds[1]:
                    out.append(f"{root}/{k}")
    return out


def _read_strip(r, path, window, tries=4, wait=10):
    """Band 1 of a window of an open tile. A read cut short on its way from the bucket is read again from the tile
    opened anew, after a pause that grows with each try; the last failure is raised."""
    import time
    import rasterio
    from rasterio.errors import RasterioIOError
    for k in range(tries):
        try:
            if k == 0:
                return r.read(1, window=window)
            with rasterio.open(path) as again:
                return again.read(1, window=window)
        except RasterioIOError:
            if k == tries - 1:
                raise
            print(f"{os.path.basename(str(path))}: a strip could not be read, trying again ({k + 1} of {tries - 1})",
                  flush=True)
            time.sleep(wait * (k + 1))


def low_grid(paths, bounds, limit_ft=LEVELS_FT[-1], step=64):
    """Where the ground or the water lies at or below `limit_ft` inside `bounds`, on a grid of `step` m cells: a
    boolean array whose first row is the north edge and first column the west edge. One row of the 1 m tiles is
    read for each row of cells, whole, so nothing a cell wide is missed along it. A county that reaches from a
    tidal river to the Piedmont is mostly ground no mapped level can reach; this is the map of the part that can."""
    import rasterio
    from rasterio.windows import Window
    x0, y0, x1, y1 = [int(round(v)) for v in bounds]
    rows, cols = -(-(y1 - y0) // step), -(-(x1 - x0) // step)
    low = np.zeros((rows, cols), bool)
    limit = float(limit_ft) / M_TO_FT
    for p in paths:
        with rasterio.open(p) as r:
            left, top = int(round(r.transform.c)), int(round(r.transform.f))
            c0, c1 = max(x0, left), min(x1, left + r.width)
            if c0 >= c1:
                continue
            for k in range(rows):
                q = top - (y1 - k * step)                           # the tile's row at this cell row's north edge
                if q < 0 or q >= r.height:
                    continue
                a = _read_strip(r, p, Window(c0 - left, q, c1 - c0, 1))[0]
                ok = (a <= limit) & (a > -1000)
                if r.nodata is not None:
                    ok &= a != r.nodata
                at = np.flatnonzero(ok)
                if len(at):
                    low[k, np.unique((at + c0 - x0) // step)] = True
    return low


def tidal_low(low, big=500, join=2):
    """The parts of a low_grid that the tide can be part of, numbered from 1 (0 elsewhere): the largest patch, every
    patch that runs off the grid's edge and every patch of `big` cells or more. Patches within `join` cells of each
    other count as one, since the grid can miss a channel between two of its rows. What is left out is low ground
    shut in by high ground: a quarry floor below sea level, a pit."""
    from scipy import ndimage
    if not low.any():
        return np.zeros(low.shape, np.int32)
    lab, n = ndimage.label(ndimage.binary_dilation(low, np.ones((3, 3), bool), iterations=join), np.ones((3, 3), bool))
    lab = np.where(low, lab, 0)
    size = np.bincount(lab.ravel(), minlength=n + 1)
    size[0] = 0
    keep = np.zeros(n + 1, bool)
    keep[np.argmax(size)] = True
    keep[size >= big] = True
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    keep[edge] = True
    keep[0] = False
    return np.where(keep[lab], lab, 0).astype(np.int32)


def read_dem(paths, bounds, into=None):
    """Mosaic the tiles over bounds (whole metres). Returns ground in ft NAVD88 (NaN where no data) and
    (x of the west edge, y of the north edge); cells are 1 m. `into` is the path of a .npy file to build the grid
    in, for areas whose grid should not sit in memory; the result is then a memory map of it."""
    import rasterio
    from rasterio.windows import Window
    x0, y0, x1, y1 = [int(round(v)) for v in bounds]
    if into is None:
        out = np.full((y1 - y0, x1 - x0), np.nan, np.float32)
    else:
        out = np.lib.format.open_memmap(into, "w+", np.float32, (y1 - y0, x1 - x0))
        for r in range(0, out.shape[0], 2048):
            out[r:r + 2048] = np.nan
    for p in paths:
        with rasterio.open(p) as r:
            if abs(r.res[0] - 1) > 1e-9 or abs(r.res[1] - 1) > 1e-9:
                raise ValueError(f"{p}: cells are not 1 m")
            left, top = int(round(r.transform.c)), int(round(r.transform.f))
            c0, c1 = max(x0, left), min(x1, left + r.width)
            r0, r1 = max(top - y1, 0), min(top - y0, r.height)      # rows count down from the top
            if c0 >= c1 or r0 >= r1:
                continue
            for q0 in range(r0, r1, 4096):                           # in strips: a whole tile is several GB
                q1 = min(q0 + 4096, r1)
                a = _read_strip(r, p, Window(c0 - left, q0, c1 - c0, q1 - q0))
                bad = (a == r.nodata) | (a < -1000) if r.nodata is not None else (a < -1000)
                a = a * np.float32(M_TO_FT)
                a[bad] = np.nan
                rows = slice(y1 - (top - q0), y1 - (top - q1))
                block = out[rows, c0 - x0:c1 - x0]
                block[~bad] = a[~bad]
    return out, (x0, y1)


def tidal_seed(dem_ft, level=LEVELS_FT[0]):
    """A cell in the largest body of water at the lowest level: the tidal water everything floods from."""
    from scipy import ndimage
    lab, n = ndimage.label(dem_ft <= level, structure=np.ones((3, 3)))
    if n == 0:
        raise ValueError("no cell at or below the lowest level: nothing to flood from")
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    k = int(sizes.argmax())
    r, c = np.unravel_index(int(np.argmax(lab == k)), lab.shape)
    return (int(r), int(c)), int(sizes[k])


EDGE_BODY_M2 = 50_000      # a body of water that runs off the grid's edge is tidal water from this size up


def tidal_seeds(dem_ft, level=LEVELS_FT[0], cell_m2=1.0, min_m2=EDGE_BODY_M2):
    """One cell in each body of tidal water at the lowest level, the largest body first.

    The largest body is the sea. Every other body of at least `min_m2` that runs off the grid's edge is tidal too:
    an estuary that meets the sea outside the frame (the Piankatank north of Gloucester, whose frame holds the York
    and Mobjack Bay; the York north of James City, whose frame holds the James). Its cell is one on the edge, so
    the caller can tell where it leaves the grid. A pond inside the grid is not a seed: the sea has to reach it."""
    from scipy import ndimage
    lab, n = ndimage.label(dem_ft <= level, structure=np.ones((3, 3)))
    if n == 0:
        raise ValueError("no cell at or below the lowest level: nothing to flood from")
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    k = int(sizes.argmax())
    r, c = np.unravel_index(int(np.argmax(lab == k)), lab.shape)
    out = [(int(r), int(c))]
    h, w = lab.shape
    er = np.concatenate([np.zeros(w, int), np.full(w, h - 1), np.arange(h), np.arange(h)])
    ec = np.concatenate([np.arange(w), np.arange(w), np.zeros(h, int), np.full(h, w - 1)])
    ids, first = np.unique(lab[er, ec], return_index=True)
    for i, j in sorted(zip(ids.tolist(), first.tolist()), key=lambda q: -sizes[q[0]]):
        if i not in (0, k) and sizes[i] * cell_m2 >= min_m2:
            out.append((int(er[j]), int(ec[j])))
    return out


def seed_list(seed):
    """[(row, col), ...] from one cell or a list of them."""
    return [tuple(int(v) for v in seed)] if np.ndim(seed) == 1 else [tuple(int(v) for v in q) for q in seed]


def connect_index(dem_ft, levels=LEVELS_FT, seed=None, log=None, links=None, blocked=None, method=None, store=None):
    """For every cell, the index of the lowest level at which it is under water joined to the seed
    (NEVER where it stays dry or cut off through the top level). seed: one (row, col) or a list of them, a cell in
    each body of tidal water; default tidal_seeds (the largest body and those that run off the grid's edge).

    links=(rows, cols, network) adds the stormwater pipes (drainage.links): cells that belong to the same network
    are joined below ground, so once water covers one of them, every other one at or below the level floods the
    ground around it. With links the result is a pair: (index over the surface only, index with the pipes).
    blocked=(rows, cols) are cells water never enters or crosses: a floodwall (barriers.cells).
    method: "label" labels the wet cells once per level (scipy, whole grid in memory); "spread" visits each cell
    once (spread.py, needs numba, takes a memory-mapped DEM of any size). Default: spread when numba is installed.
    store: where spread writes its results as it goes (see spread.connect_index); the label method ignores it.
    """
    if method != "label":
        from . import spread
        if spread.njit is not None:
            return spread.connect_index(dem_ft, levels, seed, log, links, blocked, store)
        if method == "spread":
            raise ImportError("numba is not installed")
    from scipy import ndimage
    seeds = tidal_seeds(dem_ft, levels[0]) if seed is None else seed_list(seed)
    out = np.full(dem_ft.shape, NEVER, np.uint8)
    piped = np.full(dem_ft.shape, NEVER, np.uint8) if links is not None else None
    if links is not None:
        lr, lc, ln = (np.asarray(a) for a in links)
        _, ln = np.unique(ln, return_inverse=True)
    eight = np.ones((3, 3))
    for k, lev in enumerate(levels):
        low = dem_ft <= lev                                      # NaN compares False: no-data cells stay dry
        if blocked is not None and len(blocked[0]):
            low[blocked] = False
        lab, n = ndimage.label(low, structure=eight)
        del low
        sea = np.unique([lab[q] for q in seeds])
        sea = sea[sea > 0]
        wet = np.isin(lab, sea)
        out[wet & (out == NEVER)] = k
        msg = f"  level {lev:+.2f} ft: {int(wet.sum()):,} cells wet"
        if links is not None:
            nl = lab[lr, lc]                                     # the body of water each opening is in (0: dry)
            on = nl > 0
            bodies = np.zeros(n + 1, bool)
            bodies[sea] = True
            charged = np.zeros(int(ln.max()) + 1 if len(ln) else 1, bool)
            while True:                                          # water passes body -> network -> body until nothing new
                charged[ln[on & bodies[nl]]] = True
                reach = np.unique(nl[on & charged[ln]])
                if bodies[reach].all():
                    break
                bodies[reach] = True
            wetp = bodies[lab]
            piped[wetp & (piped == NEVER)] = k
            msg += f", {int(wetp.sum()) - int(wet.sum()):,} more through the pipes"
            del wetp
        if log:
            log(msg)
        del lab, wet
    return out if links is None else (out, piped)


def sample(dem_ft, cidx, origin, x, y, levels=LEVELS_FT):
    """ground_ft and connect_ft at points (nearest cell). Points off the grid give NaN."""
    x0, ytop = origin
    col = np.floor(np.asarray(x) - x0).astype(int)
    row = np.floor(ytop - np.asarray(y)).astype(int)
    ok = (row >= 0) & (row < dem_ft.shape[0]) & (col >= 0) & (col < dem_ft.shape[1])
    ground = np.full(col.shape, np.nan, np.float32)
    conn = np.full(col.shape, np.nan, np.float32)
    ground[ok] = dem_ft[row[ok], col[ok]]
    ci = cidx[row[ok], col[ok]]
    lev = np.where(ci == NEVER, np.nan, levels[np.minimum(ci, len(levels) - 1)]).astype(np.float32)
    conn[ok] = lev
    return ground, conn


def from_depth_stack(depths, levels=LEVELS_FT):
    """The same two numbers from a stack of depth grids sampled at points.

    depths: array (n levels, n points) of water depth in ft, 0 or NaN where dry. Ground is the level
    minus the depth at the lowest wet level; connect is that lowest wet level.
    """
    d = np.where(np.isfinite(depths) & (depths > 0), depths, 0.0)
    wet = d > 0
    any_wet = wet.any(axis=0)
    first = wet.argmax(axis=0)
    cols = np.arange(d.shape[1])
    ground = np.where(any_wet, levels[first] - d[first, cols], np.nan)
    connect = np.where(any_wet, levels[first], np.nan)
    return ground.astype(np.float32), connect.astype(np.float32)


def first_level(ground_ft, connect_ft, depth_ft):
    """Lowest water level (ft NAVD88) at which a point holds depth_ft of water; NaN if it never does."""
    return np.where(np.isfinite(connect_ft), np.maximum(ground_ft + depth_ft, connect_ft), np.nan)
