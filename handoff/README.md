# Handoff from the Tidewatch project (2026-10-20)

Material copied out of the "Tidewatch Charts into StormSense" project's shared folder so the
StormSense project has it on GitHub. This repo is private; it also holds the StormSense run archive. Start with `../CLAUDE.md`. These are snapshots: paths
inside them that start with `/mnt/project-files/` refer to the old project's folder.

| Path | What | Use here |
|---|---|---|
| `PROJECT_INSTRUCTIONS.md` | text for the new project's instructions | paste into project settings |
| `FIRST_MESSAGE.md` | Derek's opening message for the new project | paste as the first message |
| `ensemble_design.md` | ensemble and adaptive guidance design, phases 1-4 | phase 2-4 work |
| `surge_guidance/` | survey of surge guidance sources and per-station model coverage | adding members |
| `skill/tidewatch-asa/` | the statistics code (Derek's skill formula, metrics, validation animations, event decks) without its inputs or big outputs | skill scoring of StormSense and ensemble members |
| `skill/tidewatch-asa/output/forecast_stats_history.csv` | every scored Tidewatch 36-h forecast since 16 Sept 2026 | baseline to beat |
| `skill/tidewatch-asa/output/tw_vs_nws_stats.csv`, `nwps_datum_check.csv` | Tidewatch vs NWS per cycle; NWPS obs offsets | baseline, datum checks |
| `skill/tidewatch-asa/output/aliasing/` | which Tidewatch obs series are copies of other gauges | gauge QC |
| `stations/TidewatchStations2026v5_GitHub_links.xlsx` | Derek's station sheet with GitHub links | station metadata |
| `disclosures/` | StormSense and Tidewatch disclaimer/disclosure PDFs and their html sources | product docs |
| `notes/` | the old project's memory notes (index, archive, ASA, datums, events, retired stations) | background |

The skill code reads Tidewatch archive folders (`f36/`, `3d/`, `30d/`); to score StormSense,
point its metrics (`asa/metrics.py`) at archived StormSense or ensemble CSVs instead.
`asa/runfiles.py` reads files loose or inside a run's `run.tar`.
