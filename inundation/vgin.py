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
        except Exception:
            if k == tries - 1:
                raise
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


def buildings(fips, log=None):
    where = f"FIPS='{fips}'"
    feats = query(BUILDINGS, where, BUILDING_FIELDS, log)
    n = count(BUILDINGS, where)
    if len(feats) != n:
        raise RuntimeError(f"buildings: got {len(feats)} of {n}")
    return feats


def locality(fips):
    feats = query(LOCALITIES, f"STCOFIPS='{fips}'", "STCOFIPS,NAME,NAMELSAD")
    if not feats:
        raise RuntimeError(f"no locality {fips}")
    return feats[0]
