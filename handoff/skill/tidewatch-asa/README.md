# Tidewatch Automated Statistical Analysis (ASA)

Scores every archived Tidewatch 36-hour forecast (px30) against the water level
that was later observed, then writes the plots, tables and PowerPoint described
in goals 2 and 3 of the Tidewatch project brief.

Observations come from the Tidewatch archive itself: the 3d (and 30d) CSVs
saved after each forecast. Those are the same gauge records Tidewatch
publishes, in the same datum, so no NOAA/USGS download or VDatum conversion is
needed. On the September 2026 captures this reproduces the NOAA-API numbers in
Lexie Chen's `archive_pipeline/results` to within about 0.01 ft RMSE at 23 of
the 26 stations she scored.

## Run

```sh
python3 -m pip install -r requirements.txt
python3 run_asa.py                 # newest cycle that observations now cover
python3 run_asa.py --target 2026-09-16T01:00Z
python3 run_sensitivity.py         # skill-score sensitivity analysis
python3 run_validation.py          # validation animations (run_asa.py also calls this)
python3 run_aliasing.py            # aliasing check of Tidewatch's observed series
```

`--archive` points at any folder holding `f36/`, `3d/`, `30d/` (flat, as in
Lexie's pipeline, or nested by year/month, as in `../tidewatch-archive`).
Defaults are in `asa/config.py`. `--fetch-missing` fills observation gaps from
the NOAA CO-OPS and USGS NWIS services (USGS NAVD88 is shifted to MLLW with the
offset Tidewatch itself uses, read from its MLLW and NAVD f36 files).

Run it from cron a few minutes after each 6-hourly archive run:

```
20 0,6,12,18 * * *  cd /path/to/tidewatch-asa && python3 run_asa.py >> asa.log 2>&1
```

Each run rescores anything new, so it is safe to run as often as you like.
A forecast is scored once observations cover 85% of its 36 hours
(`MIN_COVERAGE`); the report targets the newest cycle scored at at least half
of the stations.

## Outputs (`output/`)

| Path | Product |
|---|---|
| `cycles/<cycle>/plots/<ID>_<cycle>_timeseries.png` | 1. px30 vs observed (plus pa30) for that forecast |
| `cycles/<cycle>/plots/<ID>_<cycle>_regression.png` | 2. regression, legend top-left: points, fit with intercept (y = mx + b), fit through origin, R², Bias, MAE, RMSE, Tidewatch Skill, Site Skill |
| `cycles/<cycle>/tables/<ID>_forecast_stats_past31d.{csv,xlsx}` | 3. station table, past 31 days, one row per forecast |
| `monthly/<ID>_<Station_name>_<Month>_<Year>.{csv,xlsx}` | 3. month-end archive of the station table, written once the month is over |
| `cycles/<cycle>/all_stations_forecast_stats_<cycle>.{csv,xlsx}` | 4. every station for that cycle |
| `cycles/<cycle>/Tidewatch_ASA_<cycle>.pptx` | 5. title slide, one slide per station (1, 2 and the past-3-day table), all-station tables at 25 stations per slide |
| `cycles/<cycle>/validation/<ID>_<cycle>_{3d,30d}_overlap.png` | goal 3: every 6-hourly 36-h forecast in the window overlaid in a rainbow sequence, each with its stats |
| `cycles/<cycle>/validation/<ID>_<cycle>_{3d,30d}_regression.png` | goal 3: matching regression with a table of each forecast's issuance, period and stats |
| `history/forecast_stats_history.csv` | every scored forecast, all metrics and skill components |
| `sensitivity/` | skill-score sensitivity analysis (see FINDINGS.md) |
| `validation_animations/<ID>/36hr/<ID>_<date>_<weekday>_<hour>_36hr.mp4` | ~5 s animation of each 36-h forecast, in the format of Derek's Oct 2025 set. Drawn once the window has passed. Includes the NWS NWPS forecast over the same 36 h when one was archived. NOAA stations only. |
| `validation_animations/<ID>/72hr/..._72hr.mp4` | ~6 s version drawn once 72 h have passed. It also verifies the NWS forecast out to 72 h, with grey diamonds beyond the Tidewatch window. |
| `validation_animations/<ID>/<ID>_Tidewatch_vs_NWS_36hr_table.png` | the last 14 cycles in the layout of Derek's SWPT/YRCG/WMPT/LEWI tables (ΔRMSE = NWS − Tidewatch) |
| `validation_animations/<ID>/<ID>_<Month>_<Year>_Tidewatch_vs_NWS.xlsx` | Derek's workbook layout: ReadMe, Summary of live formulas, T## and N## sheets |
| `validation_animations/tw_vs_nws_stats.csv`, `nwps_datum_check.csv` | every validated cycle's statistics; the NWPS-minus-Tidewatch observation offset per gauge |
| `aliasing/ALIASING.md`, `aliasing_by_station.csv`, `duplicate_series.csv` | aliasing check of Tidewatch's observed series (`run_aliasing.py`) |

## Statistics

x = observed, y = Tidewatch px30, on matching 6-minute steps.

* **R²**: squared Pearson correlation (Excel trendline R²). **NSE** (1 − SSE/SST,
  what `archive_pipeline` reported as R2) is kept in the tables too.
* **Bias** = mean(px30 − observed), **MAE**, **RMSE** in ft.
* **Peak error** = forecast maximum − observed maximum in the window.

## Tidewatch Skill Score

Derek's formula (Sept 2025 verification script), on a 0-1 scale:

    S = 0.25 * [ R² + e^(−|Bias|/σ) + e^(−MAE/σ) + e^(−RMSE/σ) ]

σ is the population SD of the observations over the evaluation window. A
perfect forecast scores 1.000. A no-skill forecast (window climatology) scores
about 0.45. Scores are reported to 3 decimals.

| Score | σ | Weights |
|---|---|---|
| **Tidewatch Skill** | SD of observations in the 36-h window (Derek's formula) | 25% each |
| **Site Skill** | the station's observed SD over the trailing 30 days | CRITIC weights for that station, learned from its earlier forecasts. They are shrunk toward 25% each until the station has about 20 forecasts. |
