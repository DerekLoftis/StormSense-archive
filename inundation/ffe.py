"""First floor elevations from elevation certificates.

Virginia has no statewide first floor elevations, so damage.py assumes a height above the ground. Where a building
has a FEMA elevation certificate that the Hampton Roads Planning District Commission has keyed in, the surveyed
finished first floor elevation is used instead. HRPDC's layer holds about 5,300 building footprints from ten
localities (Chesapeake, Franklin, Hampton, James City, Newport News, Norfolk, Portsmouth, Southampton, Virginia
Beach, York; Poquoson and Gloucester are in it too), all in ft NAVD88.

Over the homes in the layer the median first floor height above grade is 3.4 ft before the first flood map and
3.9 ft after it (1,011 and 2,396 certificates, read 2026-11-26), close to the 3 and 4 ft Hazus defaults that
damage.py assumes for the rest.
"""
import numpy as np

URL = "https://geo.hrsd.com/hrgeo/rest/services/regionalgis/ElevationCertificatesNAVD88/MapServer/0"
SOURCE = "Hampton Roads Elevation Certificates (NAVD 88), HRPDC (geo.hrsd.com)"
FIELDS = "BLDG_USE,BLDG_DIAGRAM,FFE,FFH,LAG,LENGTH_UNIT,ISSUE_DATE,FIRM_STATUS"
NEAR_M = 3.0            # a certificate footprint that misses every VGIN building by more than this is left out


def fetch(log=None):
    """Every certificate footprint of the region as GeoJSON features (the layer is small: three pages)."""
    from . import vgin
    return vgin.query(URL, "FFE IS NOT NULL", FIELDS, log)


def _ffe(p):
    try:
        v = float(p.get("FFE"))
    except (TypeError, ValueError):
        return np.nan
    if str(p.get("LENGTH_UNIT") or "Feet").strip().lower().startswith("m"):
        v /= 0.3048
    return v if -5 < v < 60 else np.nan


def attach(certs, bgeoms, transform):
    """Finished first floor elevation (ft NAVD88) per building, NaN where it has no certificate.

    A certificate goes to the building that holds the middle of its footprint, or else the nearest one within
    NEAR_M. Several certificates on one building: the latest issue date wins.
    """
    import shapely
    from shapely.geometry import shape
    out = np.full(len(bgeoms), np.nan)
    pts, val, when = [], [], []
    for f in certs:
        v = _ffe(f.get("properties") or {})
        if not np.isfinite(v) or not f.get("geometry"):
            continue
        try:
            g = shapely.transform(shapely.force_2d(shape(f["geometry"])), lambda c: np.column_stack(transform(c[:, 0], c[:, 1])))
        except Exception:
            continue
        if g.is_empty:
            continue
        pts.append(shapely.make_valid(g).representative_point()); val.append(v)
        when.append(float(f["properties"].get("ISSUE_DATE") or 0))
    idx = np.flatnonzero([g is not None and not g.is_empty for g in bgeoms])
    if not pts or not len(idx):
        return out
    tree = shapely.STRtree([bgeoms[i] for i in idx])
    near = tree.query_nearest(pts, max_distance=NEAR_M, all_matches=False)
    order = np.argsort(np.asarray(when)[near[0]], kind="stable")          # later certificates overwrite earlier ones
    out[idx[near[1][order]]] = np.asarray(val)[near[0][order]]
    return out
