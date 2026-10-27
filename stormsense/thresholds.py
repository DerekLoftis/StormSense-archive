"""NWS flood categories (Action, Minor, Moderate, Major) for every StormSense station with an NWS
gauge, from the National Water Prediction Service gauge API, in the gauge's own datum and in ft
above the station's MLLW.

    python -m stormsense.thresholds [out.json]

The gauge's datum: the station table's stage_datum where it gives one (gauges whose NWPS datum
entry is wrong or that read on a local gauge datum); otherwise MLLW when NWPS names the gauge
"(IN MLLW)" or lists MLLW* as its zero, NAVD88 when it lists a NAVD88 datum. Categories NWPS
leaves unset (-9999) are omitted. When NWPS can't be reached the last good table
(data/thresholds_fallback.json) is used.
"""
import concurrent.futures
import json
import os
import sys
import urllib.request

from . import stations as S

API = "https://api.water.noaa.gov/nwps/v1/gauges/"
FALLBACK = os.path.join(S.DATA, "thresholds_fallback.json")


def gauge(lid):
    req = urllib.request.Request(API + lid, headers={"User-Agent": "StormSense (VIMS)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def from_gauge(g, stage_datum=""):
    cats = g["flood"]["categories"]
    vert = {v["abbrev"]: v["value"] for v in ((g.get("datums") or {}).get("vertical") or {}).get("value") or []}
    if stage_datum in ("MLLW", "NAVD88", "gauge"):
        datum = stage_datum
    elif "(IN MLLW)" in g["name"].upper() or vert.get("MLLW*") == 0:
        datum = "MLLW"
    elif "NAVD88" in vert:
        datum = "NAVD88"
    else:
        return None
    out = {"lid": g["lid"], "name": g["name"], "datum": datum, "units": g["flood"]["stageUnits"]}
    for k in ("action", "minor", "moderate", "major"):
        v = (cats.get(k) or {}).get("stage")
        if v is not None and v > -999:
            out[k] = v
    return out if len(out) > 4 else None


def build(table=None):
    """{station: entry} with each entry's categories also as 'mllw': {category: ft above MLLW}."""
    table = table or S.load()
    want = {st: r for st, r in table.items() if r["nws_lid"] and not r["alias_of"]}
    out, failed = {}, []

    def one(item):
        st, r = item
        return st, from_gauge(gauge(r["nws_lid"]), r["stage_datum"])

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(one, it): it[0] for it in want.items()}
        for f in concurrent.futures.as_completed(futs):
            try:
                st, t = f.result()
                if t:
                    out[st] = t
            except Exception as e:
                failed.append(f"{futs[f]}: {e}")
    if len(out) < len(want) // 3:
        old = json.load(open(FALLBACK))
        print(f"thresholds: NWPS gave {len(out)} of {len(want)} gauges ({'; '.join(failed[:3])}); using the saved table")
        out = {k: v for k, v in old.items() if k in table}
    for st, t in out.items():
        t["mllw"] = S.thresholds_mllw(table[st], t)
    for st, r in table.items():  # an alias shares its gauge's categories
        if r["alias_of"] in out:
            out[st] = out[r["alias_of"]]
    print(f"thresholds: {len(out)} stations with flood categories; {len(failed)} gauges failed")
    return out


def main(path="thresholds.json"):
    t = build()
    json.dump(t, open(path, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
