---
name: kingtide-oct2026
description: Oct 2026 king tide weekend sitrep deck (Tidewatch vs NWS vs STOFS/NWM/CBOFS), scripts, findings and model-datum gotchas
metadata:
  type: project
  modified: 2026-10-12T13:03:47.091Z
---

Derek asked 2026-10-12 12:28Z (statistics thread) for an after-event sitrep pptx of every archived Tidewatch forecast issued 8 Oct onward, validated with obs up to now (he was flood mapping in Norfolk that morning), with archived NWS and a synopsis of all other archived guidance at the end. Format follows [[flood-event-validation-format]].

- Scripts in tidewatch-asa: run_event_deck_kingtide_oct2026.py (render | deck; partial-window scoring for forecasts whose window has not ended; TW_ROOT env var points at a slim symlinked root of Oct MLLW files because rglob over the full archive takes ~10 min and does not follow dir symlinks), run_kingtide_models.py, run_kingtide_outlook.py, asa/event_models.py (SHEF/CBOFS parsers, scoring, high-tide errors). Output: output/event_decks/2026-10_kingtide/.
- Render speed on 4 cores: ~13 animations/min.
- Model datums: STOFS SHEF MLLW ft; NWM SHEF NAVD88 ft (use NOAA noaa_datums json NAVD88-MLLW at NOAA gauges); CBOFS station zeta m MSL (NOAA MSL-MLLW, nearest node <=3 km). STOFS-2D points at USGS LIDs (FTMV2, JSFV2, NMDV2) sit 0.7-1.3 ft low: excluded. NWM coastal ran 0.6-0.7 ft low (high tides ~0.9 ft low, damped range) at NOAA gauges too, so not a conversion error.
- Findings 8-12 Oct (36-h, same stations, DAHL/WADC excluded): NWS RMSE 0.20 vs TW 0.23; STOFS-2D 0.19 vs 0.20; CBOFS 0.24; STOFS-3D 0.28 (bias -0.22); NWM 0.74. Peak of event 11 Oct ~14Z (SWPT 4.06 ft MLLW, action only).

- Update 2026-10-15 (Derek: "Catch the King" sitrep with completed windows): output/event_decks/2026-10_kingtide_v2/Tidewatch_CatchTheKing_Oct8-12_2026_SitRep_v2.pptx (773 slides, 680 forecasts from 20 runs 8 Oct 00Z-12 Oct 18Z, none partial; old deck kept). Scripts now default to the v2 folder with T_HI = 12 Oct 18Z; the outlook is "as sent Monday morning" plus what was observed. Paired 36-h RMSE: NWS 0.21 vs TW 0.26 (11 stations), STOFS-2D 0.18 vs 0.22, CBOFS 0.25, STOFS-3D 0.31 (bias -0.25), NWM 0.73. SWPT event peak was Mon 12 Oct 11:12 EDT 4.12 ft (action), after the first sitrep; minor only at BISH, LEWI, DAHL.
- Animation changes Derek asked for (now default in asa/animate.py and monthly_deck.render_composite, so every deck gets them): per-forecast plots start at midnight so the obs line reaches the left edge; composites give each forecast its own colored wipe bar (next starts 2/3 through the prior), which recedes to the label leaving a faded dashed callout; labels are black text with a 1-px white then 2-px sequence-color halo.
