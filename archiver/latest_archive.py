"""Archive the run StormSense Latest has just published.

    python archiver/latest_archive.py [--base URL] [--index data/index/stormsense-latest] [--out out]

Reads latest.json from the public StormSense Latest site; if its "updated" time is not in the
run index yet, downloads every file index.html links (fore72, 7day, 30day: html, csv, json)
plus index.html, latest.json, thresholds.json, build_log.txt, the metadata JSON, the logo and
the jslib/ the charts load, so an unpacked tarball (xz: half the size of gzip) opens like the site did. Writes
out/stormsense_<build time>.tar.xz, e.g. stormsense_2026-10-20T1156Z.tar.xz (paths as on the site,
with manifest.csv listing every file), and appends to the monthly index CSVs:

    runs_YYYY-MM.csv      one row per archived run
    stations_YYYY-MM.csv  one row per run, station, product and datum: which formats it holds,
                          rows, first and last time, and the guidance named in the JSON source

Tarballs are named by build time because GitHub starts the scheduled builds hours late or skips
them; the index's cycle column is the 00/06/12/18Z slot the build ran in, and petss_cycle names the
P-ETSS cycle it used. Writes key=value lines to GITHUB_OUTPUT for the
workflow: archived, asset, tag. Exits 1 when files the index links could not be fetched
(the tarball is still written with what was).
"""

import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("STORMSENSE_LATEST_URL", "https://derekloftis.github.io/StormSense-latest/")
UA = {"User-Agent": "stormsense-archive (VIMS)", "Cache-Control": "no-cache"}
PRODUCT_FILE = re.compile(r"^(fore72|7day|30day)/([A-Z0-9-]+)_(fore72|7day|30day)_(MLLW|NAVD)\.(html|csv|json)$")
FIXED = ["index.html", "latest.json", "thresholds.json", "build_log.txt", "stormsense_logo.png", "jslib/stormsense_logo.png"]
OPTIONAL = {"build_log.txt", "thresholds.json", "stormsense_logo.png", "jslib/stormsense_logo.png"}  # kept when there, not missed
RUN_FIELDS = ["cycle", "updated_utc", "archived_utc", "tag", "asset", "bytes", "sha256", "files", "missing",
              "stations", "fore72_petss", "fore72_tidewatch", "petss_cycle", "upstream_failed"]
STATION_FIELDS = ["cycle", "asset", "station", "product", "datum", "html", "csv", "json", "rows",
                  "first_time_z", "last_time_z", "source"]


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1:
                raise
        except Exception:
            if i == tries - 1:
                raise
        time.sleep(3 * 2 ** i)


def bust(url):
    """Pages sits behind a CDN that can serve the previous deploy for a few minutes."""
    return f"{url}{'&' if '?' in url else '?'}t={int(time.time())}"


def read_latest(base):
    doc = json.loads(get(bust(base + "latest.json")))
    updated = datetime.strptime(doc["updated"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    return doc, updated


def cycle_of(t):
    return t.replace(hour=t.hour // 6 * 6, minute=0, second=0, microsecond=0)


def read_index_rows(index_dir, prefix, months):
    rows = []
    for m in months:
        p = os.path.join(index_dir, f"{prefix}_{m}.csv")
        if os.path.exists(p):
            with open(p, newline="") as f:
                rows += list(csv.DictReader(f))
    return rows


def append_rows(index_dir, prefix, month, fields, rows):
    os.makedirs(index_dir, exist_ok=True)
    p = os.path.join(index_dir, f"{prefix}_{month}.csv")
    new = not os.path.exists(p)
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def linked_files(base, page):
    """Product files and metadata JSON that index.html links, plus the scripts one chart loads."""
    hrefs = sorted(set(re.findall(r'href="([^"#?:]+)"', page)))
    files = [h for h in hrefs if PRODUCT_FILE.match(h)]
    files += [h for h in hrefs if h.startswith("metadata/") and h.endswith(".json")]
    chart = next((h for h in files if h.endswith(".html")), None)
    if chart:
        text = get(bust(base + chart)).decode("utf-8", "replace")
        for src in re.findall(r'<script[^>]+src="([^"]+)"', text):
            if src.startswith("../") and "://" not in src:
                files.append(src[3:])
        for src in re.findall(r'<img[^>]+src="\.\./([^"]+)"', text):
            files.append(src)
    return files


def csv_summary(data):
    """Rows and first/last date_time_z of a product CSV."""
    rows = list(csv.reader(io.StringIO(data.decode("utf-8", "replace"))))
    body = [r for r in rows[1:] if r and r[0].strip()]
    if not body:
        return 0, "", ""
    return len(body), body[0][0].strip(), body[-1][0].strip()


def fetch_all(base, paths):
    got, missing = {}, []

    def one(p):
        return p, get(bust(base + p))

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        futs = [ex.submit(one, p) for p in paths]
        for fu, p in zip(futs, paths):
            try:
                got[p] = fu.result()[1]
            except Exception as e:
                if p not in OPTIONAL:
                    missing.append(f"{p} ({e})")
    return got, missing


def say(**kv):
    """key=value lines for the workflow step's outputs."""
    out = os.environ.get("GITHUB_OUTPUT")
    for k, v in kv.items():
        print(f"{k}={v}")
        if out:
            with open(out, "a") as f:
                f.write(f"{k}={v}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--index", default="data/index/stormsense-latest")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    base = a.base if a.base.endswith("/") else a.base + "/"

    doc, updated = read_latest(base)
    cycle = cycle_of(updated)
    stamp = updated.strftime("%Y-%m-%d %H:%M")
    months = sorted({cycle.strftime("%Y-%m"), (cycle - timedelta(days=7)).strftime("%Y-%m")})
    done = read_index_rows(a.index, "runs", months)
    if any(r["updated_utc"] == stamp for r in done):
        print(f"build of {stamp} UTC is already archived")
        say(archived="false")
        return 0

    for attempt in range(3):
        page = get(bust(base + "index.html")).decode("utf-8", "replace")
        paths = list(dict.fromkeys(FIXED + linked_files(base, page)))
        got, missing = fetch_all(base, paths)
        doc2, updated2 = read_latest(base)
        if updated2 == updated:
            break
        # a new build landed while downloading: archive that one instead
        print(f"site changed from {stamp} to {updated2:%Y-%m-%d %H:%M} during download; starting again")
        doc, updated = doc2, updated2
        cycle, stamp = cycle_of(updated), updated.strftime("%Y-%m-%d %H:%M")
        if any(r["updated_utc"] == stamp for r in done):
            say(archived="false")
            return 0
    else:
        print("site kept changing; giving up")
        return 1
    got["latest.json"] = json.dumps(doc, indent=1).encode()

    asset = f"stormsense_{updated:%Y-%m-%dT%H%MZ}.tar.xz"
    tag = f"stormsense-{cycle:%Y-%m}"

    # per station, product and datum: which formats, and what the CSV and JSON say
    stations = {}
    for p, data in got.items():
        m = PRODUCT_FILE.match(p)
        if not m:
            continue
        _, st, prod, datum, ext = m.groups()
        r = stations.setdefault((st, prod, datum), {"cycle": f"{cycle:%Y-%m-%dT%HZ}", "asset": asset, "station": st,
                                                    "product": prod, "datum": datum, "html": 0, "csv": 0, "json": 0,
                                                    "rows": "", "first_time_z": "", "last_time_z": "", "source": ""})
        r[ext] = 1
        if ext == "csv":
            r["rows"], r["first_time_z"], r["last_time_z"] = csv_summary(data)
        elif ext == "json":
            try:
                r["source"] = json.loads(data).get("source", "")
            except ValueError:
                pass
    fore = {st: r["source"] for (st, prod, d), r in stations.items() if prod == "fore72" and d == "MLLW"}
    petss = doc.get("petss") or {}

    manifest = io.StringIO()
    w = csv.writer(manifest)
    w.writerow(["path", "bytes", "sha256"])
    for p in sorted(got):
        w.writerow([p, len(got[p]), hashlib.sha256(got[p]).hexdigest()])
    for x in missing:
        w.writerow([x, "", "missing"])
    got["manifest.csv"] = manifest.getvalue().encode()

    os.makedirs(a.out, exist_ok=True)
    tar = os.path.join(a.out, asset)
    mtime = updated.timestamp()
    with tarfile.open(tar, "w:xz", preset=6) as t:
        for p in sorted(got):
            info = tarfile.TarInfo(p)
            info.size, info.mtime, info.mode = len(got[p]), mtime, 0o644
            t.addfile(info, io.BytesIO(got[p]))
    blob = open(tar, "rb").read()

    run = {"cycle": f"{cycle:%Y-%m-%dT%HZ}", "updated_utc": stamp,
           "archived_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "tag": tag, "asset": asset,
           "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "files": len(got) - 1,
           "missing": len(missing), "stations": len({k[0] for k in stations}),
           "fore72_petss": sum(s.startswith("NWS P-ETSS") for s in fore.values()),
           "fore72_tidewatch": sum(s.startswith("VIMS Tidewatch") for s in fore.values()),
           "petss_cycle": petss.get("cycle", ""), "upstream_failed": len(doc.get("failed") or [])}
    month = cycle.strftime("%Y-%m")
    append_rows(a.index, "runs", month, RUN_FIELDS, [run])
    append_rows(a.index, "stations", month, STATION_FIELDS, [stations[k] for k in sorted(stations)])

    print(f"{asset}: {run['files']} files, {run['stations']} stations, {len(blob) // 1024} KB, "
          f"{len(missing)} missing; built {stamp} UTC")
    for x in missing:
        print("  missing:", x)
    say(archived="true", asset=tar, tag=tag)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
