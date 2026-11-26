"""The same answer as depth.connect_index in one pass over the grid, for areas too large to label 49 times.

depth.connect_index labels the wet cells once per water level, which needs the whole grid several times in memory:
fine for Norfolk (280 million cells), not for Virginia Beach or Suffolk (well over a billion). Here every cell first
gets its own level number (the lowest level at or above its ground). The cells are then added level by level, in
row order, and joined to the neighbours (and storm drain networks) already there; a body of water is marked with the
level at which it first touches the sea. Each cell is added once and memory is read almost in sequence. Six bytes
per cell are kept in memory and the DEM stays on disk.

Needs numba (in inundation/requirements.txt); depth.connect_index uses this when numba is there and labels otherwise.
"""
import numpy as np

DRY = 127            # own level number of a cell the water never covers: above the top level, no data, or a wall
OPENING = 128        # flag on a cell that is an opening of a drainage network
NEVER = 255

try:
    from numba import njit
except ImportError:                                  # pragma: no cover
    njit = None


def own_level(dem_ft, levels, blocked=None, rows=2048):
    """uint8 per cell: the number of the lowest level at or above the ground (DRY where none). Reads the DEM in
    strips, so a memory-mapped DEM never has to fit in memory."""
    lev = np.asarray(levels, np.float64)
    if len(lev) >= DRY:
        raise ValueError("too many levels")
    h, w = dem_ft.shape
    g = np.empty((h, w), np.uint8)
    for r in range(0, h, rows):
        a = np.asarray(dem_ft[r:r + rows], np.float64)
        k = np.searchsorted(lev, a, side="left")          # NaN sorts last: beyond the top level
        g[r:r + rows] = np.where(k >= len(lev), DRY, k)
    if blocked is not None and len(blocked[0]):
        g[blocked] = DRY
    return g


def seed_cell(dem_ft, level, step=8):
    """A cell of the largest body of water at the lowest level, found on every `step`-th cell of a large grid."""
    from . import depth
    if dem_ft.size <= 50_000_000:
        return depth.tidal_seed(np.asarray(dem_ft), level)[0]
    (r, c), _ = depth.tidal_seed(np.asarray(dem_ft[::step, ::step]), level)
    return r * step, c * step


if njit is not None:
    @njit(cache=True)
    def _find(parent, i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]            # path halving
            i = parent[i]
        return i

    @njit(cache=True)
    def _join(parent, flag, vflag, n, a, b, k):
        """An edge between two nodes that are both under water at level k. Bodies not yet joined to the sea merge;
        a body that touches one already joined is marked as joined at this level (and is not merged into it, so
        that each body keeps the level at which the sea first reached it)."""
        ra = _find(parent, a)
        rb = _find(parent, b)
        if ra == rb:
            return
        fa = flag[ra] if ra < n else vflag[ra - n]
        fb = flag[rb] if rb < n else vflag[rb - n]
        if fa == 255 and fb == 255:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb
        elif fa == 255:
            if ra < n:
                flag[ra] = k
            else:
                vflag[ra - n] = k
        elif fb == 255:
            if rb < n:
                flag[rb] = k
            else:
                vflag[rb - n] = k

    @njit(cache=True)
    def _spread(g, out, parent, seed_r, seed_c, nlev, cells, nets, n_nets, use_links):
        """Fills `out` with the level number at which the sea first reaches each cell (255: never).

        Cells are added level by level in row order, each joined to the neighbours already there, so memory is
        read almost in sequence. `out` holds the bodies' marks while this runs and the answer at the end."""
        h, w = g.shape
        n = h * w
        flag = out.reshape(n)
        gf = g.reshape(n)
        vflag = np.full(max(n_nets, 1), 255, np.uint8)
        for i in range(n_nets):
            parent[n + i] = n + i
        seed = seed_r * w + seed_c
        if (gf[seed] & 127) != 0:
            return 0
        for k in range(nlev):
            for r in range(h):
                base = r * w
                for c in range(w):
                    if (g[r, c] & 127) != k:
                        continue
                    cell = base + c
                    parent[cell] = cell
                    for dr in range(-1, 2):
                        rr = r + dr
                        if rr < 0 or rr >= h:
                            continue
                        for dc in range(-1, 2):
                            cc = c + dc
                            if cc < 0 or cc >= w:
                                continue
                            nb = rr * w + cc
                            gi = g[rr, cc] & 127
                            if gi < k or (gi == k and nb < cell):       # already there
                                _join(parent, flag, vflag, n, cell, nb, k)
                    if use_links and (g[r, c] & 128) != 0:              # an opening: joined to its network
                        lo = 0
                        hi = len(cells)
                        while lo < hi:
                            mid = (lo + hi) // 2
                            if cells[mid] < cell:
                                lo = mid + 1
                            else:
                                hi = mid
                        while lo < len(cells) and cells[lo] == cell:
                            _join(parent, flag, vflag, n, cell, n + nets[lo], k)
                            lo += 1
            if k == 0:
                root = _find(parent, seed)
                flag[root] = 0
        wet = 0
        for cell in range(n):
            if (gf[cell] & 127) == 127:
                flag[cell] = 255
            else:
                root = _find(parent, cell)
                flag[cell] = flag[root] if root < n else vflag[root - n]
                if flag[cell] != 255:
                    wet += 1
        return wet


def connect_index(dem_ft, levels, seed=None, log=None, links=None, blocked=None):
    """See depth.connect_index: the index over the surface, or (surface, with pipes) when links are given."""
    if njit is None:
        raise ImportError("numba is not installed")
    h, w = dem_ft.shape
    g = own_level(dem_ft, levels, blocked)
    if seed is None:
        seed = seed_cell(dem_ft, levels[0])
    n_nets = 0
    if links is not None:
        lr, lc, ln = (np.asarray(a) for a in links)
        _, net = np.unique(ln, return_inverse=True)
        net = np.asarray(net).ravel().astype(np.int64)
        n_nets = int(net.max()) + 1 if len(net) else 0
    if h * w + n_nets >= 2 ** 32:
        raise ValueError("the grid has more than 4.29 billion cells: split the area")
    parent = np.empty(h * w + n_nets, np.uint32)
    empty = np.zeros(0, np.int64)
    out = np.full((h, w), NEVER, np.uint8)
    n = _spread(g, out, parent, seed[0], seed[1], len(levels), empty, empty, 0, False)
    if log:
        log(f"  over the surface: {n:,} cells reached through {levels[-1]:+.2f} ft")
    if links is None:
        return out
    cell = lr.astype(np.int64) * w + lc
    order = np.argsort(cell, kind="stable")
    g[lr, lc] |= OPENING
    piped = np.full((h, w), NEVER, np.uint8)
    m = _spread(g, piped, parent, seed[0], seed[1], len(levels), cell[order], net[order], n_nets, True)
    if log:
        log(f"  with the pipes: {m - n:,} more cells")
    return out, piped
