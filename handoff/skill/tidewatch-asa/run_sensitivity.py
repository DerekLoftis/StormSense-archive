"""Skill-score sensitivity analysis on the scored forecast history.

    python run_sensitivity.py [--output OUTPUT_ROOT]

Run run_asa.py first; this reads output/history/forecast_stats_history.csv
and writes output/sensitivity/.
"""
import argparse
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asa import config, sensitivity, tables

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(config.OUTPUT_ROOT))
    a = ap.parse_args()
    out = Path(a.output)
    hist = tables.load_history(out / "history" / tables.HISTORY_FILE)
    rep = sensitivity.run(hist, out / "sensitivity")
    for norm in ("generalized", "site"):
        r = rep[norm]
        print(f"\n== {norm}: {r['n_forecasts']} forecasts, {r['n_stations']} stations")
        print(r["corr"].round(2))
        print("pooled CRITIC:", {k: round(v, 3) for k, v in r["critic"].items()})
        print(f"rank Spearman vs equal weights under random weights: median {r['rho_median']:.3f}, p5 {r['rho_p5']:.3f}")
        print(r["table"].drop(columns=["Name"]).to_string())
        print(r["dist"].drop(columns=["Name"]).to_string())
    print("\nrank correlation generalized vs site normalization:", round(rep["norm_rank_rho"], 3))
    print(rep["norm_rank_moves"].head(10))
