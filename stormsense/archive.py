"""Archived StormSense Ensemble runs: download them from the ensemble-YYYY-MM releases (this
repository's, then the public StormSense-ensemble's from before Oct 2026), read their MLLW ens72
CSVs, and stitch the observations into a truth record. Used by the predictor-corrector (live,
inside the run) and by the hindcasts.

Runs record which observations they used (latest.json "obs_basis"): "stormsense" for the gauges'
own records (since the move off web4), absent for Tidewatch's. USGS gauges sit on a different MLLW
in the two (Tidewatch used a fixed 1.645 ft below NAVD88), so truth is stitched within one basis."""
import csv
import io
import json
import os
import re
import tarfile
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

REPO = "DerekLoftis/StormSense-ensemble"
UA = {"User-Agent": "StormSense forecast model (VIMS)"}
NAME = re.compile(r"ensemble_(\d{4}-\d\d-\d\dT\d\dZ)(\.tar\.gz)?$")


def parse_t(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def cycle(name):
    return datetime.strptime(NAME.search(os.path.basename(name)).group(1), "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)


def _get(url):
    head = dict(UA)
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok and url.startswith("https://api.github.com/"):
        head["Authorization"] = f"Bearer {tok}"
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=120) as r:
                return r.read()
        except Exception:
            if i == 2:
                raise
            time.sleep(5 * (i + 1))


def download_recent(folder, now, days=8):
    """Fetch the run tarballs of the last `days` from the ensemble-YYYY-MM releases into folder
    (kept if already there). Returns their paths, oldest first."""
    from . import releases
    since = now - timedelta(days=days)
    paths = []
    for tag, assets in releases.assets("ensemble-").items():
        for name, url in assets.items():
            if NAME.match(name) and since <= cycle(name) < now:
                paths.append(releases.download(url, os.path.join(folder, name)))
    return sorted(set(paths), key=cycle)


def basis(path):
    """The observation basis a run tarball recorded ("stormsense" or "tidewatch")."""
    try:
        with tarfile.open(path) as t:
            return json.load(t.extractfile("latest.json")).get("obs_basis", "tidewatch")
    except Exception:
        return "tidewatch"


def read_run(path, navd88=None):
    """{station: (grid, cols)} from one run's ens72 CSVs (a tarball or an extracted folder) on the
    station's own MLLW; cols maps the column name without _ft to its values, NaN for blanks. A run
    on Tidewatch's observation basis is read from its NAVD88 CSVs plus navd88[station]."""
    navd88 = navd88 or {}
    files = {}
    if path.endswith(".tar.gz"):
        with tarfile.open(path) as t:
            try:
                own = json.load(t.extractfile("latest.json")).get("obs_basis") == "stormsense"
            except Exception:
                own = False
            for m in t.getmembers():
                if m.name.endswith(("_ens72_MLLW.csv", "_ens72_NAVD.csv")):
                    files[os.path.basename(m.name)] = t.extractfile(m).read().decode()
    else:
        d = os.path.join(path, "ens72")
        own = True
        for n in os.listdir(d):
            if n.endswith(("_ens72_MLLW.csv", "_ens72_NAVD.csv")):
                files[n] = open(os.path.join(d, n)).read()
    by_st = {}
    for n, text in files.items():
        if not n.endswith("_ens72_MLLW.csv"):
            continue
        st = n.split("_ens72_")[0]
        add = 0.0
        navd = f"{st}_ens72_NAVD.csv"
        if not own and navd88.get(st) is not None and navd in files:
            text, add = files[navd], navd88[st]
        rows = list(csv.reader(io.StringIO(text)))
        head, rows = rows[0], rows[1:]
        grid = np.array([parse_t(r[0]) for r in rows])
        cols = {}
        for j, h in enumerate(head[1:], 1):
            v = np.array([float(r[j]) if r[j] else np.nan for r in rows])
            if not np.isnan(v).all():
                cols[h[:-3]] = v + (0.0 if h.endswith("_surge_ft") else add)
        by_st[st] = (grid, cols)
    return by_st


def read_runs(paths, navd88=None):
    """{cycle string: {station: (grid, cols)}} for tarballs or folders named ensemble_<cycle>."""
    return {NAME.search(os.path.basename(p.rstrip("/"))).group(1): read_run(p, navd88) for p in paths}


def read_folder(folder, navd88=None):
    return read_runs([os.path.join(folder, n) for n in sorted(os.listdir(folder)) if NAME.match(n)], navd88)


def truth_series(runs, extra=None):
    """{station: {time: observed ft}} stitched across runs, the newest run winning; extra
    ({station: (grid, observed)}, e.g. the run being built) wins over all of them."""
    out = defaultdict(dict)
    for cyc in sorted(runs):
        for st, (grid, cols) in runs[cyc].items():
            if "observed" in cols:
                for t, v in zip(grid, cols["observed"]):
                    if not np.isnan(v):
                        out[st][t] = v
    for st, (grid, ob) in (extra or {}).items():
        for t, v in zip(grid, ob):
            if not np.isnan(v):
                out[st][t] = v
    return out


def t0_of(grid, cols):
    seen = np.where(~np.isnan(cols.get("observed", np.full(len(grid), np.nan))))[0]
    return grid[seen[-1]] if len(seen) else None
