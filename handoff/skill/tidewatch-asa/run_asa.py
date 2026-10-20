"""Run the Tidewatch Automated Statistical Analysis.

    python run_asa.py                      # latest forecast cycle with observations
    python run_asa.py --target 2026-09-16T01:00Z
    python run_asa.py --archive /path/to/archive/current --output /path/to/output

Meant to be called by cron (or scheduler.py) after each 6-hourly archive run:
it scores every archived forecast that observations now cover, then writes the
plots, tables and PowerPoint for the most recent fully validated cycle, then
draws any new Tidewatch vs NWS validation animations (see run_validation.py).
"""
import argparse
import logging

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asa import config, pipeline

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", nargs="+",
                    help=f"one or more archive roots (default {config.ARCHIVE_ROOT})")
    ap.add_argument("--output", help=f"output root (default {config.OUTPUT_ROOT})")
    ap.add_argument("--target", help="forecast issuance to report, e.g. 2026-09-16T01:00Z")
    ap.add_argument("--datum", choices=["MLLW", "NAVD"], help="default MLLW")
    ap.add_argument("--stations", nargs="*", help="limit to these Tidewatch IDs")
    ap.add_argument("--no-deck", action="store_true", help="skip the PowerPoint")
    ap.add_argument("--no-validation", action="store_true",
                    help="skip the Tidewatch vs NWS validation animations (run_validation.py)")
    ap.add_argument("--fetch-missing", action="store_true",
                    help="fill observation gaps from NOAA/USGS web services")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.fetch_missing:
        config.FETCH_MISSING_OBSERVATIONS = True
    pipeline.run(a.archive, a.output, a.target, a.datum, a.stations, make_deck=not a.no_deck)
    if not a.no_validation:
        # Validation animations for 36-h windows and 72-h NWS windows that
        # closed since the last run; already-drawn ones are skipped.
        try:
            from asa import validation
            res = validation.run(a.archive, a.output)
            logging.info("validation: %d forecasts, %d new animations", res["records"], len(res["rendered"]))
        except Exception:
            logging.exception("validation animations failed; ASA products above are unaffected")
