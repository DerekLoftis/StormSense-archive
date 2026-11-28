# Forecast diagnostics (one month at a time)

Every archived forecast run (Tidewatch f36, NWS official from NWPS, StormSense fore72) paired with
the gauge's own 6-minute observations and scored with Derek's evenly weighted skill formula
(same definitions as tidewatch-asa `asa/metrics.py`).

- `build_month.py` writes `<out>/data/<ST>.json` and `<out>/data/index.json` for one month
  (usage in its docstring). One Tidewatch run per 6-hour cycle: the first fresh publication in it,
  by upstream Last-Modified; cycles that only repeat an older run are kept and flagged stale, and
  left out of the month means.
- `viewer.html` loads `data/months.json`, then `data/<YYYY-MM>/index.json` and one station file at a
  time: run list coloured by skill, time series against observations (with the nearest NWS and
  StormSense runs), regression with 1:1 and fitted lines, statistics by lead band, and a month report
  (skill of every forecast, stitched 0-6 h forecasts, month means by lead, coverage, every forecast).
  It needs `jslib/highcharts.js` (Highcharts 6.2.0 from `stormsense/data/jslib`) next to it.

Layout of a published month: `index.html` (= viewer.html), `jslib/highcharts.js`,
`data/months.json` (`{"months": ["2026-10", ...]}`), `data/2026-10/{index,SWPT,...}.json`.

Windows: Tidewatch 36 h (bands 0-12, 12-24, 24-36), NWS and StormSense 72 h (0-12, 12-36, 36-72,
plus 0-36 for a like-for-like comparison with Tidewatch). Observations: StormSense obs archive
(`obs-YYYY-MM`, from 2026-10-14), Tidewatch 30-day CSVs before that, NWPS observed stage for JLMV
before StormSense observed it (JLMV2 stage MLLW until 2026-10-15T13:48Z, NAVD88 after).
Forecast times that fall between 6-minute observations (CNRC builds at :05, :20, ...) are matched
by linear interpolation across gaps of at most 12 minutes.
