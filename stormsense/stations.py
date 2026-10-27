"""StormSense's station table (stations.csv): one row per gauge with where its observations come
from, its datums and the forecast guidance points that cover it.

    station                  StormSense id (Tidewatch's where Tidewatch had one)
    obs                      observation sources tried in order, ';'-separated, each source:id:
                             coops:<NOS id>, nwis:<USGS site> (62620, ft NAVD88), thredds:<OPeNDAP url>,
                             archive:<folder>/<id> (FloodVISION, GreenStream archives), nwps:<NWS id>
    navd88_above_mllw_ft     NAVD88 zero above the station's MLLW (NOAA datums or VDatum)
    stage_datum, stage_to_mllw_ft, datum_change_utc, stage_to_mllw_before_ft
                             for nwps: MLLW = NWPS stage + stage_to_mllw_ft (before datum_change_utc,
                             + stage_to_mllw_before_ft: the gauge switched datums)
    nws_lid                  NWS location id (NWPS forecasts and flood categories, STOFS/NWM SHEF)
    petss_point              the P-ETSS station CSV whose surge applies (NOS id or "est" point)
    tidewatch_id             web4 Tidewatch id whose 36-hour forecast is an ensemble member
    alias_of                 the same gauge as this station (observations copied)
    suspect_obs              yes: Tidewatch published another gauge's record under this id; scored,
                             never used to pick guidance
    added                    date the station joined StormSense
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TABLE = os.path.join(HERE, "stations.csv")
# Retired 2026-10-15 (Derek): never reintroduce
RETIRED = {f"NN{i:02d}" for i in range(1, 11)} | {f"VB{i:02d}" for i in range(1, 11)} | {"USNW", "USPF", "USSV"}


def _f(x):
    return float(x) if x not in ("", None) else None


def load(path=TABLE):
    """{station: row dict} with floats parsed and the obs chain split into [(source, id)]."""
    out = {}
    for r in csv.DictReader(open(path)):
        st = r["station"]
        if st in RETIRED:
            continue
        r["navd88"] = _f(r["navd88_above_mllw_ft"])
        r["lat_f"] = _f(r["lat"])
        r["lon_f"] = _f(r["lon"])
        r["chain"] = [tuple(s.split(":", 1)) for s in r["obs"].split(";") if s]
        r["stage_to_mllw"] = _f(r["stage_to_mllw_ft"]) or 0.0
        r["stage_to_mllw_before"] = _f(r["stage_to_mllw_before_ft"])
        out[st] = r
    return out


def datums(st):
    """Tidal datums ft above the station's MLLW (HAT, MHHW, MSL, MLLW, LAT, NAVD88; '_estimated'
    lists values estimated rather than published), or {}."""
    p = os.path.join(DATA, "datums", f"{st}.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def thresholds_mllw(info, t):
    """NWS flood categories {action, minor, moderate, major} in ft above the station's MLLW, from an
    NWPS threshold entry {"datum": "MLLW"|"NAVD88"|"gauge", ...} in the gauge's own datum."""
    if not t:
        return {}
    d = t.get("datum", "MLLW")
    if d == "MLLW":
        off = 0.0
    elif d == "NAVD88":
        if info.get("navd88") is None:
            return {}
        off = info["navd88"]
    else:  # a local gauge datum: the same conversion as its NWPS stage
        off = info["stage_to_mllw"]
    return {k: round(t[k] + off, 3) for k in ("action", "minor", "moderate", "major") if t.get(k) is not None}
