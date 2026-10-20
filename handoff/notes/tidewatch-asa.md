---
name: tidewatch-asa
description: Where the automated statistical analysis (goals 2-3) and validation animations live, how they get observations, skill formula, aliasing findings
metadata:
  type: project
---
ASA code + outputs: /mnt/project-files/tidewatch-asa/ (Python; run_asa.py, run_sensitivity.py, run_validation.py, run_aliasing.py, README.md, FINDINGS.md). Built 2026-10-06.
Reads the shared archive at /mnt/project-files/tidewatch-archive/data/tidewatch (+ ../nwps for NWS). See [[tidewatch-archive]].

- Skill = Derek's formula (adopted 2026-10-07): S = 0.25[R2 + e^(-|Bias|/sd) + e^(-MAE/sd) + e^(-RMSE/sd)], sd = population SD of obs in the window; 0-1, 3 decimals, no-skill ~0.45. Site Skill = same exp components with 30-day SD + CRITIC weights.
- Observations come from Tidewatch's own 3d/30d CSVs. Run issuance = latest station start in a capture, ceil to hour.
- Validation animations (Derek 2026-10-07): output/validation_animations/<ID>/{36hr,72hr}/*.mp4, his Oct 2025 format, NOAA stations only; 36hr drawn when window passes (~5 s), 72hr with NWS to 72 h (~6 s); table PNG + monthly xlsx in his layout once 72-h NWS windows exist. run_asa.py calls validation.run after the ASA (so the capture routine needs no change). 117 retroactive Sept animations rendered.
- NWPS datum shift = median(NWPS obs - Tidewatch obs); applied when |x|>=0.01 and SD<=0.05: WMPT +0.044, YRCG +0.045, BISH -0.036, WACH -0.035 (matches Derek's Oct 2025 values).
- Aliasing (output/aliasing/ALIASING.md): Tidewatch publishes identical NAVD obs (and near-identical pa30) for groups {DAHL,USNC,USSC}, {WADC,USAR,USBK}, {USFM,USCB}, {USSF,USNR}, {USHR,USSB}, {GS01,GS02}. DAHL/WADC/USSB are not their named sites (explains Lexie API mismatch). USNW/USPF/USBB held ~18-min values. DAHL, WADC validated against NWPS obs where available.
- 23 stations (CBBT, MNPT, USLI, NN01-10, VB01-10) have stale/empty f36 on Tidewatch.
- Derek's Sept 24-29 nor'easter captures are in tidewatch-asa/input/derek_sept2026_noreaster; run_asa.py takes both roots via --archive.
- Container network blocks web4, NOAA and USGS APIs; slow shared-folder I/O (~1.5 min to load archive).
- Monthly deck (Derek 2026-10-07): run_monthly_deck.py -> output/monthly_decks/<YYYY-MM>/ (pptx with autoplay embedded mp4s per forecast, ROYGBIV sequence colors, station table PNG first, composite last). Sept 2026: 34 stations, 441 forecasts, 512 slides, 154 MB.
- Past NWS forecasts: IEM archive of issued HML products (mesonet.agron.iastate.edu/cgi-bin/request/hml.py?station=<LID>&kind=forecasts|obs&tz=UTC&sts=..&ets=..&fmt=csv). Our container can't reach it; the archive repo's on-demand GitHub workflow downloads (coordinator routes). Sept 2026 files: tidewatch-asa/input/nws_hml_sept2026/. nws.HmlArchive reads them.
- Sept 2026 result: NWS beat Tidewatch over the same 36 h at 10 of 11 valid gauges (RMSE 0.24 vs 0.28 ft); NWS 72-h RMSE 0.29 ~ Tidewatch 36-h. USNC is the true source of DAHL/USSC series (exact match to NMCV2).

- Oct 2025 king tide/nor'easter (2026-10-08): IEM HML has every NWS issuance through the shutdown (input/nws_hml_oct2025/, 1-21 Oct). run_oct2025_hml.py -> output/oct2025_hml/ (Derek's 4 workbooks + H## sheets, Summary cols U-AR, "HML issuances"). Digitised hydrographs: heights right (~0.03 ft) but 42 of 56 traces one hour early; Derek's SWPT/LEWI/WMPT workbooks are on EDT labelled UTC (YRCG is true UTC). With real HML, NWS 36-h RMSE beats Tidewatch at LEWI/WMPT, ties YRCG, loses SWPT.
- Oct 2025 paper figures (Derek's journal article, 2026-10-08): run_paper_figures_oct2025.py → output/oct2025_hml/paper_figures/ (Fig_<ST>_..._cycles_1-7/8-14.png in Derek's layout, table PNG + editable xlsx per station, all-stations xlsx, paper_figure_stats.csv; all EDT). Combined revised manuscript text: output/oct2025_hml/manuscript/Oct2025_Tidewatch_vs_NWS_manuscript_text.md. Revised conclusion: RMSE ratio NWS/TW SWPT 1.13, YRCG 1.02, WMPT 0.82, LEWI 0.89; NWS higher R² everywhere, positive bias at lower bay; TW best on rising surge, NWS best in recession.
- Dispatching the archive repo's workflows from this thread is blocked by the permission classifier; ask Derek for explicit OK and don't route the run through another thread.

- Hurricane Matthew 2016 work: see [[matthew-2016]].