"""Build StormSense CREST: station data, pages, the map page and the ArcGIS feeds.

    python -m crest.build --out site --work work [--periods 30d,7d,2026-11] [--stations SWPT,...]
        [--scores a.csv.gz ...] [--floods f.csv ...] [--picks p.csv] [--runs ensemble_*.tar.gz ...]
        [--obs-dir DIR] [--offline] [--months-from DIR ...]

Periods: "30d" and "7d" end at --now (default: now); "YYYY-MM" is a calendar month (the current one
"so far"). Each station's pages start on the day it joined StormSense (stations.csv `added`).
Without local inputs everything is read from this repository's releases (needs GH_TOKEN), as in
.github/workflows/crest.yml. --months-from adds month data built earlier (crest-data release), so
pages list every month without recomputing it.

Writes <out>/index.html (map), <out>/crest_<st>.html (one per station), <out>/data/*.json (every
period, loaded on demand), <out>/feeds/* (CSV and GeoJSON for ArcGIS) and <out>/manifest.json.
"""
import argparse
import csv
import glob
import json
import os
import shutil

import pandas as pd

from stormsense import skill as SK
from stormsense import stations as S

from . import feeds, inputs, page, stats

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_START = pd.Timestamp("2026-10-16", tz="UTC")  # first scored ensemble run


def period_of(pid, now):
    if pid.endswith("d"):
        days = int(pid[:-1])
        end = now.floor("h")
        start = end - pd.Timedelta(days=days)
        label = f"Last {days} days"
    else:
        start = pd.Timestamp(pid + "-01", tz="UTC")
        end = start + pd.offsets.MonthBegin(1)
        label = start.strftime("%B %Y")
        if end > now:
            end = now.floor("h")
            label += " (so far)"
    return {"id": pid, "label": label, "start": start.isoformat(), "end": end.isoformat(), "start_t": start, "end_t": end}


def station_table(only=None):
    out = {}
    for st, r in S.load().items():
        if only and st not in only:
            continue
        name = (r.get("name") or st).replace(" (IN MLLW)", "").replace(" (in MLLW)", "")
        out[st] = {"name": name, "lat": r.get("lat_f"), "lon": r.get("lon_f"), "navd88": r.get("navd88"),
                   "suspect": st in SK.SUSPECT_OBS or (r.get("suspect_obs") or "") == "yes",
                   "added": r.get("added") or "", "source": r.get("obs") or "", "alias_of": r.get("alias_of") or ""}
    return out


def load_changes():
    return list(csv.DictReader(open(os.path.join(HERE, "changes.csv"))))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site")
    ap.add_argument("--work", default="work")
    ap.add_argument("--now", default="")
    ap.add_argument("--periods", default="30d,7d,month")
    ap.add_argument("--stations", default="")
    ap.add_argument("--scores", nargs="*", default=[])
    ap.add_argument("--floods", nargs="*", default=[])
    ap.add_argument("--picks", default="")
    ap.add_argument("--runs", nargs="*", default=[])
    ap.add_argument("--obs-dir", default="")
    ap.add_argument("--offline", action="store_true", help="flood levels from the saved table, no NWPS")
    ap.add_argument("--months-from", nargs="*", default=[], help="folders of month data built earlier")
    ap.add_argument("--default", default="30d", help="the period a page opens on")
    ap.add_argument("--bundle", default="", help="also write each finished month's data to BUNDLE/crest_month_<m>/ (for crest-data)")
    a = ap.parse_args(argv)

    now = pd.Timestamp(a.now, tz="UTC") if a.now else pd.Timestamp.now(tz="UTC")
    pids = []
    for p in a.periods.split(","):
        if p == "month":
            pids.append(now.strftime("%Y-%m"))
        elif p == "months":  # every month since the archive began
            m = ARCHIVE_START.tz_localize(None).to_period("M")
            while m <= now.tz_localize(None).to_period("M"):
                pids.append(str(m))
                m += 1
        elif p:
            pids.append(p)
    periods = [period_of(p, now) for p in dict.fromkeys(pids)]
    only = set(a.stations.split(",")) - {""}
    table = station_table(only)
    os.makedirs(a.work, exist_ok=True)

    # scores
    local = bool(a.scores)
    paths = {"skill_scores.csv.gz": None, "flood_scores.csv.gz": None} if local else inputs.fetch_scores(a.work)
    sc = inputs.load_table(a.scores or [paths.get("skill_scores.csv.gz")], ["cycle", "station", "member", "band"])
    fs = inputs.load_table(a.floods or [paths.get("flood_scores.csv.gz")], ["cycle", "station", "member"])
    print(f"scores: {len(sc)} rows, {sc['cycle'].nunique() if len(sc) else 0} runs; flood rows {len(fs)}")
    if only:
        sc = sc[sc["station"].isin(only)]
        fs = fs[fs["station"].isin(only)] if len(fs) else fs

    first = min(p["start_t"] for p in periods)
    last = max(p["end_t"] for p in periods)
    navd = {st: i["navd88"] for st, i in table.items()}
    runs, pk = inputs.cached_runs(a.work, first - pd.Timedelta(days=0), last, a.runs, navd)
    ph = pd.read_csv(a.picks) if a.picks else inputs.picks_table(pk)
    print(f"runs: {len(runs)}; picks rows {len(ph)}")
    obs = inputs.load_obs(a.work, first - pd.Timedelta(days=1), last + pd.Timedelta(days=4), a.obs_dir or None)
    newest = max((s.index[-1] for s in obs.values() if len(s)), default=None)
    obs = inputs.top_up_obs(obs, runs)
    newest2 = max((s.index[-1] for s in obs.values() if len(s)), default=None)
    print(f"observations: {len(obs)} gauges, archive to {newest}, with the runs' own to {newest2}")
    if not obs:
        print("::warning title=CREST observations::no gauge observations were read; charts and flood events will lack them")
    levels = inputs.load_levels(offline=a.offline)
    hx = inputs.load_health()
    changes = load_changes()

    em = stats.ens_mean_rows(runs, obs, only)
    if len(em):
        sc = pd.concat([sc, em], ignore_index=True)

    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    manifest = {"built": now.strftime("%Y-%m-%d %H:%MZ"), "periods": [], "stations": sorted(table), "default": a.default}
    for p in periods:
        psc = stats.in_period(sc, p["start_t"], p["end_t"])
        pfs = stats.in_period(fs, p["start_t"], p["end_t"]) if len(fs) else fs
        pph = stats.in_period(ph, p["start_t"], p["end_t"]) if len(ph) else ph
        pruns = {c: r for c, r in runs.items() if p["start_t"] <= inputs.cycle_time(c) < p["end_t"]}
        docs = []
        for st, info in table.items():
            if info["alias_of"]:
                continue
            start = p["start_t"]
            if info["added"]:
                start = max(start, pd.Timestamp(info["added"], tz="UTC"))
            if start >= p["end_t"]:
                continue
            s_sc = psc[(psc["station"] == st)]
            s_sc = stats.in_period(s_sc, start, p["end_t"]) if len(s_sc) else s_sc
            if s_sc.empty:
                continue
            q = dict(p, start_t=start, start=start.isoformat())
            d = stats.station_doc(st, info, q, s_sc,
                                  pfs[pfs["station"] == st] if len(pfs) else pfs,
                                  pph[pph["station"] == st] if len(pph) else pph,
                                  pruns, obs, levels.get(st, {}), hx, changes)
            d["period"]["id"] = p["id"]
            docs.append(d)
            json.dump(d, open(os.path.join(a.out, "data", f"{st}_{p['id']}.json"), "w"), separators=(",", ":"), allow_nan=False)
        net = stats.network_doc(p, docs, changes)
        json.dump(net, open(os.path.join(a.out, "data", f"network_{p['id']}.json"), "w"), separators=(",", ":"), allow_nan=False)
        manifest["periods"].append({"id": p["id"], "label": p["label"], "stations": [d["station"] for d in docs]})
        print(f"{p['id']}: {len(docs)} stations")
        if a.bundle and not p["id"].endswith("d") and "so far" not in p["label"] and docs:
            bd = os.path.join(a.bundle, f"crest_month_{p['id']}")
            os.makedirs(os.path.join(bd, "data"), exist_ok=True)
            for f in glob.glob(os.path.join(a.out, "data", f"*_{p['id']}.json")):
                shutil.copy(f, os.path.join(bd, "data"))
            json.dump({"built": now.strftime("%Y-%m-%d %H:%MZ"), "periods": [manifest["periods"][-1]]},
                      open(os.path.join(bd, "manifest.json"), "w"), indent=1)
        if p["id"] == a.default:
            feeds.write(os.path.join(a.out, "feeds"), net, docs, sc, obs, levels, changes, p)

    # months built earlier (crest-data release)
    have = {p["id"] for p in manifest["periods"]}
    for d in a.months_from:
        for mp in sorted(glob.glob(os.path.join(d, "**", "manifest.json"), recursive=True)):
            m = json.load(open(mp))
            for pe in m["periods"]:
                if pe["id"] in have or pe["id"].endswith("d"):
                    continue
                for f in glob.glob(os.path.join(os.path.dirname(mp), "data", f"*_{pe['id']}.json")):
                    shutil.copy(f, os.path.join(a.out, "data"))
                manifest["periods"].append(pe)
                have.add(pe["id"])
    order = {"30d": 0, "7d": 1}
    manifest["periods"].sort(key=lambda x: (order.get(x["id"], 2), "" if x["id"] in order else x["id"]), reverse=False)
    # months newest first after the rolling views
    roll = [x for x in manifest["periods"] if x["id"] in order]
    months = sorted([x for x in manifest["periods"] if x["id"] not in order], key=lambda x: x["id"], reverse=True)
    manifest["periods"] = roll + months
    json.dump(manifest, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    page.write_site(a.out, manifest, table)


if __name__ == "__main__":
    main()
