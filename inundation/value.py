"""Year built and assessed value for each building, from the locality's parcels and assessment roll.

VGIN's statewide footprints and parcels carry no values (the parcels have only the locality's parcel id), so the
numbers come from each locality's own open data. value_sources.json says, per locality FIPS, where the parcel
polygons and the assessment table are and what their columns are called. A building takes the parcel its middle
falls in; where a parcel holds several buildings its improvement (building) value is shared between them by
footprint area.

The second opinion on value is Zillow's Home Value Index (ZHVI, the typical home value of the middle third of homes)
for the building's ZIP code: Zillow publishes that openly, per ZIP and month. It does not publish its estimate for
single addresses for bulk use, so this is the ZIP's typical home, not this house.
    https://www.zillow.com/research/data/  (Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv)
"""
import csv
import io
import json
import os
import re
import urllib.parse
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ZHVI_URL = "https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"


def sources(path=None):
    """value_sources.json by locality FIPS. An entry with "like": "<key>" starts from that entry (keys beginning
    with "_" are shared templates) and overrides it one level deep."""
    with open(path or os.path.join(HERE, "value_sources.json")) as f:
        raw = json.load(f)
    out = {}
    for k, v in raw.items():
        base = raw.get(v.get("like"), {})
        m = {**base, **v}
        for kk in base:
            if isinstance(base[kk], dict) and isinstance(v.get(kk), dict):
                m[kk] = {**base[kk], **v[kk]}
        out[k] = m
    return out


def fetch_parcels(src, log=None):
    from . import vgin
    p = src["parcels"]
    feats = vgin.query(p["url"], p.get("where", "1=1"), p.get("fields", p["key"]), log)
    n = vgin.count(p["url"], p.get("where", "1=1"))
    if len(feats) != n:
        raise RuntimeError(f"parcels: got {len(feats)} of {n}")
    return feats


def records_from_parcels(parcels, src):
    """The same records where the parcel layer itself carries the values (assessment type "parcels")."""
    cols = src["assessment"]["columns"]
    return [{ours: f["properties"].get(theirs, "") for ours, theirs in cols.items()} for f in parcels]


def fetch_use(src):
    """{parcel key: use code} from a second layer, for a locality whose value layer has no use or zoning
    ("use_from": {"url", "key", "use"}; Chesapeake: the city's own parcels carry the property class)."""
    from . import vgin
    u = src["use_from"]
    feats = vgin.query(u["url"], u.get("where", "1=1"), f"{u['key']},{u['use']}", geometry=False)
    return {str(f["properties"].get(u["key"]) or "").strip(): str(f["properties"].get(u["use"]) or "").strip() for f in feats}


def add_use(records, uses):
    """Put the use codes of fetch_use on the records that have none."""
    for r in records:
        if not str(r.get("use") or "").strip():
            r["use"] = uses.get(str(r.get("key") or "").strip(), "")
    return records


def fetch_assessment(src):
    """The assessment roll as a list of dicts with our column names (see value_sources.json "columns")."""
    a = src["assessment"]
    cols = a["columns"]
    if a["type"] != "socrata":
        raise ValueError(f"unknown assessment type {a['type']}")
    q = {"$select": ",".join(sorted(set(cols.values()))), "$limit": 1000000, "$order": cols["key"]}
    req = urllib.request.Request(a["url"] + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "python-urllib"})
    with urllib.request.urlopen(req, timeout=600) as r:
        text = r.read().decode("utf-8", "replace")
    return [{ours: row.get(theirs, "") for ours, theirs in cols.items()} for row in csv.DictReader(io.StringIO(text))]


def fetch_zhvi(state="VA"):
    """{zip: (typical home value, 'YYYY-MM')} for one state from Zillow's public ZHVI file (about 120 MB)."""
    req = urllib.request.Request(ZHVI_URL, headers={"User-Agent": "python-urllib"})
    out = {}
    with urllib.request.urlopen(req, timeout=900) as r:
        rd = csv.reader(io.TextIOWrapper(r, "utf-8"))
        h = next(rd)
        si, zi = h.index("State"), h.index("RegionName")
        first = max(i for i, c in enumerate(h) if not re.match(r"\d{4}-\d\d-\d\d", c)) + 1
        for x in rd:
            if x[si] != state:
                continue
            for j in range(len(h) - 1, first - 1, -1):
                if x[j]:
                    out[x[zi].zfill(5)] = (round(float(x[j])), h[j][:7])
                    break
    return out


def _num(s):
    try:
        v = float(str(s).replace(",", "").replace("$", ""))
        return v if np.isfinite(v) else np.nan
    except (TypeError, ValueError):
        return np.nan


def roll(records, src):
    """One row per parcel key from the assessment records (a parcel can have several: condominium units, accounts).

    Values are summed; the year built, use and class come from the record with the largest improvement value."""
    res_words = [w.lower() for w in src.get("residential_uses", [])]
    res_pats = [re.compile(p, re.I) for p in src.get("residential_patterns", [])]     # for localities with zoning codes only
    kinds = [(k, [re.compile(p, re.I) for p in src.get(k + "_classes", [])]) for k in ("split_level", "two_story", "one_story")]
    out = {}
    for r in records:
        k = str(r.get("key", "")).strip()
        if not k:
            continue
        impr, total, year = _num(r.get("improvement_value")), _num(r.get("total_value")), _num(r.get("year_built"))
        o = out.setdefault(k, {"impr": 0.0, "total": 0.0, "best": -1.0, "year": np.nan, "use": "", "cls": "", "zip": "", "living": 0.0, "n": 0})
        o["impr"] += impr if impr > 0 else 0.0
        o["total"] += total if total > 0 else 0.0
        o["living"] += max(_num(r.get("living_area")), 0) if np.isfinite(_num(r.get("living_area"))) else 0.0
        o["n"] += 1
        if (impr if np.isfinite(impr) else 0.0) > o["best"]:
            o["best"] = impr if np.isfinite(impr) else 0.0
            o["year"] = year if 1600 < year < 2100 else np.nan
            o["use"], o["cls"] = str(r.get("use") or "").strip(), str(r.get("class") or "").strip()
            o["zip"] = str(r.get("zip") or "").strip()[:5]
    for o in out.values():
        u = o["use"].lower()
        o["residential"] = bool(u) and (any(w in u for w in res_words) or any(p.search(o["use"]) for p in res_pats))
        o["curve"] = next((k for k, pats in kinds if any(p.search(o["cls"]) for p in pats)), "")
    return out


def attach(bgeoms, parcels, transform, rolled, src, zhvi=None):
    """Columns for the building table: parcel key, use, year built, which depth-damage curve fits (one story, two or
    more, split level), values, ZIP and the ZIP's Zillow value."""
    import shapely
    from shapely.geometry import shape
    n = len(bgeoms)
    key = src["parcels"]["key"]
    pg, pk = [], []
    for f in parcels:
        try:
            g = shapely.transform(shapely.force_2d(shape(f["geometry"])), lambda c: np.column_stack(transform(c[:, 0], c[:, 1])))
        except Exception:
            continue
        if g.is_empty:
            continue
        pg.append(shapely.make_valid(g)); pk.append(str(f["properties"].get(key, "")).strip())
    tree = shapely.STRtree(pg)
    pts = [g.representative_point() if g is not None and not g.is_empty else None for g in bgeoms]
    have = np.array([p is not None for p in pts])
    bi, pi = tree.query([p for p in pts if p is not None], predicate="within")
    parcel = np.full(n, -1)
    parcel[np.flatnonzero(have)[bi]] = pi                       # overlapping parcels: the last one wins
    area = np.array([g.area if g is not None else 0.0 for g in bgeoms])
    on = parcel >= 0
    parcel_area = np.bincount(parcel[on], weights=area[on], minlength=len(pg))
    cols = {"parcel": np.full(n, "", object), "use": np.full(n, "", object), "year_built": np.full(n, np.nan),
            "curve": np.full(n, "", object), "residential": np.zeros(n, int), "value_total": np.full(n, np.nan),
            "value_impr": np.full(n, np.nan), "value_bldg": np.full(n, np.nan), "zip": np.full(n, "", object),
            "zhvi": np.full(n, np.nan)}
    for i in np.flatnonzero(on):
        k = pk[parcel[i]]
        cols["parcel"][i] = k
        o = rolled.get(k)
        if o is None:
            continue
        share = area[i] / parcel_area[parcel[i]] if parcel_area[parcel[i]] > 0 else 1.0
        curve = o["curve"]
        if not curve and o["living"] > 0 and parcel_area[parcel[i]] > 0:    # living area against the footprints, in ft2
            curve = "two_story" if o["living"] / (parcel_area[parcel[i]] * 10.7639) >= 1.6 else "one_story"
        cols["use"][i], cols["year_built"][i], cols["curve"][i] = o["use"], o["year"], curve or "one_story"
        cols["residential"][i] = int(o["residential"] and area[i] <= src.get("home_max_footprint_m2", np.inf))
        cols["value_total"][i] = o["total"] if o["total"] > 0 else np.nan
        cols["value_impr"][i] = o["impr"] if o["impr"] > 0 else np.nan
        cols["value_bldg"][i] = round(o["impr"] * share) if o["impr"] > 0 else np.nan
        cols["zip"][i] = o["zip"]
        if zhvi and o["residential"] and o["zip"] in zhvi:
            cols["zhvi"][i] = zhvi[o["zip"]][0]
    return cols
