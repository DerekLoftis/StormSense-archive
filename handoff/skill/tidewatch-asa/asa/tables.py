"""Forecast statistics history, rolling station tables, aggregate tables."""
import calendar
import re

import pandas as pd

from . import config
from .data import station_name

HISTORY_FILE = "forecast_stats_history.csv"

# Column order of the station tables Derek asked for: forecast date/time first,
# then R2, Bias, MAE, RMSE and the Tidewatch Skill score.
TABLE_COLUMNS = ["Issued (UTC)", "R2", "Bias (ft)", "MAE (ft)", "RMSE (ft)", "SD obs (ft)",
                 "Tidewatch Skill", "Site Skill", "NSE", "Peak error (ft)", "Coverage", "N"]


def to_table(history, lead=()):
    t = history.sort_values(["issued", "station"], kind="stable").copy()
    t["Issued (UTC)"] = t["issued"].dt.strftime("%Y-%m-%d %H:%MZ")
    t = t.rename(columns={"Bias": "Bias (ft)", "MAE": "MAE (ft)", "RMSE": "RMSE (ft)", "SDobs": "SD obs (ft)",
                          "Skill": "Tidewatch Skill", "SiteSkill": "Site Skill",
                          "PeakError": "Peak error (ft)"})
    out = t[list(lead) + TABLE_COLUMNS].copy()
    for c in ["R2", "Bias (ft)", "MAE (ft)", "RMSE (ft)", "SD obs (ft)", "NSE", "Peak error (ft)", "Coverage"]:
        out[c] = out[c].round(4)
    for c in ["Tidewatch Skill", "Site Skill"]:
        out[c] = out[c].round(3)
    return out


def load_history(path):
    if not path.exists():
        return pd.DataFrame()
    h = pd.read_csv(path)
    h["issued"] = pd.to_datetime(h["issued"], utc=True)
    return h


def save_history(history, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    h = history.sort_values(["station", "issued"]).copy()
    h["issued"] = h["issued"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    h.to_csv(path, index=False)


def write_station_table(history, station, end, out_dir):
    """Product 3: the station's forecast statistics for the past 31 days."""
    h = history[(history["station"] == station)
                & (history["issued"] > end - pd.Timedelta(days=config.ROLLING_DAYS))
                & (history["issued"] <= end)]
    table = to_table(h)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{station}_forecast_stats_past{config.ROLLING_DAYS}d"
    table.to_csv(base.with_suffix(".csv"), index=False)
    _to_xlsx(table, base.with_suffix(".xlsx"), f"{station} {config.ROLLING_DAYS}d")
    return table


def write_aggregate(history, issued, out_dir):
    """Product 4: every station's statistics for one forecast cycle."""
    h = history[history["issued"] == issued].copy()
    h["Station"] = h["station"]
    h["Name"] = h["station"].map(station_name)
    t = to_table(h, lead=("Station", "Name")).reset_index(drop=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = issued.strftime("%Y-%m-%d_%H%MZ")
    base = out_dir / f"all_stations_forecast_stats_{stamp}"
    t.to_csv(base.with_suffix(".csv"), index=False)
    _to_xlsx(t, base.with_suffix(".xlsx"), "All stations")
    return t


def _safe(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def write_monthly(history, year, month, out_dir):
    """Month-end archive: each station's table clipped to one calendar month.

    Named <ID>_<Station name>_<Month>_<Year>.xlsx (and .csv).
    """
    start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    h = history[(history["issued"] >= start) & (history["issued"] < end)]
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for station, g in h.groupby("station"):
        name = f"{station}_{_safe(station_name(station))}_{calendar.month_name[month]}_{year}"
        table = to_table(g)
        table.to_csv(out_dir / f"{name}.csv", index=False)
        _to_xlsx(table, out_dir / f"{name}.xlsx", f"{station} {calendar.month_abbr[month]} {year}")
        written.append(name)
    return written


def _to_xlsx(df, path, sheet):
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name=sheet[:31], index=False)
        ws = xw.sheets[sheet[:31]]
        ws.freeze_panes = "A2"
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(48, width + 2)
