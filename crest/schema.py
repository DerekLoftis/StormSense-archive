"""CREST's feeds: every column's type and label. The feed writer (feeds.py) writes these columns, the
ArcGIS upload (arcgis_push.py) publishes each with its type instead of letting ArcGIS guess from the
first rows (it took numbers with blank leading cells, and every column of an empty file, for text),
and the dashboard builder (arcgis_dashboard.py) reads the labels.

Types: "str", "float", "int", "date" (ISO 8601 UTC in the CSV). A third item is a string length.
"""
MAIN = ["primary", "stormsense", "petss", "nws", "tidewatch", "stofs2d", "etss"]
SHORT = {"primary": "Published", "stormsense": "StormSense model", "petss": "P-ETSS", "nws": "NWS", "tidewatch": "Tidewatch",
         "stofs2d": "STOFS-2D", "etss": "ETSS"}
# One color per guidance everywhere: the pages (templates/common.js gets this as COL) and the ArcGIS dashboard.
COLORS = {"primary": "#0b3954", "stormsense": "#00b8c4", "petss": "#d9480f", "etss": "#e8a33d", "stofs2d": "#5f3dc4",
          "stofs3d": "#9c6ade", "cbofs": "#2b8a3e", "nwm_short": "#94a3b8", "nwm_medium": "#64748b", "nws": "#c2255c",
          "psurge": "#a61e4d", "tidewatch": "#16687E", "ens_mean": "#f59f00", "stormsense_raw": "#66d9e8",
          "baseline": "#868e96", "tide": "#adb5bd", "obs": "#1f4fbf"}


def _per_member(prefix, kind, label):
    return [(f"{prefix}_{m}", kind, f"{label}: {SHORT[m]}") for m in MAIN]


STATIONS = [
    ("station", "str", "Station"), ("name", "str", "Station name"), ("lon", "float", "Longitude"), ("lat", "float", "Latitude"),
    ("period", "str", "Period"), ("best", "str", "Best guidance (id)"), ("best_name", "str", "Best guidance"),
    ("cone_pct", "float", "Observations inside the 10-90% band (%)"), ("obs_coverage_pct", "float", "Gauge record (%)"),
    ("runs", "int", "Forecasts scored"), ("events", "int", "Floods observed"), ("last_event", "str", "Latest flood"),
    ("suspect_obs", "int", "Suspect gauge (1 = yes)"), ("verdict", "str", "Summary", 1000),
    ("crest_url", "str", "CREST page", 400), ("forecast_url", "str", "Latest forecast", 400),
] + _per_member("S", "float", "Skill S")

DAILY = [
    ("station", "str", "Station"), ("name", "str", "Station name"), ("date", "date", "Day"),
    ("lon", "float", "Longitude"), ("lat", "float", "Latitude"),
] + _per_member("S", "float", "Skill S") + [
    ("max_obs_ft_mllw", "float", "Highest observed water (ft MLLW)"), ("category", "str", "Flood category reached"),
]

FLOODS = [
    ("station", "str", "Station"), ("name", "str", "Station name"), ("lon", "float", "Longitude"), ("lat", "float", "Latitude"),
    ("peak_time", "date", "Peak (UTC)"), ("peak_ft_mllw", "float", "Peak (ft MLLW)"), ("category", "str", "Flood category"),
    ("summary", "str", "Summary", 400),
] + [f for m in MAIN for f in ((f"peak_err_{m}", "float", f"Peak error (ft): {SHORT[m]}"),
                               (f"timing_err_min_{m}", "float", f"Timing error (min): {SHORT[m]}"),
                               (f"runs_{m}", "int", f"Forecasts: {SHORT[m]}"))]

LEADERBOARD = [
    ("station", "str", "Station"), ("member", "str", "Guidance (id)"), ("member_name", "str", "Guidance"),
    ("band", "str", "Lead time band (h)"), ("period", "str", "Period"), ("S", "float", "Skill S"),
    ("Bias", "float", "Bias (ft)"), ("MAE", "float", "MAE (ft)"), ("RMSE", "float", "RMSE (ft)"), ("R2", "float", "R squared"),
    ("n", "int", "Forecasts scored"),
]

DECAY = [
    ("station", "str", "Station"), ("member", "str", "Guidance (id)"), ("member_name", "str", "Guidance"),
    ("segment", "str", "Lead time segment (h)"), ("lead", "str", "Lead time"), ("lead_mid_h", "int", "Lead time (h)"),
    ("S", "float", "Skill S"), ("RMSE", "float", "RMSE (ft)"), ("period", "str", "Period"),
]

FORECASTS = [
    ("station", "str", "Station"), ("name", "str", "Station name"), ("member", "str", "Guidance (id)"),
    ("member_name", "str", "Guidance"), ("cycle", "str", "Forecast cycle"), ("issued", "date", "Issued (UTC)"), ("issued_day", "date", "Day issued"), ("issued_label", "str", "Issued"),
    ("status", "str", "Verification"),
    ("S", "float", "Skill S, whole forecast"), ("S_0_12", "float", "Skill S, 0-12 h"), ("S_12_36", "float", "Skill S, 12-36 h"),
    ("S_36_72", "float", "Skill S, 36-72 h"), ("S_0_36", "float", "Skill S, 0-36 h"), ("Bias", "float", "Bias (ft)"),
    ("MAE", "float", "MAE (ft)"), ("RMSE", "float", "RMSE (ft)"), ("R2", "float", "R squared"),
    ("peak_err_ft", "float", "Peak error (ft)"), ("span_h", "int", "Hours scored"), ("rating", "str", "Rating"),
    ("fc_peak_ft_mllw", "float", "Forecast peak (ft MLLW)"), ("fc_peak_time", "date", "Forecast peak time (UTC)"),
    ("fc_category", "str", "Flood category forecast"), ("fc_over_action_ft", "float", "Forecast peak above Action (ft)"),
    ("summary", "str", "Summary", 400), ("chart_url", "str", "Forecast chart", 400),
]

CHANGES = [("date", "date", "Date"), ("label", "str", "Change"), ("detail", "str", "Detail", 1000)]

# key -> (ArcGIS layer title, feed file, has lon/lat, columns)
FEEDS = {
    "stations": ("CREST Stations", "stations.csv", True, STATIONS),
    "daily": ("CREST Daily Skill", "daily_skill.csv", True, DAILY),
    "floods": ("CREST Flood Events", "flood_events.csv", True, FLOODS),
    "leaderboard": ("CREST Leaderboard", "leaderboard.csv", False, LEADERBOARD),
    "decay": ("CREST Lead Decay", "lead_decay.csv", False, DECAY),
    "forecasts": ("CREST Forecasts", "forecasts.csv", False, FORECASTS),
    "changes": ("CREST Changes", "changes.csv", False, CHANGES),
}


def columns(key):
    return [f[0] for f in FEEDS[key][3]]


ESRI = {"str": ("esriFieldTypeString", "sqlTypeNVarchar"), "float": ("esriFieldTypeDouble", "sqlTypeFloat"),
        "int": ("esriFieldTypeInteger", "sqlTypeInteger"), "date": ("esriFieldTypeDate", "sqlTypeTimestamp2")}


def esri_fields(key):
    """{column: ArcGIS field properties} for one feed."""
    out = {}
    for f in FEEDS[key][3]:
        t, sql = ESRI[f[1]]
        d = {"type": t, "sqlType": sql, "alias": f[2]}
        if f[1] == "str":
            d["length"] = f[3] if len(f) > 3 else 256
        elif f[1] == "date":
            d["length"] = 8
        out[f[0]] = d
    return out
