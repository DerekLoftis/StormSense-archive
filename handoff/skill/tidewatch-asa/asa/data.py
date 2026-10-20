"""Read archived Tidewatch forecasts and observations."""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, runfiles
from .stations import STATIONS

STATION_INFO = {s["tidewatch_id"]: s for s in STATIONS}


def station_name(station_id):
    info = STATION_INFO.get(station_id)
    return info["name"] if info else station_id


@dataclass
class Forecast:
    station: str
    issued: pd.Timestamp          # Tidewatch run time shared by all stations in the run
    start: pd.Timestamp           # first forecast time (latest observation used)
    end: pd.Timestamp
    archive_cycle: str            # archive folder the file came from
    cycle_path: Path              # full path of that folder
    data: pd.DataFrame            # time, pa30, anom, px30


def _read_forecast(path):
    df = pd.read_csv(path)
    if not {"time", "px30"}.issubset(df.columns):
        return None
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    for col in ("pa30", "anom", "px30"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["time"])
    if df["px30"].isna().all():
        return None
    return df


def archive_roots(archive_root=None):
    """One archive root or several (a list), each holding f36/, 3d/, 30d/."""
    roots = archive_root or config.ARCHIVE_ROOT
    if isinstance(roots, (str, Path)):
        roots = [roots]
    return [Path(r) for r in roots]


def _cycle_dirs(roots, product, pattern):
    """Archive cycle folders under a product, across all roots, oldest first.

    Works with Lexie's flat layout (f36/<cycle>/) and the archive's nested
    one (f36/<YYYY>/<MM>/<cycle>/). Folder names start with the date and
    time, so sorting by name sorts by time.
    """
    dirs = {Path(d) for r in roots for d in runfiles.run_dirs_with(r / product, pattern)}
    return sorted(dirs, key=lambda d: (_cycle_key(d), str(d)))


def _cycle_key(cycle_dir):
    """Sortable time stamp from a cycle folder name (2026-09-16_0120Z or
    2026-09-16T0120Z, optionally followed by a suffix)."""
    return cycle_dir.name.replace("_", "T")[:15]


def load_forecasts(archive_root=None, datum=None, stations=None):
    """All archived f36 forecasts, one per station and Tidewatch run.

    Station files in one Tidewatch run start at different times (each starts
    at that gauge's latest observation, which lags by up to ~1.5 h), so the
    run's issuance time is the latest start among its stations, rounded up to
    the hour. Every station in the run shares that issuance time.

    The archive can hold the same forecast more than once (two downloads in
    the same hour, or a station whose file Tidewatch did not update). A
    forecast is identified by its station and first forecast time; the first
    copy wins, so a stale file stays with the run that produced it. Files that
    start more than a day before the run (e.g. CBBT, frozen since 2022) are
    skipped as stale.
    """
    roots = archive_roots(archive_root)
    datum = datum or config.DATUM
    seen = {}
    for cycle_dir in _cycle_dirs(roots, "f36", f"*_f36_{datum}.csv"):
        files = []
        for name in runfiles.glob(cycle_dir, f"*_f36_{datum}.csv"):     # loose or packed in run.tar
            station = Path(name).name.split("_")[0]
            if station in config.RETIRED:
                continue
            df = _read_forecast(runfiles.open_text(cycle_dir, name))
            if df is not None:
                files.append((station, df, df["time"].min()))
        if not files:
            continue
        latest = max(t0 for _, _, t0 in files)
        issued = latest.ceil("h")
        for station, df, start in files:
            if stations and station not in stations:
                continue
            if issued - start > pd.Timedelta(days=1):
                continue
            key = (station, start)
            if key in seen:
                continue
            seen[key] = Forecast(
                station=station,
                issued=issued,
                start=start,
                end=df["time"].max(),
                archive_cycle=cycle_dir.name,
                cycle_path=cycle_dir,
                data=df,
            )
    return sorted(seen.values(), key=lambda f: (f.station, f.issued))


def load_observations(archive_root=None, datum=None):
    """Observed water levels per station, merged from every 3d and 30d file.

    Later archive snapshots overwrite earlier ones where they overlap, so the
    series is the most recent published value at each time step.
    """
    roots = archive_roots(archive_root)
    datum = datum or config.DATUM
    frames = {}
    for product in ("30d", "3d"):
        for cycle_dir in _cycle_dirs(roots, product, f"*_{product}_{datum}.csv"):
            for name in runfiles.glob(cycle_dir, f"*_{product}_{datum}.csv"):
                station = Path(name).name.split("_")[0]
                if station in config.RETIRED:
                    continue
                try:
                    df = pd.read_csv(runfiles.open_text(cycle_dir, name))
                except Exception:
                    continue
                if not {"time", "feet"}.issubset(df.columns):
                    continue
                df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
                df["observed"] = pd.to_numeric(df["feet"], errors="coerce")
                df["snapshot"] = _cycle_key(cycle_dir)
                frames.setdefault(station, []).append(df[["time", "observed", "snapshot"]])
    obs = {}
    for station, parts in frames.items():
        df = pd.concat(parts).dropna(subset=["time", "observed"])
        df = df.sort_values(["time", "snapshot"]).drop_duplicates("time", keep="last")
        obs[station] = df.set_index("time")["observed"]
    return obs


def datum_offset(forecast_mllw_path, forecast_navd_path):
    """MLLW minus NAVD88 at a station, from Tidewatch's own two f36 files.

    Used only when USGS observations (published in NAVD88) are fetched to
    fill a gap in an MLLW comparison.
    """
    a = pd.read_csv(forecast_mllw_path)["pa30"]
    b = pd.read_csv(forecast_navd_path)["pa30"]
    return float(np.nanmedian(a.values - b.values))


def fetch_observations(station_id, start, end):
    """NOAA / USGS web-service fallback (needs network access to both)."""
    import requests

    info = STATION_INFO.get(station_id)
    if not info:
        return pd.Series(dtype=float)
    if info["source"] == "NOAA":
        r = requests.get(
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
            params={
                "product": "water_level", "application": "TidewatchASA",
                "begin_date": start.strftime("%Y%m%d %H:%M"),
                "end_date": end.strftime("%Y%m%d %H:%M"),
                "datum": "MLLW", "station": info["station_no"],
                "time_zone": "gmt", "units": "english", "format": "json",
            },
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=float)
        s = pd.Series(pd.to_numeric(df["v"], errors="coerce").values,
                      index=pd.to_datetime(df["t"], utc=True))
        return s.dropna()
    r = requests.get(
        "https://waterservices.usgs.gov/nwis/iv/",
        params={
            "format": "json", "sites": info["station_no"], "parameterCd": "62620",
            "startDT": start.strftime("%Y-%m-%dT%H:%MZ"),
            "endDT": end.strftime("%Y-%m-%dT%H:%MZ"), "siteStatus": "all",
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    ts = r.json()["value"]["timeSeries"]
    if not ts:
        return pd.Series(dtype=float)
    df = pd.DataFrame(ts[0]["values"][0]["value"])
    s = pd.Series(pd.to_numeric(df["value"], errors="coerce").values,
                  index=pd.to_datetime(df["dateTime"], utc=True))
    return s.dropna()  # NAVD88: caller shifts to MLLW with datum_offset()


def pair(forecast, observed):
    """Forecast and observation on matching 6-minute time steps."""
    f = forecast.data.set_index("time")
    if observed is None or observed.empty:
        return pd.DataFrame(columns=["predicted", "observed", "astronomical"])
    window = observed.loc[forecast.start:forecast.end]
    df = pd.DataFrame({
        "predicted": f["px30"],
        "astronomical": f.get("pa30"),
    }).join(window.rename("observed"), how="inner").dropna(subset=["predicted", "observed"])
    return df


def coverage(forecast, paired):
    expected = (forecast.end - forecast.start) / pd.Timedelta(minutes=6) + 1
    return min(1.0, len(paired) / expected) if expected > 0 else 0.0

