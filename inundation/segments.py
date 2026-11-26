"""Cut road centerlines into short pieces (5 m) so each piece can be tested against the water on its own.

Each centerline part is cut into equal pieces no longer than `piece` metres (a 23 m segment gives five
4.6 m pieces), following the line's own vertices. Every piece also gets `nsamp` sample points spread
along it, where the ground level is read.
"""
import numpy as np

from . import PIECE_M

NSAMP = 5


def _parts(geom):
    if geom is None:
        return []
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


def split(features, transform, piece=PIECE_M, nsamp=NSAMP, keep=("OBJECTID", "RCL_ID", "ST_FULL", "MTFCC", "SEG_TYPE")):
    """features: GeoJSON road features (lon, lat). transform(lon, lat) -> x, y in metres.

    Returns a dict of arrays, one row per piece: road (index into `features`), part, seq, length_m,
    x0, y0, x1, y1 (ends), xm, ym (middle), sx, sy (nsamp sample points each), plus `lines`, a list of
    (k, 2) vertex arrays in metres for drawing, and the kept attributes repeated per piece.
    """
    frac = (np.arange(nsamp) + 0.5) / nsamp
    road, part, seq, length, ends, mids, sx, sy, lines = [], [], [], [], [], [], [], [], []
    for i, f in enumerate(features):
        for j, coords in enumerate(_parts(f.get("geometry"))):
            c = np.asarray(coords, float)[:, :2]
            if len(c) < 2:
                continue
            x, y = transform(c[:, 0], c[:, 1])
            xy = np.column_stack([x, y])
            d = np.hypot(*np.diff(xy, axis=0).T)
            ok = np.concatenate([[True], d > 1e-6])        # drop repeated vertices
            xy = xy[ok]
            if len(xy) < 2:
                continue
            cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
            total = cum[-1]
            n = max(1, int(np.ceil(total / piece - 1e-9)))
            cuts = np.linspace(0.0, total, n + 1)
            cx, cy = np.interp(cuts, cum, xy[:, 0]), np.interp(cuts, cum, xy[:, 1])
            sd = cuts[:-1, None] + frac[None, :] * (total / n)
            sx.append(np.interp(sd, cum, xy[:, 0])); sy.append(np.interp(sd, cum, xy[:, 1]))
            md = (cuts[:-1] + cuts[1:]) / 2
            mids.append(np.column_stack([np.interp(md, cum, xy[:, 0]), np.interp(md, cum, xy[:, 1])]))
            ends.append(np.column_stack([cx[:-1], cy[:-1], cx[1:], cy[1:]]))
            # vertices of the original line strictly inside each piece
            lo = np.searchsorted(cum, cuts[:-1], side="right")
            hi = np.searchsorted(cum, cuts[1:], side="left")
            for k in range(n):
                inner = xy[lo[k]:hi[k]]
                lines.append(np.vstack([[cx[k], cy[k]], inner, [cx[k + 1], cy[k + 1]]]) if len(inner)
                             else np.array([[cx[k], cy[k]], [cx[k + 1], cy[k + 1]]]))
            road.append(np.full(n, i)); part.append(np.full(n, j)); seq.append(np.arange(n))
            length.append(np.full(n, total / n))
    out = {"road": np.concatenate(road), "part": np.concatenate(part), "seq": np.concatenate(seq),
           "length_m": np.concatenate(length), "sx": np.vstack(sx), "sy": np.vstack(sy), "lines": lines}
    e, m = np.vstack(ends), np.vstack(mids)
    out.update(x0=e[:, 0], y0=e[:, 1], x1=e[:, 2], y1=e[:, 3], xm=m[:, 0], ym=m[:, 1])
    for k in keep:
        vals = np.array([f["properties"].get(k) for f in features], dtype=object)
        out[k] = vals[out["road"]]
    return out
