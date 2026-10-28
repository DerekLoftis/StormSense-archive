"""One-off: compose stormsense/stations.csv, StormSense's single station table, from the tables the
three StormSense repos and the Tidewatch stand-in charts kept separately (Oct 2026), and write the
NOAA stations' tidal datums as ft above MLLW into stormsense/data/datums/. Kept for the record;
stations.csv is the source of truth from now on.

    python tools/make_stations.py
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FM = os.environ.get("FORECASTMODEL", "/home/user/stormsense_forecastmodel/ssmodel/stations.csv")
ENS = os.environ.get("ENSEMBLE", "/home/user/stormsense-ensemble/stations.csv")

FIELDS = ["station", "name", "lat", "lon", "obs", "navd88_above_mllw_ft", "stage_datum", "stage_to_mllw_ft",
          "datum_change_utc", "stage_to_mllw_before_ft", "nws_lid", "petss_point", "tidewatch_id", "alias_of",
          "suspect_obs", "added", "note"]
# P-ETSS station CSV (NOS id or "est" point) per station: matched through the SHEF bulletins for the
# gauges with an NWS id (2026-10-27 06Z cycle), the nearest point on the same water for the rest
PETSS = {"AXTV": "8634214", "BISH": "8571421", "CBBT": "8638901", "CRSM": "8571091", "DAHL": "8635027",
         "EZMV": "est2337", "IHDM": "8579381", "KIPT": "8632200", "LEWI": "8635750", "MNPT": "8639348",
         "SLIM": "8577330", "SWPT": "8638610", "USBK": "est0033", "USFM": "8638288", "USHR": "est0034",
         "USJT": "8637712", "USLB": "est2339", "USLN": "est2340", "USLR": "est2341", "USPT": "8638339",
         "WACH": "8631044", "WADC": "8594900", "WMPT": "8636580", "YRCG": "8637689",
         # nearest point on the same water (no P-ETSS point of their own)
         "BRDH": "8638610", "USLN-X": "est2340", "CNRC": "8635027", "USBC": "8639348", "USBM": "8639348",
         "USMB": "est2339", "USPA": "est2337", "USTC": "est0035", "USLI": "est0035", "USNC": "est0034",
         "USSF": "est2346", "USNR": "est2346", "GS01": "8637712", "GS02": "8637689", "USSB": "est2349",
         "USAR": "8571091", "USBB": "est2301", "USCB": "8630440", "USSC": "8633777"}
# USGS sites of the new stations (NWPS gauge metadata), tried before NWPS's own record
USGS = {"ALEV": "01652545", "AXTV": "0165258890", "BPAD": "01651827", "CRSM": "01485755", "EZMV": "0204288831",
        "GTND": "01647600", "IHDM": "01655480", "LENM": "01661350", "RICV": "02037705"}
# Tidewatch publishes another gauge's observations under these ids (Oct 2026 aliasing check)
SUSPECT = {"USSC", "USAR", "USCB", "USNR", "USSB", "GS01", "USBB"}
# Tidewatch f36 stations that are Tidewatch's own forecast (not one we rebuilt from CO-OPS or VIMS data)
NOT_TIDEWATCH = {"CBBT", "MNPT", "DAHL", "WADC", "BRDH", "USLN-X", "CNRC"}


def noaa_datums(noaa_id, navd):
    j = json.load(open(os.path.join(HERE, "noaa_datums", f"{noaa_id}.json")))
    v = {d["name"]: d["value"] for d in j["datums"] if d.get("value") is not None}
    for k in ("HAT", "LAT"):
        if k not in v and j.get(k) is not None:
            v[k] = j[k]
    out = {k: round(v[k] - v["MLLW"], 3) for k in ("HAT", "MHHW", "MSL", "MLLW", "LAT", "NAVD88") if k in v}
    if "NAVD88" not in out:  # CBBT, MNPT: VDatum (MDVAchb12)
        out["NAVD88"] = navd
        out["_estimated"] = ["NAVD88"]
    out["_source"] = f"NOAA CO-OPS {noaa_id}, epoch {j.get('epoch')}"
    return out


def main():
    lids = {r["station"]: r for r in csv.DictReader(open(ENS))}
    rows = []
    for r in csv.DictReader(open(FM)):
        st = r["station"]
        src = {"coops": "coops:", "nwis": "nwis:", "thredds": "thredds:", "archive": "archive:"}.get(r["source"])
        obs = src + r["source_id"] if src else ""
        if st in ("GS01", "GS02"):
            obs = ""  # GreenStream's API needs a key; no public feed yet
        rows.append({"station": st, "name": r["name"] or st, "lat": r["lat"], "lon": r["lon"], "obs": obs,
                     "navd88_above_mllw_ft": r["navd88_above_mllw_ft"], "stage_datum": "", "stage_to_mllw_ft": "",
                     "datum_change_utc": "", "stage_to_mllw_before_ft": "",
                     "nws_lid": r["nws_lid"] or lids.get(st, {}).get("nws_lid", ""), "petss_point": PETSS.get(st, ""),
                     "tidewatch_id": "" if st in NOT_TIDEWATCH else st, "alias_of": r["alias_of"],
                     "suspect_obs": "yes" if st in SUSPECT else "", "added": "", "note": r["note"]})
        if r["source"] == "coops":
            json.dump(noaa_datums(r["source_id"], float(r["navd88_above_mllw_ft"])),
                      open(os.path.join(ROOT, "stormsense", "data", "datums", f"{st}.json"), "w"), indent=1)
    have = {r["station"] for r in rows}
    for r in csv.DictReader(open(os.path.join(HERE, "nwps_stations_source.csv"))):
        st = r["station"]
        if st in have:
            continue
        chain = []
        if r["operator"] == "NOAA NOS" and r["surge_from"].isdigit() and st == "SLIM":
            chain.append("coops:8577330")
        if st in USGS:
            chain.append("nwis:" + USGS[st])
        chain.append("nwps:" + r["nwps_lid"])
        rows.append({"station": st, "name": r["name"], "lat": r["lat"], "lon": r["lon"], "obs": ";".join(chain),
                     "navd88_above_mllw_ft": r["navd88_above_mllw_ft"], "stage_datum": r["stage_datum"],
                     "stage_to_mllw_ft": r["stage_to_mllw_ft"], "datum_change_utc": r["datum_change_utc"],
                     "stage_to_mllw_before_ft": r["stage_to_mllw_before_ft"], "nws_lid": r["nwps_lid"],
                     "petss_point": PETSS.get(st, r["surge_from"]), "tidewatch_id": "", "alias_of": "",
                     "suspect_obs": "", "added": "2026-10-27", "note": r["note"]})
    have = {r["station"] for r in rows}
    old = os.path.join(ROOT, "stormsense", "stations.csv")
    if os.path.exists(old):  # keep stations added by hand (stand-ins such as VIMS)
        rows += [r for r in csv.DictReader(open(old)) if r["station"] not in have]
    rows.sort(key=lambda x: x["station"])
    with open(os.path.join(ROOT, "stormsense", "stations.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(len(rows), "stations")


if __name__ == "__main__":
    main()
