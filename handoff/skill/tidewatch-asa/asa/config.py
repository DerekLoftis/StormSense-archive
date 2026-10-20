"""Settings for the Tidewatch Automated Statistical Analysis (ASA).

Everything a run depends on lives here, so moving the pipeline to another
machine (web4, a VIMS server, a laptop) only means editing this file or
passing the matching command-line flags to run_asa.py.
"""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ASA_ROOT = PACKAGE_DIR.parent

# Root of the Tidewatch CSV archive: the folder holding f36/, 3d/ and 30d/.
# Cycle folders can sit directly below each product (Lexie Chen's
# archive_pipeline: f36/2026-09-16_0120Z/) or nested by year and month (the
# project archive: f36/2026/09/2026-09-16T0120Z/).
ARCHIVE_ROOT = ASA_ROOT.parent / "tidewatch-archive" / "data" / "tidewatch"

OUTPUT_ROOT = ASA_ROOT / "output"
HISTORY_DIR = OUTPUT_ROOT / "history"      # running per-station forecast stats
MONTHLY_DIR = OUTPUT_ROOT / "monthly"      # month-end clipped station tables

# MLLW is published for every Tidewatch station (NOAA and USGS alike), and the
# 3d/30d observation files use the same datum, so no VDatum conversion is
# needed when observations come from the Tidewatch archive.
DATUM = "MLLW"

FORECAST_HOURS = 36
# A forecast is scored only when observations cover at least this share of
# its 36-hour window.
MIN_COVERAGE = 0.85

# Rows kept in the rolling station table (task 2, product 3).
ROLLING_DAYS = 31
# Rows shown on each station slide (task 2, product 5).
SLIDE_TABLE_DAYS = 3
# Stations per aggregate slide (task 2, product 5).
STATIONS_PER_SLIDE = 25

# ---------------------------------------------------------------------------
# Tidewatch Skill Score (Derek Loftis, Sept 2025 verification)
# ---------------------------------------------------------------------------
#   S = 0.25 * [ R2 + exp(-|Bias|/sd) + exp(-MAE/sd) + exp(-RMSE/sd) ]
# sd = population standard deviation of the observations over the evaluation
# window. Bounded on (0, 1]; a no-skill (window climatology) forecast ~0.45.
# "Site Skill" uses the same exp components with the station's trailing
# 30-day observed SD as sd, and per-station CRITIC weights.
SKILL_NORMALIZATION = "window"
REFERENCE_ERROR_FT = 1.0  # unused by the skill score; kept for older scripts
SITE_SCALE_DAYS = 30

EQUAL_WEIGHTS = {"R2": 0.25, "Bias": 0.25, "MAE": 0.25, "RMSE": 0.25}

# Calibrated weights are shrunk toward equal weights until a station has this
# many scored forecasts (weight on the calibrated value = n / (n + this)).
CALIBRATION_SHRINKAGE_N = 20

# Optional: fall back to the NOAA / USGS web services when the archive has no
# observations for part of a window. Off by default because the archive is
# the record Derek asked to validate against.
FETCH_MISSING_OBSERVATIONS = False
REQUEST_TIMEOUT = 30

# Stations Derek retired on 2026-10-15 (NN and VB sensors stale for over a year; USNW, USPF and USSV
# non-tidal or lacking data). The archiver stopped saving them; the ASA skips any old files still around.
RETIRED = {f"NN{i:02d}" for i in range(1, 11)} | {f"VB{i:02d}" for i in range(1, 11)} | {"USNW", "USPF", "USSV"}
