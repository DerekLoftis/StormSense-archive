# Aliasing in Tidewatch's observed water levels (7 Oct 2026)

Checked: the 3d and 30d hindcast CSVs Tidewatch published for the 34 stations
with live forecasts, 7 Sep to 7 Oct 2026, from the shared archive. Script:
`run_aliasing.py` (`asa/aliasing.py`). Per-station numbers:
`aliasing_by_station.csv`; matching pairs: `duplicate_series.csv`.

## 1. Fifteen stations publish another station's observations

In 6 groups, Tidewatch publishes the **same observation series** under several
station IDs. The NAVD88 files are identical value for value, in every 3d
snapshot from 16 Sep to 7 Oct. The MLLW files differ only by a constant, which
is each station's own MLLW-NAVD offset applied to the shared series.

| Group | Stations | Which is real (checked against the NWS NWPS gauge) |
|---|---|---|
| 1 | DAHL, USNC, USSC | Not Dahlgren. Against NWPS NCDV2 (Upper Machodoc Creek), correlation is -0.41 and the differences have an SD of 1.22 ft. The NWPS gauge for New Market Creek (NMCV2) isn't in the archive, so I couldn't confirm the series is USNC's. |
| 2 | WADC, USAR, USBK | Not Washington. Against NWPS WASD2, the differences have an SD of 0.94 ft and the best match lags by about 2 hours. The Brick Kiln Creek gauge (BKNV2) isn't in the archive, so I couldn't confirm the series is USBK's. |
| 3 | USFM, USCB | The series is Fort Monroe. It matches NWPS FTMV2 exactly. USCB is the copy. |
| 4 | USSF, USNR | The series is Nansemond River at Suffolk. It matches NWPS NMDV2 exactly. USNR is the copy, not the Sleepy Hole gauge (NRSV2 runs 48 min later). |
| 5 | USHR, USSB | Not Nassawadox Creek. It leads NWPS NSWV2 by 84 min, with differences at an SD of 0.57 ft. It is probably Hampton River (USHR). |
| 6 | GS01, GS02 | Both are StormSense gauges. There is no independent feed to check them against. |

The **forecasts are affected too**. Within each group, the astronomical tide
(pa30) in the NAVD f36 files is identical or nearly so:
- an SD of difference of 0.000 ft in groups 3 to 6;
- 0.03 to 0.05 ft in groups 1 and 2.

Only the surge anomaly differs. So Tidewatch's tide prediction for DAHL, WADC,
USSB, USCB, USNR and one station in each of GS01/GS02, USNC/USSC and
USAR/USBK was fitted to another gauge's record.

This also explains the three stations where the 6 Oct statistics disagreed
with Lexie's NOAA-API results (DAHL, WADC, USSB). Scored against Tidewatch's
own (copied) observations, those forecasts look fine. Scored against the real
gauge, they don't. The station IDs in `stations.py` were right; it is
Tidewatch's own data that's crossed.

## 2. Coarse or held samples

| Station | What the 6-min series shows |
|---|---|
| USNW, USBB, USPF | 62-66% of 6-min steps repeat the previous value, in about 18-min steps. Correlation with the astronomical tide is under 0.25, because these sites are nearly non-tidal and the signal is mostly steps of one reporting increment. |
| USLB, USPA | About 40% of steps lie on straight lines between 12-min points. That is linear fill between sparser reports. It's mild, and doesn't distort the tide. |

## 3. Spiky high-frequency scatter

The scatter is measured as the RMS of the observations about a 1-hour running
median.

| Station | RMS about running median | Successive changes flip sign? |
|---|---|---|
| USAR, USBK, WADC (group 2) | 0.15 ft | Yes (lag-1 correlation -0.13) |
| USHR, USSB (group 5) | 0.09 ft | Yes |
| KIPT | 0.08 ft | Yes |
| NOAA stations, typical | 0.01-0.03 ft | No |

- **Groups 2 and 5:** this is the signature of unaveraged readings sampled every 6 min, with wave energy folded into the series.
- **KIPT:** the NWPS feed has the same scatter (difference SD 0.003 ft), so it is real wave action at an exposed NOAA gauge, not a Tidewatch artefact.

## 4. NOAA stations otherwise clean

For BISH, KIPT, LEWI, SWPT, WACH, WMPT and YRCG, Tidewatch's observations
match the NWPS observed stage to an SD of 0.003 ft, with no time lag.

The constant offsets are what Derek found in Oct 2025:

| Stations | NWPS minus Tidewatch |
|---|---|
| WMPT, YRCG | +0.044 to +0.045 ft |
| BISH, WACH | -0.035 to -0.036 ft |
| KIPT, LEWI, SWPT | 0.00 to 0.01 ft |

The validation animations correct the NWS forecasts by these offsets
(`validation_animations/nwps_datum_check.csv`).

## What the validation does about it

- **DAHL and WADC** are validated against the NWPS observations for the real gauge wherever those cover the window. The NWPS observations start on 6 Oct.
- **The September DAHL and WADC animations** fall before that. They still use Tidewatch's copied series and carry a caution line saying so.

## Update: sources confirmed against NWS observations (IEM HML archive, Sept 2026)

NWS HML observations for 1 Sep to 7 Oct 2026 (6-min, about 8,500 values per gauge) settle most groups. An exact match means the difference SD is 0.000 ft.

| Group | Finding |
|---|---|
| 1 | The shared series **is New Market Creek**: USNC matches NWS NMCV2 exactly. DAHL and USSC are copies of it. Tidewatch's DAHL forecast runs about 180° out of phase with the real Dahlgren gauge (NCDV2): on the 25 Sep 17Z run, RMSE is 1.94 ft against Dahlgren. |
| 2 | Close to Brick Kiln Creek, but not exact: correlation with NWS BKNV2 is 0.967 and the differences have an SD of 0.28 ft. Tidewatch's version is the spiky one (0.15 ft RMS high-frequency scatter), which fits unaveraged readings aliasing wave energy. WADC is not Washington. |
| 3 | Fort Monroe, confirmed (FTMV2). USCB is a copy. |
| 4 | Nansemond River at Suffolk, confirmed (NMDV2). USNR is a copy. |
| 5 | Hampton River (HMNV2): correlation 0.984, differences at an SD of 0.21 ft. Again the Tidewatch series carries extra high-frequency scatter. USSB is a copy, not Nassawadox Creek. |

These gauges match NWS exactly apart from a constant datum offset:

| Station | Offset (ft) |
|---|---|
| USNC | +0.015 |
| USLB | −1.645 |
| USLR | +0.535 |
| USLN | −0.125 |
| USTC | −1.645 |
| USNW | −1.645 |
| USBC | +0.205 |
| USPA | −1.645 |
| USJT | −0.003 |
| USPT | +0.135 |

The −1.645 ft offsets look like NWS publishing those gauges on a different datum.
