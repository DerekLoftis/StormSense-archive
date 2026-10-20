# StormSense Ensemble and Adaptive Guidance: design (2026-10-16)

Derek's idea: a second StormSense product that plots every forecast source together as an ensemble, plus
an ensemble archive. The primary StormSense forecast then switches automatically to whichever guidance is
judged best, and names that guidance on the chart below the disclaimer.

## Ensemble members (all reachable from GitHub Actions)

| Member | What drives it | Includes rain/rivers | Output | Notes |
|---|---|---|---|---|
| P-ETSS (mean, 10%, 90%) | GEFS ensemble winds/pressure, SLOSH | no | hourly, 102 h, 4/day | current StormSense source; the cone |
| ETSS / Tidewatch | GFS winds/pressure, harmonic tide | no | 36 h | baseline we know |
| STOFS-2D-Global | GFS, ADCIRC hydrodynamic | no | 180 h, 4/day | best in the Oct 2026 king tide (0.20 ft at 72 h) |
| STOFS-3D-Atlantic | GFS/HRRR, river inflow, 3-D | yes (rivers) | 96 h, 12Z only | ran ~0.25-0.30 ft low |
| CBOFS | NAM/HRRR, ROMS | yes (rivers) | 48 h, 4/day | 10 NOAA stations |
| NWM coastal (short, medium) | HRRR/GFS, rainfall-runoff + SCHISM | yes | 18 h / 10 d | ran 0.6-0.7 ft low; needs a bias fix |
| NWS official (NWPS) | forecaster blend | partly | ~3/day, ~72-96 h | most proven in storms; 19 gauges |
| P-Surge (NHC) | hurricane track/intensity ensemble | no | only during tropical advisories | the operational hurricane surge guidance |
| Tide + persistence | astro tide + last observed residual, decaying | n/a | any length | the "no-skill" floor every model must beat |

Every member gets the same gauge alignment StormSense uses now (median gap to the last 24 h of
observations), so datum offsets such as STOFS-2D's at the USGS gauges and NWM's low bias stop
counting against them.

## The ensemble product (phase 1)

- An `ensemble/` folder on StormSense Latest: one chart per station and datum with every member as
  its own line, the P-ETSS 10-90% cone, observations and NWS flood thresholds; CSV and JSON with one
  column per member.
- An ensemble archive, written by the Actions run each cycle as one tarball on a GitHub release. It
  never touches the project's file count.

## How the primary forecast picks its guidance

The pick is made **per station and per lead-time band** (0-12, 12-36 and 36-72 h). The best model for the
next 6 hours is often not the best at 60 hours.

1. **Recent skill (Derek's 1).** Score each member's earlier runs against the observations that have
   come in since, with an exponentially weighted error over the last few days (newest weighted most).
   Use peak and threshold scores as well as RMSE. Switch only when another member is clearly better
   for two updates running (hysteresis), so the chart doesn't flip back and forth.
2. **Analog conditions (Derek's 2).** Describe each cycle by wind speed and direction, pressure and its
   trend, 24-48 h rainfall, river flow and residual at the site, then find the past cycles that looked
   most like it and check which member did best afterwards. Our own archive is too short for this, but
   it can be backfilled: STOFS and NWM forecasts sit on NOAA's AWS buckets for years, IEM holds NWS
   forecasts back to 2016, and ERA5 or HRRR give the weather.
3. **Regime rules (Derek's 3).** These are adjustments to the weights:
   - heavy QPF (WPC/NBM) or high river flow favors NWM coastal, STOFS-3D, CBOFS and NWS;
   - an active NHC advisory near the bay favors P-Surge and STOFS-2D;
   - nor'easter conditions (sustained N-NE wind, deepening low) favor the hydrodynamic models;
   - calm weather favors tide plus persistence and bias-corrected P-ETSS.
4. **Factors to add (Derek's 4):**
   - **Blend rather than pick.** A skill-weighted mean of the top members almost always beats the single
     best member. The chart can still name the leading member and the blend weights.
   - **Score what matters for warnings:** high-tide height and timing errors, and hits, misses and
     false alarms at the NWS thresholds. Missing a flood should cost more than a false alarm.
   - **Data freshness and fallback.** Members post at different times; a newer run of a weaker model
     can beat a stale run of a stronger one. A missing member drops out instead of breaking the run.
   - **Gauge quality control.** Bad or aliased observations (DAHL, WADC) and outages must not drive the
     pick; neighbouring stations can share skill when a gauge is down.
   - **Seasonal and sea-level offsets** (monthly mean sea level runs high in fall), wave setup at the
     ocean-facing gauges, and river discharge at the upper tidal rivers (WMPT, LEWI).
   - **Transparency.** Below the disclaimer, the chart says which guidance is in use and why, for example
     "Guidance: STOFS-2D (best of 8 members over the last 3 days, RMSE 0.14 ft)". The JSON carries
     the full scoring.

## Phases

1. Ensemble chart, CSV/JSON and archive (all members aligned). Start now.
2. Recent-skill selection with hysteresis, per station and lead band, labelled on the primary chart.
   Useful after about a week of archive.
3. Regime rules from QPF, NHC advisories and wind forecasts.
4. Analog and skill learning, after the backfill.
