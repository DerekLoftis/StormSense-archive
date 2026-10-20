---
name: retired-stations
description: Stations Derek retired 2026-10-15 (NN01-10, VB01-10, USNW, USPF, USSV): no longer archived, files deleted, ASA skips them
metadata:
  type: project
  modified: 2026-10-15T23:39:24.948Z
---
Derek (2026-10-15) retired NN01-NN10 and VB01-VB10 (stale on web4 for over a year, slated for removal) and USNW, USPF, USSV (non-tidal or lacking data) to save project files. The archive thread stopped archiving them. The statistics thread deleted their 1,590 files from tidewatch-asa (input captures, output/cycles, event and monthly deck animations/tables, output/monthly), keeping the finished pptx decks, which embed their own media. asa/config.py RETIRED makes data.load_forecasts/load_observations skip them, so the ASA does not regenerate them. Related: [[tidewatch-asa]], [[tidewatch-archive]].

Related 2026-10-20: the archive thread packs old runs into <run>/run.tar (pack_runs.py; newest 2 days loose). The ASA reads loose or packed files through asa/runfiles.py (a copy of archiver/runfiles.py) in data.py, nws.py, event_models.py and pipeline.py; loose and packed sample runs gave identical results.
