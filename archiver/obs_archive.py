"""Archive StormSense's own gauge observations, one UTC day at a time.

    python archiver/obs_archive.py [--index data/index/obs] [--out out]
                                   [--since YYYY-MM-DD] [--days 10]

Reads every gauge StormSense observes for itself straight from its source (NOAA CO-OPS, USGS
NWIS, the VIMS THREDDS gauge, the FloodVISION and GreenStream archives), with the fetch and QC
code of this repository's stormsense/obs.py (each gauge's source chain in stormsense/stations.csv), and for
each finished UTC day not yet in the index writes out/obs_YYYY-MM-DD.tar.xz holding

    raw/<ST>.csv     every reading as the source gave it, ft MLLW, with its QC flag
                     (ok, range, spike, flatline), so QC can be redone later
    6min/<ST>.csv    the QC'd series on the 6-minute clock, ft MLLW (what the model uses)
    stations.csv     source, id and NAVD88-above-MLLW offset used for each gauge
    report.csv       per gauge: readings, flags, 6-minute coverage, error

and appends to the monthly index CSVs:

    days_YYYY-MM.csv      one row per archived day
    stations_YYYY-MM.csv  one row per day and gauge: counts, coverage, first and last time, error

Days are archived once they ended 3 hours ago (USGS and CO-OPS post within minutes to an hour).
Each run catches up the last --days days (GitHub skips scheduled runs now and then), or every day
from --since for a backfill. Writes out/assets.txt ("tag path" per tarball) and archived=true|false
to GITHUB_OUTPUT. Exits 1 when no gauge could be read for a day.
"""

import argparse
import concurrent.futures
import csv
import hashlib
import io
import os
import sys
import tarfile
from datetime import datetime, timedelta, timezone

import numpy as np

SETTLE = timedelta(hours=3)
PAD = timedelta(hours=1)  # read past both ends of the span so QC and the 6-minute clock see neighbours
DAY_FIELDS = ["day", "archived_utc", "tag", "asset", "bytes", "sha256", "gauges", "gauges_ok", "readings",
              "flagged", "six_min", "failed"]
STATION_FIELDS = ["day", "station", "source", "source_id", "readings", "flag_range", "flag_spike",
                  "flag_flatline", "six_min", "coverage_pct", "first_time_z", "last_time_z", "error"]


def read_index_days(index_dir):
    done = set()
    if os.path.isdir(index_dir):
        for name in os.listdir(index_dir):
            if name.startswith("days_") and name.endswith(".csv"):
                with open(os.path.join(index_dir, name), newline="") as f:
                    done |= {r["day"] for r in csv.DictReader(f)}
    return done


def append_rows(index_dir, prefix, month, fields, rows):
    os.makedirs(index_dir, exist_ok=True)
    p = os.path.join(index_dir, f"{prefix}_{month}.csv")
    new = not os.path.exists(p)
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def say(**kv):
    """key=value lines for the workflow step's outputs."""
    out = os.environ.get("GITHUB_OUTPUT")
    for k, v in kv.items():
        print(f"{k}={v}")
        if out:
            with open(out, "a") as f:
                f.write(f"{k}={v}\n")


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%MZ")


def to_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def days_to_archive(done, now, since=None, days=10):
    last = (now - SETTLE).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    first = since or last - timedelta(days=days - 1)
    out, d = [], first
    while d <= last:
        if d.strftime("%Y-%m-%d") not in done:
            out.append(d)
        d += timedelta(days=1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="data/index/obs")
    ap.add_argument("--out", default="out")
    ap.add_argument("--since", default="")
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from stormsense import obs

    now = datetime.now(timezone.utc)
    since = datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=timezone.utc) if a.since else None
    todo = days_to_archive(read_index_days(a.index), now, since, a.days)
    if not todo:
        print("every finished day is archived")
        say(archived="false")
        return 0
    span_a, span_b = todo[0] - PAD, todo[-1] + timedelta(days=1) + PAD
    print(f"archiving {len(todo)} day(s), {todo[0]:%Y-%m-%d} to {todo[-1]:%Y-%m-%d}")

    table = obs.stations()
    gauges = [st for st, r in sorted(table.items()) if r["chain"] and not r["alias_of"]]

    used = {}

    def one(st):
        try:
            t, v = obs.fetch_raw(st, span_a, span_b, table[st], used)
            return st, obs.qc_flags(np.array(t), np.array(v, float)), ""
        except Exception as e:
            return st, None, f"{type(e).__name__}: {e}"[:200]

    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        got = {st: (res, err) for st, res, err in ex.map(one, gauges)}

    # the QC'd 6-minute series over the whole span, so a day's edges interpolate across midnight
    six = {}
    for st, (res, err) in got.items():
        if res is not None:
            t, v, f = res
            six[st] = obs.on_six_minutes(t[f == 0], v[f == 0])

    def src(st):  # (source, id) that answered, else the first in the station's chain
        return ((used.get(st) or table[st]["obs"].split(";")[0]) + ":").split(":")[:2]

    stations_csv = to_csv(["station", "source", "source_id", "navd88_above_mllw_ft", "name"],
                  [[st, *src(st), table[st]["navd88_above_mllw_ft"],
                    table[st]["name"]] for st in gauges])
    os.makedirs(a.out, exist_ok=True)
    assets, status = [], 0
    for day in todo:
        end = day + timedelta(days=1)
        files, rows = {"stations.csv": stations_csv}, []
        for st in gauges:
            res, err = got[st]
            row = {"day": f"{day:%Y-%m-%d}", "station": st, "source": src(st)[0],
                   "source_id": src(st)[1], "readings": 0, "flag_range": 0, "flag_spike": 0,
                   "flag_flatline": 0, "six_min": 0, "coverage_pct": 0.0, "first_time_z": "",
                   "last_time_z": "", "error": err}
            if res is not None:
                t, v, f = res
                sel = np.array([day <= x < end for x in t], bool)
                if sel.any():
                    files[f"raw/{st}.csv"] = to_csv(["date_time_z", "water_level_ft_mllw", "qc"],
                                                    [[stamp(x), f"{y:.3f}", obs.QC_FLAGS[q]]
                                                     for x, y, q in zip(t[sel], v[sel], f[sel])])
                    row.update(readings=int(sel.sum()), flag_range=int((f[sel] == 1).sum()),
                               flag_spike=int((f[sel] == 2).sum()), flag_flatline=int((f[sel] == 3).sum()),
                               first_time_z=stamp(t[sel][0]), last_time_z=stamp(t[sel][-1]))
                t6, v6 = six[st]
                s6 = np.array([day <= x < end for x in t6], bool)
                if s6.any():
                    files[f"6min/{st}.csv"] = to_csv(["date_time_z", "water_level_ft_mllw"],
                                                     [[stamp(x), f"{y:.3f}"] for x, y in zip(t6[s6], v6[s6])])
                row.update(six_min=int(s6.sum()), coverage_pct=round(100.0 * s6.sum() / 240, 1))
                if not row["readings"] and not row["error"]:
                    row["error"] = "no readings this day"
            rows.append(row)
        files["report.csv"] = to_csv(STATION_FIELDS[1:], [[r[k] for k in STATION_FIELDS[1:]] for r in rows])

        asset = f"obs_{day:%Y-%m-%d}.tar.xz"
        tag = f"obs-{day:%Y-%m}"
        path = os.path.join(a.out, asset)
        with tarfile.open(path, "w:xz", preset=6) as tf:
            for name in sorted(files):
                info = tarfile.TarInfo(f"obs_{day:%Y-%m-%d}/{name}")
                info.size, info.mtime, info.mode = len(files[name]), end.timestamp(), 0o644
                tf.addfile(info, io.BytesIO(files[name]))
        blob = open(path, "rb").read()
        ok = [r for r in rows if r["readings"]]
        failed = [r["station"] for r in rows if not r["readings"]]
        drow = {"day": f"{day:%Y-%m-%d}", "archived_utc": now.strftime("%Y-%m-%d %H:%M"), "tag": tag,
                "asset": asset, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
                "gauges": len(rows), "gauges_ok": len(ok), "readings": sum(r["readings"] for r in rows),
                "flagged": sum(r["flag_range"] + r["flag_spike"] + r["flag_flatline"] for r in rows),
                "six_min": sum(r["six_min"] for r in rows), "failed": " ".join(failed)}
        if not ok:
            print(f"{day:%Y-%m-%d}: no gauge could be read; not archived")
            status = 1
            continue
        month = day.strftime("%Y-%m")
        append_rows(a.index, "days", month, DAY_FIELDS, [drow])
        append_rows(a.index, "stations", month, STATION_FIELDS, rows)
        assets.append(f"{tag} {path}")
        line = (f"{asset}: {len(ok)}/{len(rows)} gauges, {drow['readings']} readings, {drow['flagged']} flagged, "
                f"{len(blob) // 1024} KB; none from {drow['failed'] or '-'}")
        print(line)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::notice title={asset}::{line}")
    with open(os.path.join(a.out, "assets.txt"), "w") as f:
        f.write("".join(x + "\n" for x in assets))
    for st, (res, err) in sorted(got.items()):
        if err:
            print(f"  {st}: {err}")
            if os.environ.get("GITHUB_ACTIONS"):  # readable as a check-run annotation
                print(f"::warning title={st} not read::{err}")
    say(archived="true" if assets else "false")
    return status


if __name__ == "__main__":
    sys.exit(main())
