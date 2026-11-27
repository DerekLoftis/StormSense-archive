"""CREST's inputs, read from the StormSense archives (inside GitHub Actions, where the release
downloads work) or from local files (testing).

    scores      skill_scores.csv.gz and flood_scores.csv.gz, cumulative, on the newest ensemble-YYYY-MM
                release (every run x station x member x window, scored by stormsense/skill.py)
    picks       skill/picks_history.csv inside the newest ensemble run tarball
    runs        ensemble_<cycle>.tar.gz: every member's ens72 series (6-minute, ft MLLW); reduced to
                the 0-72 h forecasts per station and cached as runs/<cycle>.json.gz
    obs         obs_YYYY-MM-DD.tar.xz on obs-YYYY-MM: each gauge's QC'd 6-minute record (6min/<ST>.csv)
    health      data/index/obs/stations_YYYY-MM.csv in this repository (coverage and QC flags per day)
    levels      NWS flood categories in ft above each station's MLLW (stormsense/thresholds.py)
"""
import glob
import gzip
import io
import json
import os
import re
import tarfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from stormsense import releases
from stormsense.obs import without_bridges

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
RUN_NAME = re.compile(r"ensemble_(\d{4}-\d\d-\d\dT\d\dZ)\.tar\.gz$")
OBS_NAME = re.compile(r"obs_(\d{4}-\d\d-\d\d)\.tar\.xz$")
OBS_FILE = re.compile(r"(^|/)6min/[^/]+\.csv$")
STEP_MIN = 6
LEAD_H = 72


def utc(s):
    return pd.Timestamp(s).tz_localize("UTC") if pd.Timestamp(s).tzinfo is None else pd.Timestamp(s).tz_convert("UTC")


def cycle_time(c):
    return pd.Timestamp(datetime.strptime(c, "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc))


# --- scores ---------------------------------------------------------------------------------------------
def _read_any(path):
    return pd.read_csv(path, compression="gzip" if path.endswith(".gz") else None)


def load_table(paths, keys):
    """Concatenate score tables, the later file winning on duplicate keys."""
    frames = [_read_any(p) for p in paths if p and os.path.exists(p)]
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True)
    return d.drop_duplicates(keys, keep="last").reset_index(drop=True)


def fetch_scores(work):
    """Download the cumulative score tables from the newest ensemble release. Returns their paths."""
    out = {}
    for name in ("skill_scores.csv.gz", "flood_scores.csv.gz"):
        url = releases.newest_asset("ensemble-", name, (releases.PRIVATE,))
        if url:
            out[name] = releases.download(url, os.path.join(work, name))
        else:
            print(f"no {name} on the ensemble releases")
    return out


# --- run tarballs ---------------------------------------------------------------------------------------
def run_assets(start, end):
    """{cycle: api url} of the ensemble run tarballs with start <= cycle < end."""
    out = {}
    for tag, assets in releases.assets("ensemble-").items():
        for name, url in assets.items():
            m = RUN_NAME.match(name)
            if m and start <= cycle_time(m.group(1)) < end:
                out.setdefault(m.group(1), url)
    return dict(sorted(out.items()))


def reduce_run(path, navd88=None):
    """One run tarball -> {"cycle", "updated", "stations": {st: {"t0": ms, "members": {m: [..]}}}}:
    each member's forecast from t0 to t0+72 h on the 6-minute clock (ft MLLW, None for blanks).
    Runs on Tidewatch's observation basis (no obs_basis) are moved onto StormSense's MLLW the way
    stormsense/skill.py does: their NAVD88 CSVs plus StormSense's NAVD88 offset."""
    navd88 = navd88 or {}
    with tarfile.open(path) as t:
        names = {m.name.lstrip("./"): m for m in t.getmembers()}
        latest = json.load(t.extractfile(names["latest.json"]))
        own = latest.get("obs_basis") == "stormsense"
        picks = None
        if "skill/picks_history.csv" in names:
            picks = t.extractfile(names["skill/picks_history.csv"]).read()
        out = {"cycle": latest.get("cycle"), "updated": latest.get("updated"), "obs_basis": latest.get("obs_basis", "tidewatch"),
               "stations": {}}
        for st, info in (latest.get("stations") or {}).items():
            if not info.get("t0"):
                continue
            mllw, navd = f"ens72/{st}_ens72_MLLW.csv", f"ens72/{st}_ens72_NAVD.csv"
            add = 0.0
            if not own and navd88.get(st) is not None and navd in names:
                f, add = names[navd], navd88[st]
            elif mllw in names:
                f = names[mllw]
            else:
                continue
            df = pd.read_csv(t.extractfile(f))
            tt = pd.to_datetime(df.pop("date_time_z"), utc=True)
            t0 = pd.Timestamp(info["t0"])
            grid = pd.date_range(t0, t0 + pd.Timedelta(hours=LEAD_H), freq=f"{STEP_MIN}min")
            df.index = tt
            df = df[~df.index.duplicated()]
            seen = None
            if own and "observed_ft" in df:
                # the gauge's last day before issue: fills CREST's observations past the daily obs archive
                o = pd.Series(without_bridges(df["observed_ft"].to_numpy(float)), df.index).dropna()   # no line across an outage
                if len(o):
                    og = pd.date_range(o.index[0], o.index[-1], freq=f"{STEP_MIN}min")
                    seen = {"t0": int(og[0].value // 10**6),
                            "v": [None if not np.isfinite(x) else round(float(x), 3) for x in o.reindex(og).to_numpy(float)]}
            df = df.reindex(grid)
            members = {}
            for c in df.columns:
                if not c.endswith("_ft") or c.endswith("_surge_ft") or c == "observed_ft":
                    continue
                v = df[c].to_numpy(float) + add
                if np.isnan(v).all():
                    continue
                key = "tide" if c == "astro_tide_ft" else c[:-3]
                members[key] = [None if not np.isfinite(x) else round(float(x), 3) for x in v]
            out["stations"][st] = {"t0": int(t0.value // 10**6), "members": members}
            if seen:
                out["stations"][st]["obs"] = seen
    return out, picks


def cached_runs(work, start, end, local=(), navd88=None, keep_tars=False):
    """Reduced runs with start <= cycle < end, from work/runs/<cycle>.json.gz when cached, else from
    the local tarballs or the releases. Returns ({cycle: reduced run}, picks_history csv bytes of the newest)."""
    cache = os.path.join(work, "runs")
    os.makedirs(cache, exist_ok=True)
    todo = {}
    for p in local:
        m = RUN_NAME.search(os.path.basename(p))
        if m and start <= cycle_time(m.group(1)) < end:
            todo[m.group(1)] = p
    if not local:
        todo.update(run_assets(start, end))
    runs = {}
    pick_path = os.path.join(work, "picks_history.csv")
    last = max(todo) if todo else None
    for cyc in sorted(todo):
        cp = os.path.join(cache, f"{cyc}.json.gz")
        if os.path.exists(cp) and not (cyc == last and not os.path.exists(pick_path)):
            runs[cyc] = json.load(gzip.open(cp))
            continue
        src = todo[cyc]
        try:
            if src.startswith("https://"):
                src = releases.download(src, os.path.join(work, "tars", f"ensemble_{cyc}.tar.gz"))
            r, pk = reduce_run(src, navd88)
        except Exception as e:  # a damaged tarball costs one run, not the build
            print(f"run {cyc} unreadable: {e}")
            continue
        with gzip.open(cp, "wt") as f:
            json.dump(r, f, separators=(",", ":"))
        runs[cyc] = r
        if pk is not None and cyc == last:
            open(pick_path, "wb").write(pk)
        if not keep_tars and src.startswith(os.path.join(work, "tars")):
            os.remove(src)
    picks = open(pick_path, "rb").read() if os.path.exists(pick_path) else None
    return runs, picks


# --- observations -----------------------------------------------------------------------------------------
def obs_assets(start, end):
    out = {}
    for tag, assets in releases.assets("obs-", (releases.PRIVATE,)).items():
        for name, url in assets.items():
            m = OBS_NAME.match(name)
            if m and start.normalize() <= pd.Timestamp(m.group(1), tz="UTC") < end:
                out.setdefault(m.group(1), url)
    return dict(sorted(out.items()))


def unpack_obs(tar_path, folder):
    """Write a day tarball's 6-minute CSVs (obs_<day>/6min/<ST>.csv; older ones 6min/<ST>.csv) to folder."""
    os.makedirs(folder, exist_ok=True)
    n = 0
    with tarfile.open(tar_path) as t:
        for m in t.getmembers():
            if m.isfile() and OBS_FILE.search(m.name):
                with open(os.path.join(folder, os.path.basename(m.name)), "wb") as f:
                    f.write(t.extractfile(m).read())
                n += 1
    return n


def load_obs(work, start, end, local_dir=None):
    """{station: pd.Series ft MLLW on the 6-minute clock} for start <= t < end, from the daily obs
    tarballs (cached under work/obs/<day>/), or from local_dir/<ST>.csv (time, value) for testing."""
    parts = {}
    if local_dir:
        for p in glob.glob(os.path.join(local_dir, "*.csv")):
            d = pd.read_csv(p)
            s = pd.Series(d.iloc[:, 1].to_numpy(float), index=pd.to_datetime(d.iloc[:, 0], utc=True))
            parts.setdefault(os.path.basename(p)[:-4], []).append(s)
    else:
        for day, url in obs_assets(start - pd.Timedelta(days=1), end).items():
            folder = os.path.join(work, "obs", day)
            # a folder without CSVs is a day that was unpacked wrongly or not at all: read it again
            if not glob.glob(os.path.join(folder, "*.csv")):
                tp = releases.download(url, os.path.join(work, "obs", f"obs_{day}.tar.xz"))
                unpack_obs(tp, folder)
                os.remove(tp)
            for p in glob.glob(os.path.join(folder, "*.csv")):
                d = pd.read_csv(p)
                if d.empty:
                    continue
                s = pd.Series(d.iloc[:, 1].to_numpy(float), index=pd.to_datetime(d.iloc[:, 0], utc=True))
                parts.setdefault(os.path.basename(p)[:-4], []).append(s)
    out = {}
    for st, ss in parts.items():
        s = pd.concat(ss)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        out[st] = s[(s.index >= start) & (s.index < end)].dropna()
    return out


def top_up_obs(obs, runs):
    """Add the observations the run tarballs carry (each holds the day before its issue time) where
    the daily obs archive has none yet: the archive is written once a day, so without this the
    newest forecasts would be charted without the newest water levels. The archive wins where both have a value."""
    extra = {}
    for cyc in sorted(runs):
        for st, d in runs[cyc]["stations"].items():
            o = d.get("obs")
            if o and o.get("v"):
                idx = pd.date_range(pd.Timestamp(o["t0"], unit="ms", tz="UTC"), periods=len(o["v"]), freq=f"{STEP_MIN}min")
                extra.setdefault(st, []).append(pd.Series(o["v"], index=idx, dtype=float).dropna())
    out = dict(obs)
    for st, parts in extra.items():
        s = pd.concat(([obs[st]] if st in obs else []) + parts)
        out[st] = s[~s.index.duplicated(keep="first")].sort_index()
    return out


# --- sensor health ----------------------------------------------------------------------------------------
def load_health(index_dir=None):
    index_dir = index_dir or os.path.join(REPO_ROOT, "data", "index", "obs")
    frames = [pd.read_csv(p) for p in sorted(glob.glob(os.path.join(index_dir, "stations_*.csv")))]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# --- flood levels -------------------------------------------------------------------------------------------
def load_levels(offline=False):
    """{station: {category: ft above MLLW}} from NWPS (or the saved table when offline/unreachable)."""
    from stormsense import stations as S
    from stormsense import thresholds
    table = S.load()
    if offline:
        old = json.load(open(thresholds.FALLBACK))
        return {st: S.thresholds_mllw(table[st], t) for st, t in old.items() if st in table}
    try:
        return {st: e.get("mllw", {}) for st, e in thresholds.build(table).items()}
    except Exception as e:
        print("flood levels unavailable:", e)
        return load_levels(offline=True)


def picks_table(raw):
    return pd.read_csv(io.BytesIO(raw)) if raw else pd.DataFrame(columns=["cycle", "station", "band", "pick"])
