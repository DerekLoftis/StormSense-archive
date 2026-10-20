# Surge guidance sources beyond ETSS for the Tidewatch fork

Checked 2026-10-06 against the live NOAA open-data buckets on AWS and the National Water Prediction Service API. Station-by-station coverage is in [station_coverage.csv](station_coverage.csv) (all 42 stations in Lexie's `stations.py`).

## Short answer

Yes, the fork can serve a multi-model ensemble. Four independent operational models already publish ready-made point forecasts at most Tidewatch stations, free and without credentials, alongside the NWS forecaster-issued hydrographs and P-ETSS. ETSS would become one member among five or six. The archive has to come first, because sensible per-station weights need a few weeks of each member's skill scores.

## Sources that cover the Tidewatch stations

| Source | Model / who runs it | Cycles, horizon | Point output for Tidewatch | Datum in point files | Access (no login) |
|---|---|---|---|---|---|
| **ETSS** (current input) | SLOSH, NWS MDL | 4/day, ~4 days | NOAA stations | MLLW (stations) | NOMADS `com/etss/prod` |
| **P-ETSS** | SLOSH ensemble, NWS MDL: 42 members from NAEFS (GEFS + Canadian) | 4/day, ~4 days | NOAA stations; exceedance (10–90 %) water levels | MLLW | NOMADS `com/petss/prod` |
| **STOFS-2D-Global** | ADCIRC, NOS | 4/day (00/06/12/18Z), 180 h | 19 of 42 (all NOAA except Wachapreague, plus 7 USGS) | SHEF in MLLW ft; netCDF relative to geoid/MSL | `s3://noaa-gestofs-pds/stofs_2d_glo.YYYYMMDD/…points.cwl.shef` (also `.swl` surge-only and `.htp` tide-only) |
| **STOFS-3D-Atlantic** | SCHISM (VIMS-developed), NOS; includes river inflow from the National Water Model | 1/day (12Z), 96 h + 24 h nowcast | 11 NOAA stations in SHEF; Wachapreague also in the netCDF | SHEF in MLLW ft; netCDF relative to MSL | `s3://noaa-nos-stofs3d-pds/STOFS-3D-Atl/stofs_3d_atl.YYYYMMDD/…points.cwl.shef` |
| **NWM v3 coastal (Atlantic/Gulf)** | SCHISM total water level, NWS Office of Water Prediction; compound flood (surge + rainfall runoff) | Short range hourly to 18 h; medium range 4/day to 120 h | **34 of 42** (all 12 NOAA, 22 USGS) | NAVD88 ft | `s3://noaa-nwm-pds/nwm.YYYYMMDD/{short_range_coastal_atlgulf, medium_range_coastal_atlgulf_mem1}/…total_water.atlgulf.shef` |
| **CBOFS** | ROMS, NOS Chesapeake Bay OFS | 4/day, 48 h | 10 NOAA stations within 3 km (not Money Point or Wachapreague); USGS sites not checked | MSL (needs VDatum shift) | `s3://noaa-nos-ofs-pds/cbofs/netcdf/YYYY/MM/DD/cbofs.tHHz.YYYYMMDD.stations.forecast.nc` |
| **NWS official forecast (NWPS)** | Wakefield (AKQ) / Sterling (LWX) forecasters, blending the guidance above | ~2/day, ~4 days | Confirmed live today at SWPV2, WAHV2, LHNV2, RAPV2, NCDV2; WASD2 only during events; none at THAV2 | Station datum per gauge (most AKQ gauges are labelled "IN MLLW") | `https://api.water.noaa.gov/nwps/v1/gauges/{lid}/stageflow/forecast` (JSON) |

NWPS here is the National Water Prediction Service at water.noaa.gov, the successor to AHPS. That is where the NWS coastal forecast hydrographs live. It is a forecaster product built from the models above, so it is a useful benchmark but not an independent ensemble member.

**One snapshot of the spread, Sewells Point, 2026-10-06 12Z cycle.** Tonight's high tide (~23Z) is 3.69 ft MLLW in STOFS-2D and 2.97 ft in STOFS-3D. NWM coastal gives 1.31 ft NAVD88, which is about 2.9 ft MLLW using the 1.6 ft offset from the week 5 meeting. That 0.7 ft gap between members is the kind of signal an ensemble and a skill-weighted blend are meant to use.

## Sources that don't fit

- **Stevens Flood Advisory System (SFAS):** covers NY/NJ and the Mid-Atlantic Bight, not Chesapeake Bay or Virginia.
- **P-Surge and the NHC surge products:** run only when tropical advisories are active. They are worth archiving during hurricanes but can't be a standing member.
- **CERA / ADCIRC Surge Guidance System (UNC, LSU):** mostly tropical-event runs with web map output. They have no stable public point feed to automate against. Event-only member at best.
- **NWPS wave model (Nearshore Wave Prediction System):** takes its water levels from ETSS/P-ETSS/STOFS rather than computing surge itself.

## Stations no model covers at a point

8 USGS sites have no model point output: USBC (Bailey Creek), LENM (McIntosh Run), USWN (West Neck Creek), USSB (Silver Beach), USMB (Bypass Canal), USBM (Bells Mill Creek), USNW (Northwest River) and USPA (EB Elizabeth at Rt 165). The fork could pull these from the nearest wet node of the gridded output. The NWM coastal `total_water` netCDF is about 31 MB per hour and the easier route. The STOFS-3D field files run to 1.6 GB per 12 hours, so they would need byte-range subsetting. Nearest-node values in small creeks should be validated against observations before they count as members.

## How an ensemble could fit Tidewatch's structure

Tidewatch is harmonic tide + ETSS surge + an anomaly correction (the m30 signal). Two ways to add members:

1. **Surge-swap:** replace ETSS surge with each model's surge-only signal and keep Tidewatch's own tide and anomaly correction. STOFS-2D publishes surge-only (`swl`) and tide-only (`htp`) point files, so this works directly for it. For other models, subtract their tide.
2. **Total-water members:** take each model's total water level, convert it to MLLW/NAVD with VDatum offsets per station, then apply the same station anomaly or predictor-corrector offset (goal 4) to every member.

Option 2 needs fewer assumptions about each model's tide, and it lets the skill score from goal 3 rank and weight members per station (for example, inverse-RMSE or Bayesian model averaging over a rolling 30-day window). The output would be an ensemble mean or weighted blend plus a spread band, alongside Tidewatch's existing f36 curve.

## What this means for the archive

Every source above except ETSS/P-ETSS (NOMADS) and NWPS (api.water.noaa.gov) sits on public AWS S3 buckets. The point files are small, from 0.3 MB SHEF to 50 MB netCDF per cycle. Archiving the SHEF files every 6 hours for STOFS-2D, STOFS-3D (12Z), NWM medium-range coastal and CBOFS would cost well under 1 GB a month. Note that this cloud environment can reach the AWS buckets but not NOMADS, api.water.noaa.gov, tidesandcurrents or web4.vims.edu, so a job running outside VIMS needs a host with normal internet access.

## Caveats

- The USGS-to-NWS ID mapping comes from gauge names in the NWPS API. NMDV2 ("Nansemond River at Suffolk") carries a different name in the STOFS-2D station list, so check its coordinates before use.
- The NWS official forecast column is only filled where I checked; the other rows are marked "not checked".
- P-ETSS and ETSS details come from MDL documentation, because NOMADS wasn't reachable from here to list files.
