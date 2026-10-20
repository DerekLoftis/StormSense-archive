"""Check Tidewatch's observed water levels for aliasing (see asa/aliasing.py).

    python run_aliasing.py   # writes output/aliasing/aliasing_by_station.csv and duplicate_series.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from asa import aliasing, config  # noqa: E402

if __name__ == "__main__":
    roots = sys.argv[1:] or [config.ARCHIVE_ROOT]
    table, dup, (start, end) = aliasing.run(roots, config.OUTPUT_ROOT / "aliasing")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(f"window {start} to {end}")
    print(table.drop(columns=["flags"]).round(3).to_string())
    print(table[["source", "operational", "flags"]].to_string())
    print(dup.to_string())
