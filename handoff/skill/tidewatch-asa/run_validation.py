"""Tidewatch vs NWS NWPS validation animations, tables and workbooks.

    python run_validation.py                 # render whatever has newly elapsed
    python run_validation.py --force         # redraw every animation

Run after each 6-hourly capture (after run_asa.py). Animations already on disk
are skipped, so each run only draws the 36-hour windows and 72-hour NWS
windows that closed since the last one. Outputs go to
output/validation_animations/<STATION>/{36hr,72hr}/.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asa import config, validation  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", nargs="+", help=f"one or more archive roots (default {config.ARCHIVE_ROOT})")
    ap.add_argument("--output", help=f"output root (default {config.OUTPUT_ROOT})")
    ap.add_argument("--stations", nargs="*", help="Tidewatch IDs (default: every NOAA station)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="redraw animations that already exist")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = validation.run(a.archive, a.output, stations=a.stations, workers=a.workers, force=a.force)
    print(f"{res['records']} validated forecasts; {len(res['rendered'])} animations drawn; "
          f"NWS tables for: {', '.join(res['tables']) or 'none yet'}")
