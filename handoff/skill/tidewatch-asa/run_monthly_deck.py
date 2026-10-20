"""Monthly validation deck: station tables, one animation per 36-h forecast, and a composite.

    python run_monthly_deck.py --month 2026-09 --archive ../tidewatch-archive/data/tidewatch input/derek_sept2026_noreaster
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asa import config, monthly_deck  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", default="2026-09")
    ap.add_argument("--archive", nargs="+", default=[str(config.ARCHIVE_ROOT)])
    ap.add_argument("--output", default=str(config.OUTPUT_ROOT))
    ap.add_argument("--stations", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    deck, summary = monthly_deck.run(a.archive, a.output, a.month, a.stations, a.workers, a.force)
    print(f"{deck}: {summary['forecasts']} forecasts, {summary['stations']} stations")
