"""Roads, building footprints and boundaries from VGIN's statewide layers (Virginia Base Mapping Program).

    Road centerlines (RCL)   .../VA_Base_Layers/VBMP_RCL/FeatureServer/5   (the full set; 1, 2 and 4 are scale subsets)
    Building footprints      .../VA_Base_Layers/VA_Building_Footprints/FeatureServer/0
    Cities and counties      .../VA_Base_Layers/VA_Admin_Boundaries/FeatureServer/1

A locality is asked for by its five-digit FIPS (Norfolk 51710). Geometry comes back as GeoJSON (WGS84).
The statewide downloads, for when the whole state is prepared at once:
    https://vginmaps.vdem.virginia.gov/download/BaseMapData/SHP/VirginiaRoadCenterline.shp.zip
"""
import json
import time
import urllib.parse
import urllib.request

BASE = "https://vginmaps.vdem.virginia.gov/arcgis/rest/services/VA_Base_Layers"
ROADS = f"{BASE}/VBMP_RCL/FeatureServer/5"
BUILDINGS = f"{BASE}/VA_Building_Footprints/FeatureServer/0"
LOCALITIES = f"{BASE}/VA_Admin_Boundaries/FeatureServer/1"
ROAD_FIELDS = "OBJECTID,RCL_ID,ST_FULL,MTFCC,SEG_TYPE,SEG_EXISTS,VDOT_RNAM,VDOT_FC,MFIPS"
BUILDING_FIELDS = "OBJECTID,SOURCEFEATUREID,BUILDINGCLASS,NUMSTORIES,FIPS"
USA_STRUCTURES = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/USA_Structures_View/FeatureServer/0"
USA_STRUCTURES_NAME = "FEMA USA Structures (Oak Ridge National Laboratory)"
PAGE = 2000
UA = {"User-Agent": "StormSense (VIMS)"}     # James City's server answers 403 to a client that does not name itself


def _get(url, params, tries=4):
    data = urllib.parse.urlencode(params).encode()
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=180) as r:
                d = json.loads(r.read())
            if isinstance(d, dict) and d.get("error"):
                raise RuntimeError(str(d["error"]))
            return d
        except Exception as e:
            if k == tries - 1:                               # name the layer: "HTTP Error 404" alone does not say whose
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(3 * (k + 1))


def _with_oid(fields):
    return fields if "OBJECTID" in fields.split(",") or fields == "*" else "OBJECTID," + fields


def query(layer, where, fields, log=None, geometry=True):
    """Every feature matching `where`, paged by OBJECTID so nothing is skipped or repeated. Pages until a request
    comes back empty, because servers cap a page at their own size (1000 or 2000) and do not always say so.
    geometry=False asks for the attributes only."""
    out, last = [], -1
    while True:
        d = _get(f"{layer}/query", {
            "where": f"({where}) AND OBJECTID > {last}", "outFields": _with_oid(fields), "orderByFields": "OBJECTID",
            "resultRecordCount": PAGE, "outSR": 4326, "geometryPrecision": 7, "f": "geojson",
            "returnGeometry": "true" if geometry else "false"})
        feats = d.get("features", [])
        if not feats:
            break
        out += feats
        last = max(f["properties"].get("OBJECTID", f.get("id")) for f in feats)
        if log:
            log(f"  {len(out)} features")
    return out


def count(layer, where):
    return _get(f"{layer}/query", {"where": where, "returnCountOnly": "true", "f": "json"})["count"]


def roads(fips, log=None):
    where = f"MFIPS='{fips}' OR FIPS_L='{fips}' OR FIPS_R='{fips}'"
    feats = query(ROADS, where, ROAD_FIELDS, log)
    n = count(ROADS, where)
    if len(feats) != n:
        raise RuntimeError(f"roads: got {len(feats)} of {n}")
    return feats


def _as_usa(feats, fips):
    for f in feats:
        p = f["properties"]
        f["properties"] = {"OBJECTID": p.get("OBJECTID", f.get("id")), "SOURCEFEATUREID": str(p.get("BUILD_ID") or ""),
                           "BUILDINGCLASS": None, "NUMSTORIES": None, "FIPS": fips, "FOOTPRINTS": USA_STRUCTURES_NAME}
    return feats


NEAR_DEG = 0.00003      # about 3 m: an outline this close to a footprint is the same building drawn from other imagery


def untouched(extra, feats, near=NEAR_DEG):
    """The features of `extra` whose outline comes within `near` degrees of none of `feats`."""
    import numpy as np
    import shapely
    from shapely.geometry import shape

    def geoms(fs):
        return shapely.make_valid(np.array([shape(f["geometry"]) if f.get("geometry") else None for f in fs], object))
    if not extra or not feats:
        return [f for f in extra if f.get("geometry")]
    mine = geoms(feats)
    tree = shapely.STRtree(mine[~shapely.is_missing(mine)])
    hit = set(tree.query(geoms(extra), predicate="dwithin", distance=near)[0].tolist())
    return [f for i, f in enumerate(extra) if i not in hit and f.get("geometry")]


def buildings(fips, log=None):
    """The locality's building footprints from VGIN, plus the outlines of FEMA's USA Structures (ORNL, from imagery;
    450 square feet and larger) that come within 3 m of no VGIN footprint, with VGIN's property names and
    `FOOTPRINTS` saying where those came from. VGIN's layer has gaps: none for King and Queen County (2026-11),
    4,056 footprints in Northumberland where FEMA maps 14,101 structures (11,825 of them away from any footprint),
    and FEMA structures without a footprint are 6,246 in King William, 6,142 in Gloucester, 5,001 in Mathews and
    5 to 17% of FEMA's count in the counties around them. They are other buildings, not the same ones drawn to one
    side: in Mathews the median distance to the nearest footprint is 28 m. VGIN keeps the sheds FEMA leaves out.
    If FEMA's service cannot be read the log warns and VGIN's footprints stand alone."""
    where = f"FIPS='{fips}'"
    n = count(BUILDINGS, where)
    feats = query(BUILDINGS, where, BUILDING_FIELDS, log) if n else []
    if len(feats) != n:
        raise RuntimeError(f"buildings: got {len(feats)} of {n}")
    try:
        m = count(USA_STRUCTURES, where)
        more = query(USA_STRUCTURES, where, "OBJECTID,BUILD_ID,FIPS", log) if m else []
        if len(more) != m:
            raise RuntimeError(f"buildings (USA Structures): got {len(more)} of {m}")
    except RuntimeError as e:
        if n == 0:
            raise
        if log:
            log(f"  WARNING: FEMA USA Structures could not be read ({str(e)[:160]}): VGIN's footprints only")
        return feats
    more = _as_usa(untouched(more, feats), fips)
    if log and more:
        log(f"  VGIN has {n} footprints for {fips} and FEMA maps {m} structures: {len(more)} outlines away from any "
            f"VGIN footprint added from {USA_STRUCTURES_NAME}")
    return feats + more


def locality(fips):
    """The locality's boundary as one feature. The layer holds some localities in several rows (Prince George County
    is a sliver of 2 ha and the county itself): their polygons are put together."""
    feats = query(LOCALITIES, f"STCOFIPS='{fips}'", "STCOFIPS,NAME,NAMELSAD")
    if not feats:
        raise RuntimeError(f"no locality {fips}")
    return one_feature(feats)


def one_feature(feats):
    """Several polygon features as one MultiPolygon feature with the first one's properties; one feature as it is."""
    if len(feats) == 1:
        return feats[0]
    polys = []
    for f in feats:
        g = f["geometry"]
        polys += g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    polys.sort(key=lambda poly: -len(poly[0]))               # the main body first: prep reads the zone off it
    return {"type": "Feature", "properties": dict(feats[0]["properties"]),
            "geometry": {"type": "MultiPolygon", "coordinates": polys}}
