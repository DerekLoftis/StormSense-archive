# StormSense-archive

Private home of StormSense: the code that makes the StormSense forecasts, the archive of every run, and the
data it ingests. The public repos DerekLoftis/StormSense-latest and DerekLoftis/StormSense-ensemble only hold
what is published (html charts, csv, json). Tidewatch's own archive stays in DerekLoftis/tidewatch_archive.

## The StormSense run

`.github/workflows/stormsense.yml` runs `python -m stormsense.build` at 00:20, 06:20, 12:20 and 18:20 UTC:

1. NWS flood categories (NWPS) and 40 days of every gauge's own observations, straight from NOAA CO-OPS,
   USGS, NWPS, the VIMS THREDDS gauge, FloodVISION and GreenStream (`stormsense/obs.py`; no web4).
2. StormSense's harmonic tide per gauge (`harmonics/`, refit monthly by `harmonics.yml`), locked to m30,
   the 30-day mean water level (John Boon's VIMS tide).
3. Every forecast member: NWS P-ETSS and ETSS, STOFS-2D and 3D, CBOFS, NWM, the NWS official forecast, NHC
   P-Surge, VIMS Tidewatch's 36-hour forecast, tide + persistence, and the StormSense model.
4. Skill of every archived run (`stormsense/skill.py`): Derek's SS-Skill per lead band and per 6 hours,
   peak-height and peak-timing errors, hits, misses and false alarms at each NWS flood stage; the
   adaptive pick of each station's guidance.
5. The StormSense Latest 72-hour forecast, its uncertainty cone from its own past errors, the chance of
   reaching each flood stage, and XHW labels on the forecast high waters.
6. Both sites (`site/latest`, `site/ensemble`), pushed to the public repos' `site` branches and deployed
   to GitHub Pages by their `deploy.yml` (needs the secret `STORMSENSE_PUBLISH_TOKEN`).
7. The run's archive on this repo's releases: `ensemble_<cycle>.tar.gz` and the cumulative scores on
   `ensemble-YYYY-MM`, `fore72_history.csv.gz` on `stormsense-YYYY-MM`.

Station list and sources: `stormsense/stations.csv` (regenerate with `tools/make_stations.py`).
Tests: `python -m pytest tests`. A pull request runs a dry build of a few stations.

## StormSense Latest runs

`.github/workflows/archive-latest.yml` runs at :50 every hour. `archiver/latest_archive.py` reads
https://derekloftis.github.io/StormSense-latest/latest.json and, when that build is not in the index yet,
downloads everything the site's index.html links (fore72, 7day and 30day html, csv and json for every
station) plus index.html, latest.json, thresholds.json, build_log.txt, the metadata JSON and jslib/, and
uploads it as one `stormsense_<build time>.tar.xz` (paths as on the site, `manifest.csv` inside) to the
release `stormsense-YYYY-MM`. Unpacked, the charts open in a browser as they did on the site.

Index, committed by the workflow:

- `data/index/stormsense-latest/runs_YYYY-MM.csv`: one row per build: `cycle` (00/06/12/18Z slot the
  build ran in), `updated_utc` (build time from latest.json), `tag`, `asset`, `bytes`, `sha256`, `files`,
  `missing`, `stations`, `fore72_petss` / `fore72_tidewatch` (stations whose fore72 came from P-ETSS or
  fell back to Tidewatch), `petss_cycle`, `upstream_failed` (files the build itself could not fetch).
- `data/index/stormsense-latest/stations_YYYY-MM.csv`: one row per build, station, product and datum:
  which of html/csv/json it holds, CSV `rows`, `first_time_z`, `last_time_z`, and the JSON `source`.

To read a run: `gh release download stormsense-2026-10 -R DerekLoftis/StormSense-archive -p 'stormsense_2026-10-20T1156Z.tar.xz'`
then `tar xJf` it.

GitHub starts the publish runs hours late or skips them, so tarballs are named by build time, not by
cycle, and the hourly check catches whichever builds land.

## StormSense observations

`.github/workflows/archive-obs.yml` runs daily at 04:10 UTC. `archiver/obs_archive.py` reads every gauge
StormSense observes for itself straight from its source (NOAA CO-OPS 6-minute, USGS NWIS 62620, the VIMS
THREDDS gauge BRDH, the FloodVISION USLN-X and GreenStream CNRC archives; no web4), with the fetch and QC
code of DerekLoftis/StormSense_forecastmodel (`ssmodel/obs.py`), and uploads one `obs_YYYY-MM-DD.tar.xz`
per UTC day to the release `obs-YYYY-MM`:

- `raw/<ST>.csv`: every reading as the source gave it, ft MLLW, with its QC flag (`ok`, `range`, `spike`, `flatline`)
- `6min/<ST>.csv`: the QC'd series on the 6-minute clock, ft MLLW (what the model uses)
- `stations.csv` (source, id, NAVD88-above-MLLW offset used) and `report.csv` (per gauge counts and errors)

Each run catches up the last 10 finished days, so a skipped run loses nothing; dispatching it with `since`
backfills further (USGS keeps about 120 days online, CO-OPS years). Index, committed by the workflow:

- `data/index/obs/days_YYYY-MM.csv`: one row per day: `gauges`, `gauges_ok`, `readings`, `flagged`,
  `six_min`, `failed` (gauges with no readings that day), `asset`, `bytes`, `sha256`.
- `data/index/obs/stations_YYYY-MM.csv`: one row per day and gauge: `readings`, `flag_range`,
  `flag_spike`, `flag_flatline`, `six_min`, `coverage_pct` (of the day's 240 six-minute slots),
  `first_time_z`, `last_time_z`, `error`.

## handoff/

Material from the Tidewatch project; see `handoff/README.md`.
