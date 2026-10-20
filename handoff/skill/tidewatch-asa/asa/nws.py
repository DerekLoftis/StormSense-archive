"""NWS NWPS stage forecasts from the shared archive, matched to Tidewatch runs.

The archive thread saves api.water.noaa.gov/nwps/v1/gauges/<lid>/stageflow
every 6 hours under data/nwps/YYYY/MM/<capture>/<LID>_stageflow.json.gz. Each
file holds the gauge's recent observations and its latest forecast (hourly,
about 72 h or more), both in ft. Tidal gauges marked "(IN MLLW)" are on MLLW,
the same datum as the Tidewatch MLLW files.
"""
import gzip
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import runfiles

# Tidewatch station -> NWPS gauge (location id). NOAA stations only; CBBT and
# MNPT have NWPS gauges (CHBV2, MNPV2) but no live Tidewatch forecast.
NWPS_LID = {
    "BISH": "BISM2", "DAHL": "NCDV2", "KIPT": "KPTV2", "LEWI": "LWTV2",
    "SWPT": "SWPV2", "WACH": "WAHV2", "WADC": "WASD2", "WMPT": "WNDV2",
    "YRCG": "YKTV2", "SLIM": "SLIM2", "CBBT": "CHBV2", "MNPT": "MNPV2",
    # USGS gauges that NWS also forecasts (IEM HML archive). USCB, USNR and
    # USSB are left out: Tidewatch publishes other gauges' data under them.
    "USFM": "FTMV2", "USSF": "NMDV2", "USJT": "JSFV2", "USPT": "ELZV2", "USLI": "LHNV2",
}

# A datum shift is applied only when NWPS and Tidewatch observations differ by
# a steady amount (Derek's Oct 2025 check: +0.044 ft at WMPT, +0.045 ft at
# YRCG corrected; +0.002-0.003 ft at LEWI and SWPT left alone).
SHIFT_MIN_FT = 0.01
SHIFT_MAX_SD_FT = 0.05
# Use the capture taken closest to the Tidewatch run, within this window.
CAPTURE_WINDOW = (pd.Timedelta(hours=-1), pd.Timedelta(hours=3))
MAX_FORECAST_AGE = pd.Timedelta(hours=12)


@dataclass
class NwsForecast:
    lid: str
    issued: pd.Timestamp
    capture: str
    series: pd.Series          # hourly stage forecast, ft (raw NWPS datum)


def _series(block):
    rows = [(d["validTime"], d["primary"]) for d in block.get("data", [])
            if d.get("primary") is not None and d["primary"] > -900]
    if not rows:
        return pd.Series(dtype=float)
    t, v = zip(*rows)
    s = pd.Series(np.asarray(v, float), index=pd.to_datetime(list(t), utc=True))
    return s[~s.index.duplicated(keep="last")].sort_index()


def _capture_time(name):
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})?Z", name)
    if not m:
        return None
    return pd.Timestamp(f"{m.group(1)}T{m.group(2)}:{m.group(3) or '00'}", tz="UTC")


class NwpsArchive:
    def __init__(self, nwps_root):
        self.root = Path(nwps_root)
        self._cache = {}

    def _files(self):
        """{lid: [(run folder, file name)]} over every capture, oldest first; one listing per run."""
        if not hasattr(self, "_index"):
            idx = {}
            for run in runfiles.run_dirs_with(self.root, "*_stageflow.json*"):
                for name in runfiles.glob(run, "*_stageflow.json*"):
                    idx.setdefault(Path(name).name.split("_")[0], []).append((run, name))
            self._index = idx
        return self._index

    def _captures(self, lid):
        if lid in self._cache:
            return self._cache[lid]
        out = []
        for run, name in self._files().get(lid, []):
            t = _capture_time(Path(run).name)
            if t is None:
                continue
            try:
                d = json.load(runfiles.open_text(run, name))     # loose or packed in run.tar
            except (OSError, ValueError, KeyError):
                continue
            fc = d.get("forecast") or {}
            out.append({
                "capture": Path(run).name, "time": t,
                "issued": pd.to_datetime(fc.get("issuedTime"), utc=True, errors="coerce"),
                "forecast": _series(fc), "observed": _series(d.get("observed") or {}),
            })
        self._cache[lid] = out
        return out

    def observed(self, lid):
        """Every archived NWPS observation for a gauge, later captures winning."""
        obs = [c["observed"] for c in self._captures(lid) if not c["observed"].empty]
        if not obs:
            return pd.Series(dtype=float)
        s = pd.concat(obs, sort=True)
        return s[~s.index.duplicated(keep="last")].sort_index()

    def forecast_for(self, lid, run_time, start):
        """The NWS forecast on hand when Tidewatch issued run_time."""
        best = None
        for c in self._captures(lid):
            dt = c["time"] - run_time
            if not (CAPTURE_WINDOW[0] <= dt <= CAPTURE_WINDOW[1]) or c["forecast"].empty:
                continue
            if pd.isna(c["issued"]) or start - c["issued"] > MAX_FORECAST_AGE:
                continue
            if best is None or abs(dt) < abs(best["time"] - run_time):
                best = c
        if best is None:
            return None
        return NwsForecast(lid, best["issued"], best["capture"], best["forecast"])


def datum_shift(nwps_obs, tidewatch_obs):
    """Median and SD of (NWPS obs - Tidewatch obs) at matching times."""
    if nwps_obs.empty or tidewatch_obs is None or tidewatch_obs.empty:
        return np.nan, np.nan, 0
    j = pd.concat([nwps_obs.rename("n"), tidewatch_obs.rename("t")], axis=1, sort=True).dropna()
    if len(j) < 10:
        return np.nan, np.nan, len(j)
    d = j["n"] - j["t"]
    return float(d.median()), float(d.std(ddof=0)), len(j)


def applied_shift(median, sd):
    """Shift to subtract from NWS values, or 0 when none is warranted."""
    if pd.isna(median) or pd.isna(sd) or sd > SHIFT_MAX_SD_FT or abs(median) < SHIFT_MIN_FT:
        return 0.0
    return round(median, 3)


class HmlArchive:
    """NWS forecasts and observations from IEM's archive of issued HML products.

    Files: <dir>/<LID>_forecasts.csv (station, issued[UTC], ..., forecast_valid[UTC],
    primary_value) and <LID>_obs.csv (station, valid[UTC], <name>[ft]), as
    downloaded from mesonet.agron.iastate.edu/cgi-bin/request/hml.py. Same
    interface as NwpsArchive, so validation can use either.
    """

    def __init__(self, hml_dir):
        self.dir = Path(hml_dir)
        self._fc, self._obs = {}, {}

    def _forecasts(self, lid):
        if lid not in self._fc:
            p = self.dir / f"{lid}_forecasts.csv"
            out = {}
            if p.exists():
                df = pd.read_csv(p)
                if len(df):
                    df["issued"] = pd.to_datetime(df["issued[UTC]"], utc=True)
                    df["valid"] = pd.to_datetime(df["forecast_valid[UTC]"], utc=True)
                    df["v"] = pd.to_numeric(df["primary_value"], errors="coerce")
                    df = df[df["v"] > -900]
                    for t, g in df.groupby("issued"):
                        s = g.set_index("valid")["v"].sort_index()
                        out[t] = s[~s.index.duplicated(keep="last")]
            self._fc[lid] = out
        return self._fc[lid]

    def observed(self, lid):
        if lid not in self._obs:
            p = self.dir / f"{lid}_obs.csv"
            s = pd.Series(dtype=float)
            if p.exists():
                df = pd.read_csv(p)
                vcol = [c for c in df.columns if c not in ("station", "valid[UTC]")]
                if len(df) and vcol:
                    s = pd.Series(pd.to_numeric(df[vcol[0]], errors="coerce").values,
                                  index=pd.to_datetime(df["valid[UTC]"], utc=True)).dropna()
                    s = s[(s > -900)]
                    s = s[~s.index.duplicated(keep="last")].sort_index()
            self._obs[lid] = s
        return self._obs[lid]

    def forecast_for(self, lid, run_time, start):
        """Latest NWS issuance on hand at the Tidewatch run (issued before run_time + 1 h)."""
        fc = self._forecasts(lid)
        cands = [t for t in fc if t <= run_time + pd.Timedelta(hours=1) and start - t <= MAX_FORECAST_AGE]
        if not cands:
            return None
        t = max(cands)
        return NwsForecast(lid, t, "IEM HML archive", fc[t])
