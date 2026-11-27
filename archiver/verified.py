"""Re-read NOAA's verified and USGS's approved gauge records once the agencies finalize them, list
what changed from the preliminary readings StormSense used, and rescore that month's forecasts.

    python archiver/verified.py --out out [--state data/verified] [--months 2026-10,2026-11]
                                [--stations SWPT,USJT] [--no-rescore] [--probe 12] [--dry]

StormSense forecasts and their scores use the readings as they were at run time: NOAA CO-OPS
"preliminary" 6-minute water levels and USGS "provisional" instantaneous values. The agencies
finalize them later: CO-OPS verifies each station's previous calendar month during the following
months (stations in random order, two-person review), USGS approves a record after its hydrographer
reviews it (policy goal 150 to 240 days). Neither publishes a fixed date, so this job does not assume
one: every month it re-reads each unfinished month for every CO-OPS and USGS gauge, with the flag the
agency attaches to each reading (CO-OPS q = p|v, USGS P|A), and keeps going back to a month until all
of its readings are final. data/verified/status.csv records, per station and month, how much was
final when; data/verified/cadence.csv (from --probe) the same for months before the archive began, so
the real lag is measured rather than guessed.

For each month M it reads M plus PAD_DAYS either side, because a forecast issued in the last days of
the previous month verifies inside M (fore72 is 72 h, Tidewatch's f36 36 h) and writes, on release
verified-YYYY-MM:

    verified_obs_YYYY-MM.tar.xz         <ST>.csv: date_time_z, preliminary_ft, final_ft, status
                                        (ft MLLW; status v|a final, p provisional, - missing)
    changes_YYYY-MM.csv                 every reading the agency changed by more than 0.1 ft, removed
                                        or added (station, time, preliminary, final, change)
    skill_scores_verified_YYYY-MM.csv.gz, flood_scores_verified_YYYY-MM.csv.gz
                                        every run issued from M - PAD_DAYS to the end of M rescored on
                                        the final readings (preliminary ones where not final yet)
    skill_changes_YYYY-MM.csv           those scores next to the operational ones where they differ
    summary_YYYY-MM.md                  the month in a few lines

The operational skill_scores.csv.gz (adaptive pick, cone, CREST) is not changed. Gauges read from
NWPS, THREDDS, FloodVISION or GreenStream have no verified record and are left out.
"""
import argparse
import csv
import io
import json
import os
import sys
import tarfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from stormsense import obs, releases  # noqa: E402

FIRST_MONTH = "2026-10"   # the observation archive starts 14 Oct 2026
PAD_DAYS = 3              # Derek: 2 days for Tidewatch's 36-h forecast; 3 covers StormSense's 72 h
CHANGE_FT = 0.1           # list every reading changed by more than this
MAX_BACK = 12             # months still checked for verification
STATUS_FIELDS = ["month", "station", "agency", "source_id", "readings", "final", "final_pct",
                 "changed_gt_0p1", "removed", "added", "max_abs_change_ft", "checked_utc", "first_final_utc"]
CADENCE_FIELDS = ["month", "station", "agency", "source_id", "readings", "final", "final_pct", "checked_utc"]
SCORE_KEYS = ["cycle", "station", "member", "band"]


# --- reading the agencies with their flags ---------------------------------------------------------
def coops_flagged(noaa_id, a, b):
    """CO-OPS 6-minute water level ft MLLW with each reading's q flag ('v' verified, 'p' preliminary)."""
    rows = []
    for x, y in obs._chunks(a, b, 30):
        url = obs.COOPS.format(id=noaa_id, product="water_level", a=x.strftime("%Y%m%d %H:%M"),
                               b=y.strftime("%Y%m%d %H:%M"))
        js = json.loads(obs.get(url.replace(" ", "%20")))
        if "error" in js:
            if "No data" in str(js["error"]):
                continue
            raise RuntimeError(js["error"].get("message", js["error"]))
        for r in js.get("data", []):
            t = datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            v = float(r["v"]) if r.get("v") not in ("", None) else np.nan
            rows.append((t, v, (r.get("q") or "").lower()[:1] or "?"))
    return rows


def parse_nwis_flagged(raw, pc=obs.NAVD_PARAM):
    for ts in json.loads(raw).get("value", {}).get("timeSeries", []):
        if ts["variable"]["variableCode"][0]["value"] != pc:
            continue
        nodata = ts["variable"].get("noDataValue")
        for block in ts.get("values", []):
            out = []
            for p in block.get("value", []):
                if p.get("value") in (None, ""):
                    continue
                v = float(p["value"])
                q = "a" if "A" in (p.get("qualifiers") or []) else "p"
                out.append((obs.utc(p["dateTime"]), np.nan if v == nodata else v, q))
            if out:
                return out
    return []


def parse_ogc_flagged(raw):
    js = json.loads(raw)
    out = []
    for f in js.get("features", []):
        p = f["properties"]
        if p.get("value") in (None, ""):
            continue
        st = str(p.get("approval_status") or "").lower()
        out.append((obs.utc(p["time"]), float(p["value"]), "a" if st.startswith("approved") else "p"))
    nxt = next((ln["href"] for ln in js.get("links", []) if ln.get("rel") == "next"), None)
    return out, nxt


def usgs_flagged(site, a, b):
    """USGS 62620 ft NAVD88 with each value's approval ('a' approved, 'p' provisional). The OGC API
    first (it keeps years; legacy NWIS IV keeps about 120 days), NWIS IV if that fails."""
    url = obs.OGC.format(site=site, pc=obs.NAVD_PARAM, a=a.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         b=b.strftime("%Y-%m-%dT%H:%M:%SZ"))
    rows = []
    try:
        while url:
            got, url = parse_ogc_flagged(obs.get(url))
            rows += got
        if rows:
            return sorted(rows)
    except Exception as e:
        print(f"  {site}: OGC API failed ({str(e)[:100]}), trying NWIS IV")
    fmt = "%Y-%m-%dT%H:%MZ"
    for x, y in obs._chunks(a, b, 120):
        rows += parse_nwis_flagged(obs.get(obs.NWIS.format(site=site, pc=obs.NAVD_PARAM, a=x.strftime(fmt),
                                                           b=y.strftime(fmt))))
    return sorted(rows)


def agency_source(info):
    """('noaa'|'usgs', id) of the station's first CO-OPS or USGS source, else None."""
    for src, sid in info["chain"]:
        if src == "coops":
            return "noaa", sid
        if src == "nwis":
            return "usgs", sid
    return None


def read_final(st, info, a, b):
    """DataFrame indexed by time: final_ft (ft MLLW), status (v|a|p)."""
    agency, sid = agency_source(info)
    if agency == "noaa":
        rows = coops_flagged(sid, a, b)
        off = 0.0
    else:
        rows = usgs_flagged(sid, a, b)
        off = info["navd88"]
    if not rows:
        return pd.DataFrame(columns=["final_ft", "status"])
    df = pd.DataFrame(rows, columns=["t", "final_ft", "status"]).drop_duplicates("t", keep="last").set_index("t")
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
    df["final_ft"] = df["final_ft"] + off
    return df[(df.index >= a) & (df.index < b)]


# --- the preliminary readings StormSense archived ----------------------------------------------------
def month_bounds(month):
    a = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    b = (a + timedelta(days=32)).replace(day=1)
    return a, b


def preliminary(days, work):
    """{station: Series ft MLLW} of the raw readings in the obs-YYYY-MM day tarballs for these days."""
    out = {}
    rel = releases.assets("obs-", repos=(releases.PRIVATE,))
    urls = {n: u for assets in rel.values() for n, u in assets.items()}
    for d in days:
        name = f"obs_{d:%Y-%m-%d}.tar.xz"
        if name not in urls:
            continue
        path = releases.download(urls[name], os.path.join(work, "obs", name))
        with tarfile.open(path) as t:
            for m in t.getmembers():
                if "/raw/" not in m.name or not m.name.endswith(".csv"):
                    continue
                st = os.path.basename(m.name)[:-4]
                df = pd.read_csv(t.extractfile(m))
                s = pd.Series(df["water_level_ft_mllw"].to_numpy(float),
                              index=pd.to_datetime(df["date_time_z"], utc=True))
                out.setdefault(st, []).append(s)
    return {st: pd.concat(v).sort_index().pipe(lambda s: s[~s.index.duplicated(keep="last")]) for st, v in out.items()}


def compare(pre, fin):
    """(merged frame, changes frame): every time either has, with change = final - preliminary."""
    m = pd.concat([pre.rename("preliminary_ft"), fin["final_ft"], fin["status"]], axis=1, sort=True)
    m["status"] = m["status"].fillna("-")
    m["change_ft"] = m["final_ft"] - m["preliminary_ft"]
    kind = np.where(m["preliminary_ft"].notna() & m["final_ft"].isna() & (m["status"] != "-"), "removed",
           np.where(m["preliminary_ft"].notna() & (m["status"] == "-"), "",
           np.where(m["preliminary_ft"].isna() & m["final_ft"].notna(), "added",
           np.where(m["change_ft"].abs() > CHANGE_FT, "changed", ""))))
    # "added" only on days StormSense archived readings for (the archive starts 14 Oct 2026; a
    # gauge's outage day is still compared, a day before the archive is not)
    have = set(pre.index.floor("D")) if len(pre) else set()
    kind = np.where((kind == "added") & ~np.isin(m.index.floor("D"), list(have)), "", kind)
    m["kind"] = kind
    final = m["status"].isin(["v", "a"])
    ch = m[(m["kind"] != "") & (final | (m["kind"] == "removed"))]
    return m, ch


# --- rescoring ---------------------------------------------------------------------------------------
def rescore(month, truth, work, navd, levels, max_runs=0):
    """Scores of every run issued from the month's start - PAD_DAYS to its end, on `truth`."""
    from stormsense import skill as SK
    a, b = month_bounds(month)
    a = a - timedelta(days=PAD_DAYS)
    rel = releases.assets("ensemble-")
    tars = sorted((SK.cycle_of(n), n, u) for assets in rel.values() for n, u in assets.items()
                  if n.startswith("ensemble_") and n.endswith(".tar.gz"))
    tars = [x for x in tars if a <= x[0] < b][:max_runs or None]
    scores, floods = [], []
    for i, (c, name, url) in enumerate(tars):
        path = releases.download(url, os.path.join(work, "tars", name))
        try:
            s, f, _ = SK.score_runs({c: path}, truth, levels, navd)
            scores.append(s[s["station"].isin(truth)])
            floods.append(f[f["station"].isin(truth)])
        except Exception as e:
            print(f"  {name}: {type(e).__name__}: {str(e)[:120]}")
        finally:
            os.remove(path)
        if i % 20 == 0:
            print(f"  scored {i + 1}/{len(tars)} runs")
    cat = lambda x: pd.concat(x, ignore_index=True) if x else pd.DataFrame()  # noqa: E731
    return cat(scores), cat(floods), len(tars)


def score_changes(ver, work):
    """Verified scores next to the operational ones (newest skill_scores.csv.gz) where they differ."""
    url = next((a["skill_scores.csv.gz"] for a in releases.assets("ensemble-").values() if "skill_scores.csv.gz" in a), None)
    if url is None or ver.empty:
        return pd.DataFrame()
    op = pd.read_csv(io.BytesIO(releases._get(url, "application/octet-stream")), compression="gzip")
    keep = ["Skill", "RMSE", "MAE", "Bias", "R2", "PeakError", "N"]
    m = ver[SCORE_KEYS + keep].merge(op[SCORE_KEYS + keep], on=SCORE_KEYS, how="left", suffixes=("_verified", "_operational"))
    m["skill_change"] = m["Skill_verified"] - m["Skill_operational"]
    m["rmse_change_ft"] = m["RMSE_verified"] - m["RMSE_operational"]
    return m[(m["skill_change"].abs() >= 0.0005) | (m["rmse_change_ft"].abs() >= 0.0005) | m["Skill_operational"].isna()]


# --- state -------------------------------------------------------------------------------------------
def read_csv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def months_to_check(status, now, wanted=None):
    if wanted:
        return wanted
    last = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    out, m = [], FIRST_MONTH
    while m <= last:
        out.append(m)
        m = (month_bounds(m)[1]).strftime("%Y-%m")
    out = out[-MAX_BACK:]
    done = {}
    for r in status:
        done.setdefault(r["month"], []).append(float(r["final_pct"] or 0) >= 100.0)
    return [m for m in out if not (done.get(m) and all(done[m]))]


def say(**kv):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def probe(table, stations, now, back, state):
    """How much of each of the last `back` months is final now, per gauge (the agencies' real lag)."""
    rows = read_csv_rows(os.path.join(state, "cadence.csv"))
    stamp = now.strftime("%Y-%m-%d %H:%M")
    m = now.replace(day=1)
    months = []
    for _ in range(back):
        m = (m - timedelta(days=1)).replace(day=1)
        months.append(m.strftime("%Y-%m"))
    for st in stations:
        ag = agency_source(table[st])
        for month in months:
            a, b = month_bounds(month)
            try:
                fin = read_final(st, table[st], a, b)
            except Exception as e:
                print(f"  probe {st} {month}: {str(e)[:100]}")
                continue
            n, f = int(fin["final_ft"].notna().sum()), int(fin["status"].isin(["v", "a"]).sum())
            rows.append({"month": month, "station": st, "agency": ag[0], "source_id": ag[1], "readings": n,
                         "final": f, "final_pct": round(100.0 * f / n, 1) if n else 0.0, "checked_utc": stamp})
            print(f"  probe {st} {month}: {f}/{n} final")
    write_csv_rows(os.path.join(state, "cadence.csv"), CADENCE_FIELDS, rows)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", default="out")
    p.add_argument("--state", default="data/verified")
    p.add_argument("--work", default="work_verified")
    p.add_argument("--months", default="")
    p.add_argument("--stations", default="")
    p.add_argument("--no-rescore", action="store_true")
    p.add_argument("--probe", type=int, default=0, help="also record how final each of the last N months is")
    p.add_argument("--probe-stations", default="SWPT,YRCG,KIPT,CBBT,USJT,USSF,USLR,USBM")
    p.add_argument("--max-runs", type=int, default=0, help="rescore at most this many runs (a dry run)")
    p.add_argument("--now", default="")
    a = p.parse_args(argv)
    now = datetime.strptime(a.now, "%Y-%m-%d").replace(tzinfo=timezone.utc) if a.now else datetime.now(timezone.utc)
    os.makedirs(a.out, exist_ok=True)
    table = obs.stations()
    gauges = sorted(st for st, r in table.items() if r["chain"] and not r["alias_of"] and agency_source(r))
    if a.stations:
        gauges = [st for st in gauges if st in a.stations.split(",")]
    if a.probe:
        probe(table, [s for s in a.probe_stations.split(",") if s in table and agency_source(table[s])],
              now, a.probe, a.state)

    status_path = os.path.join(a.state, "status.csv")
    status = read_csv_rows(status_path)
    months = months_to_check(status, now, [m for m in a.months.split(",") if m])
    print(f"months to check: {months or 'none'}; {len(gauges)} CO-OPS/USGS gauges")
    stamp = now.strftime("%Y-%m-%d %H:%M")
    assets = []
    navd = {st: r["navd88"] for st, r in table.items()}
    levels = None
    for month in months:
        m0, m1 = month_bounds(month)
        a0, b0 = m0 - timedelta(days=PAD_DAYS), m1 + timedelta(days=PAD_DAYS)
        days = [a0 + timedelta(days=i) for i in range((b0 - a0).days)]
        pre = preliminary(days, a.work)
        print(f"{month}: preliminary readings for {len(pre)} gauges")
        prev = {(r["month"], r["station"]): r for r in status}
        tar_path = os.path.join(a.out, f"verified_obs_{month}.tar.xz")
        changes, truth, newly_final = [], {}, []
        with tarfile.open(tar_path, "w:xz") as tar:
            for st in gauges:
                ag, sid = agency_source(table[st])
                try:
                    fin = read_final(st, table[st], a0, b0)
                except Exception as e:
                    print(f"  {st}: {type(e).__name__}: {str(e)[:120]}")
                    continue
                m, ch = compare(pre.get(st, pd.Series(dtype=float)), fin)
                inm = (m.index >= m0) & (m.index < m1)
                n = int(m.loc[inm, "final_ft"].notna().sum())
                nf = int(m.loc[inm, "status"].isin(["v", "a"]).sum())
                pct = round(100.0 * nf / n, 1) if n else 0.0
                chm = ch[(ch.index >= m0) & (ch.index < m1)]
                old = prev.get((month, st), {})
                first_final = old.get("first_final_utc") or (stamp if pct >= 100.0 else "")
                if nf > int(old.get("final") or 0):
                    newly_final.append(st)
                row = {"month": month, "station": st, "agency": ag, "source_id": sid, "readings": n, "final": nf,
                       "final_pct": pct, "changed_gt_0p1": int((chm["kind"] == "changed").sum()),
                       "removed": int((chm["kind"] == "removed").sum()), "added": int((chm["kind"] == "added").sum()),
                       "max_abs_change_ft": round(float(chm["change_ft"].abs().max()), 3) if chm["change_ft"].notna().any() else 0.0,
                       "checked_utc": stamp, "first_final_utc": first_final}
                status = [r for r in status if (r["month"], r["station"]) != (month, st)] + [row]
                print(f"  {st} ({ag} {sid}): {nf}/{n} final, {row['changed_gt_0p1']} changed > {CHANGE_FT} ft, "
                      f"{row['removed']} removed, {row['added']} added")
                out = m.reset_index().rename(columns={"index": "date_time_z"})
                out["date_time_z"] = pd.to_datetime(out["date_time_z"], utc=True).dt.strftime("%Y-%m-%dT%H:%MZ")
                buf = out[["date_time_z", "preliminary_ft", "final_ft", "status"]].to_csv(index=False, float_format="%.3f").encode()
                ti = tarfile.TarInfo(f"verified_obs_{month}/{st}.csv")
                ti.size = len(buf)
                tar.addfile(ti, io.BytesIO(buf))
                for t, r in chm.iterrows():
                    changes.append({"station": st, "agency": ag, "date_time_z": t.strftime("%Y-%m-%dT%H:%MZ"),
                                    "preliminary_ft": r["preliminary_ft"], "final_ft": r["final_ft"],
                                    "change_ft": r["change_ft"], "kind": r["kind"], "status": r["status"]})
                # truth: the final reading where the agency finalized it, else the preliminary one; QC'd
                best = m["final_ft"].where(m["status"].isin(["v", "a"]), m["preliminary_ft"]).dropna()
                if (m["status"].isin(["v", "a"])).any() and len(best):
                    tq, vq = obs.on_six_minutes(*obs.qc(np.array([x.to_pydatetime() for x in best.index]),
                                                        best.to_numpy(float)))
                    if len(tq):
                        truth[st] = pd.Series(vq, index=pd.DatetimeIndex(tq))
        write_csv_rows(status_path, STATUS_FIELDS, sorted(status, key=lambda r: (r["month"], r["station"])))
        tag = f"verified-{month}"
        assets.append((tag, tar_path))
        ch_path = os.path.join(a.out, f"changes_{month}.csv")
        pd.DataFrame(changes, columns=["station", "agency", "date_time_z", "preliminary_ft", "final_ft", "change_ft",
                                       "kind", "status"]).to_csv(ch_path, index=False, float_format="%.3f")
        assets.append((tag, ch_path))
        lines = [f"# Verified observations, {month}", "",
                 f"Checked {stamp} UTC. Readings from {a0:%Y-%m-%d} to {b0:%Y-%m-%d} (the month plus {PAD_DAYS} days either side).", "",
                 "| station | agency | final | changed > 0.1 ft | removed | added | max change ft |", "|---|---|---|---|---|---|---|"]
        for r in sorted((r for r in status if r["month"] == month), key=lambda r: r["station"]):
            lines.append(f"| {r['station']} | {r['agency']} | {r['final_pct']}% | {r['changed_gt_0p1']} | {r['removed']} | "
                         f"{r['added']} | {r['max_abs_change_ft']} |")
        if not newly_final:
            lines += ["", "Nothing more was finalized since the last check: not rescored."]
        if truth and newly_final and not a.no_rescore:
            if levels is None:
                try:
                    from stormsense import thresholds as THR
                    levels = {st: e.get("mllw", {}) for st, e in THR.build(table).items()}
                except Exception as e:
                    print("thresholds unavailable:", e)
                    levels = {}
            sc, fl, nruns = rescore(month, truth, a.work, navd, levels, a.max_runs)
            for name, df in ((f"skill_scores_verified_{month}.csv.gz", sc), (f"flood_scores_verified_{month}.csv.gz", fl)):
                path = os.path.join(a.out, name)
                df.to_csv(path, index=False, float_format="%.4f", compression="gzip")
                assets.append((tag, path))
            sch = score_changes(sc, a.work)
            path = os.path.join(a.out, f"skill_changes_{month}.csv")
            sch.to_csv(path, index=False, float_format="%.4f")
            assets.append((tag, path))
            lines += ["", f"Rescored {nruns} runs on {len(truth)} gauges with final readings: {len(sc)} scores, "
                      f"{len(sch)} differ from the operational scores."]
            if len(sch):
                full = sch[sch["band"] == "0-36"] if "0-36" in set(sch["band"]) else sch
                big = full.reindex(full["skill_change"].abs().sort_values(ascending=False).index).head(10)
                lines += ["", "Largest skill changes:", "", "| cycle | station | member | band | skill op -> verified | RMSE op -> verified |",
                          "|---|---|---|---|---|---|"]
                for _, r in big.iterrows():
                    lines.append(f"| {r['cycle']} | {r['station']} | {r['member']} | {r['band']} | {r['Skill_operational']:.3f} -> "
                                 f"{r['Skill_verified']:.3f} | {r['RMSE_operational']:.3f} -> {r['RMSE_verified']:.3f} |")
        md = os.path.join(a.out, f"summary_{month}.md")
        open(md, "w").write("\n".join(lines) + "\n")
        assets.append((tag, md))
        print("\n".join(lines))
    with open(os.path.join(a.out, "assets.txt"), "w") as f:
        f.writelines(f"{t} {p}\n" for t, p in assets)
    say(checked="true" if months else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
