---
name: matthew-2016
description: Hurricane Matthew 2016 NWS forecast verification and Word report for Derek (statistics thread), files and findings
metadata:
  type: project
---

- Hurricane Matthew 2016 (Derek 2026-10-08): IEM files in input/nws_hml_oct2016/ (13 gauges with HML forecasts; CFW tide tables incl CBBT, Duck; PFM QPF; PNS storm totals with lat/lon) + CO-OPS hourly CBBT/Duck pulled via WebFetch (CO-OPS API blocked to curl; WebFetch reads it verbatim). run_matthew_2016.py -> output/matthew_2016/ (FINDINGS.md, xlsx, figures, station map using Natural Earth geojson from raw.githubusercontent). Findings: surge peak underforecast until ~6 h out; tide tables high-biased +0.2-0.7 ft; rainfall underforecast ~3x in Hampton Roads. NWPS gauge metadata (lat/lon, flood cats) readable via WebFetch of api.water.noaa.gov/nwps/v1/gauges/<LID>.
- Matthew Word report (Derek 2026-10-08 23:36Z, for his paper on street-level inundation and 2050 compound flooding): output/matthew_2016/report/ (4-page docx+pdf, Hampton Roads figs without flood thresholds, from run_matthew_2016_report_figs.py and build_matthew_2016_report.js via docx-js). HRSD 15-min pump-station rain (74 gauges) in input/hrsd_rain_oct2016/. This thread is at its 24 attached-outputs cap: link files by /mnt/project-files path in replies.

Related: [[tidewatch-asa]].
