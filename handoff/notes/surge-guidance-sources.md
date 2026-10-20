---
name: surge-guidance-sources
description: Which surge models beyond ETSS cover Tidewatch stations and where their point files live (survey of 2026-10-06)
metadata:
  type: reference
  modified: 2026-10-06T20:38:58.968Z
---

Survey of goal 5 (ensemble surge guidance) done 2026-10-06; full write-up at /mnt/project-files/surge_guidance/surge_guidance_survey.md, per-station table at station_coverage.csv (Tidewatch ID -> NWS LID -> which models have point output).

- NWM v3 coastal total water SHEF (s3://noaa-nwm-pds, NAVD88 ft) covers 34/42 stations, the best coverage.
- STOFS-2D-Global SHEF (s3://noaa-gestofs-pds, MLLW ft, 4/day, 180 h) covers 19; it also has surge-only (swl) and tide-only (htp) files.
- STOFS-3D-Atl SHEF (s3://noaa-nos-stofs3d-pds, 12Z only, 96 h) covers 11 NOAA stations.
- CBOFS stations netCDF (s3://noaa-nos-ofs-pds) covers 10 NOAA stations.
- NWS official forecasts: api.water.noaa.gov/nwps/v1/gauges/{lid}/stageflow/forecast. P-ETSS and ETSS: NOMADS.
- Stevens SFAS does not cover Virginia.
- The cloud container reaches AWS S3 but NOT NOMADS, api.water.noaa.gov, tidesandcurrents or web4.vims.edu (proxy 403). WebFetch can read api.water.noaa.gov.

Related: [[tidewatch-archive]] [[predictor-corrector]]
