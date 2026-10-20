---
name: tidewatch-datum-charts
description: Datum-annotated Tidewatch Highcharts pages for the 11 NOAA stations (Derek 2026-10-09), where the code, NOAA datums and style choices live
metadata:
  type: project
  modified: 2026-10-09T01:25:50.876Z
---
Built in the capture thread (session_016GxoBiqvzUfqbsRBs6Ryjd) at Derek's request, 2026-10-09.
- Folder: /mnt/project-files/tidewatch-datum-charts/ (add_datums.py one chart; make_noaa_charts.py all NOAA stations from the latest f36 run -> stations/ + summary.csv; jslib/ for --inline; vims_logo.png).
- Derek's spec: gray datum lines (HAT, MHHW, MSL, MLLW, LAT, NAVD88; MLLW/NAVD solid), m30 in VIMS teal #16687E, HAT+1/+2 dotted (more when water exceeds HAT+2), labels in right margin, disclaimer "Disclaimer: Tidewatch Charts(TM) are automated storm tide forecasts; User assumes all risk" under the x-axis title, VIMS logo at 50% in first clear corner (TR, TL, BR, BL). Pages are self-contained (libraries inlined) because Derek's browser stuck at "Loading...." with CDN scripts.
- NOAA datums: noaa_datums/<id>.json, mdapi datums.json fetched by the archive repo's "NOAA station datums" workflow (sandbox and WebFetch can't reach tidesandcurrents). Values are above STND; script subtracts MLLW. CBBT and MNPT have no NOAA NAVD88.
- m30 = 30-day mean of pickle observations (NAVD), moved to MLLW with Tidewatch's own CSV offset.
- NOAA's current HAT/LAT differ from the Classic chart's HAT83/LAT83 (SWPT 3.70/-0.87 vs 3.53/-0.67).
Related: [[tidewatch-archive]], [[tidewatch-asa]].
- USGS stations (2026-10-09, Derek chose VDatum grids since VDatum's web API fails for Virginia): make_usgs_charts.py + vdatum_grid.py -> stations_usgs/ (48 charts, 24 Tidewatch IDs; summary_usgs.csv). Grids in vdatum_grids/ (fetched by the archive repo's VDatum grids workflow). Finding: the 2024 IGS14 releases (VAswchb11, MDVAechb11, DEVAemb23) put NAVD88 ~1 ft too high vs NOAA bench marks (inferred: tss tied to a geoid surface, not NAVD88); the 2019 MDVAchb12 and NCinner11 agree; fixed with a local offset to MDVAchb12 (DEVAemb23: calibrated at WACH). HAT/LAT estimated by GT-scaling the nearest NOAA station. USGS MLLW charts are rebased from Tidewatch's fixed 1.645 ft offset onto VDatum MLLW. USBB, USPF, USNW are nontidal in VDatum (only LMSL and NAVD88 drawn, Tidewatch basis kept). USLI's latest data is from 2019. GS01/GS02 (StormSense, coords from the VIMS station list) added to the same set (52 charts); both publish one series, which is GS02 (Pamunkey), confirmed by Derek 2026-10-09, and Tidewatch's GS01 MLLW offset 1.651 equals VDatum's value at GS02.
- Archive styling (Derek 2026-10-09 06:08Z): style_archive.py styles every archived f36/3d/30d run's html into archive/<product>/<YYYY>/<MM>/<run>.zip (shared jslib/, ~1.7 MB per run) and latest/<product>/ (inline pages, overwritten). Runs as Step 3 of this thread's 6-hourly routine. Only f36 and 00Z 3d runs carry html; first 30d html due 2026-10-15 00Z (untested page layout). 3d/30d CSV pairs start at different times, so the offset is the median over common times.

- Retired stations (Derek 2026-10-15, file-count limit): NN01-NN10, VB01-VB10, USNW, USPF, USSV dropped. Their charts, latest/ pages and stations_usgs/_datums/USNW.json, USPF.json were deleted, so style_archive.py now styles 35 stations and skips them; older archive zips still hold their pages.
