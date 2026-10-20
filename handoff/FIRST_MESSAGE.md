This project takes over StormSense from my Tidewatch project. Read CLAUDE.md in StormSense-ensemble first, then:

1. Start archiving every StormSense Latest run (fore72, 7day and 30day CSV, JSON and html) separately from Tidewatch, one tarball per 6-hourly cycle on monthly releases in StormSense-archive, with an index of what each run holds.
2. Score P-ETSS and each ensemble member against observations as the archive grows, with my skill formula, and turn on the adaptive guidance pick (phase 2) once there is enough history.
3. Plan and build StormSense's own 72-hour forecast (its own tide, observations and surge, trained on the archives) as a new ensemble member, so it can become the primary forecast once it scores best.

Start a thread for each.
