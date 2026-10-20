StormSense is VIMS's storm tide forecast and sensor service for Virginia and the Chesapeake Bay, run by Derek Loftis. This project archives StormSense forecasts (separately from Tidewatch) and develops StormSense's own 72-hour forecast.

- Read CLAUDE.md in DerekLoftis/StormSense-ensemble (same file in StormSense-latest) before any work; StormSense-archive (private) holds the handoff material in handoff/ (see handoff/README.md). Keep CLAUDE.md current when facts change.
- The live sites run on GitHub Actions: StormSense-latest (publish.yml, :20 past 00/06/12/18 UTC) and StormSense-ensemble (ensemble.yml, :40). Don't break them: test changes on a branch and keep the schedules and file names stable, since outside pages link to them.
- Archives go on GitHub releases written from inside workflows (one tarball per run, monthly tags), never into the project's files. History from before this project is on DerekLoftis/tidewatch_archive releases (read only here).
- Tidewatch itself (web4, tidewatch_archive, tidewatch_latest, the ASA) belongs to the Tidewatch project. Don't edit those repos from here; ask Derek to pass changes along.
- Score forecasts with Derek's skill formula: S = 0.25[R² + e^(-|Bias|/σ) + e^(-MAE/σ) + e^(-RMSE/σ)], σ = population SD of obs in the window. Event look-backs use his deck format (handoff/notes/flood-event-validation-format.md).
- Chart style: StormSense logo, cyan #00E5EE residual, the disclaimer "StormSense(TM) Predictive Plots are computer-generated operational storm tide forecasts; the user assumes any and all risk.", the guidance name below it, NWS flood categories on NAVD88 charts.
- Retired stations NN01-NN10, VB01-VB10, USNW, USPF, USSV stay out.
- Only Derek can create repos or buckets, add secrets, change repo settings or approve dispatching a workflow; ask him in one line when that is the blocker.
