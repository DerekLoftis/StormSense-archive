"""October 2025 king tide + nor'easter deck in the monthly-deck format.

Tidewatch forecasts and NOAA observations come from Derek's four workbooks
(T01..T14 sheets); the NWS forecasts are the archived HML issuances that match
his digitised hydrographs (run_oct2025_hml.py). Every time is put on true UTC:
the SWPT, LEWI and WMPT workbooks are on EDT although labelled UTC.

Usage: python3 run_event_deck_oct2025.py <dir with the 4 xlsx> [workers]
"""
import copy
import glob
import logging
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import run_oct2025_hml as R
from asa import animate, monthly_deck as M, validation as V
from asa.data import station_name
from asa.nws import HmlArchive

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("event_deck")
OUT = Path("output/event_decks/2025-10_kingtide_noreaster")
LABEL = "Oct 2025 King Tide + Nor'easter"
H36, H72 = pd.Timedelta(hours=36), pd.Timedelta(hours=72)


def records(src, hml):
    out = {}
    for st, (lid, shift) in R.STATIONS.items():
        path = sorted(glob.glob(str(Path(src) / f"*{st}_Tidewatch_vs_NWS_skill_stats.xlsx")))[-1]
        _, cyc, res, _, datum, _ = R.analyse(st, path, hml)
        clock = pd.Timedelta(hours=datum["clock_h"])     # workbook clock = UTC + clock
        _, wobs, _ = R.read_workbook(path)
        obs = wobs.set_axis(wobs.index - clock)
        hobs = hml.observed(lid) - shift
        obs = obs.combine_first(hobs).sort_index()
        fc = hml._forecasts(lid)
        recs = []
        for c, (_, r) in zip(cyc, res.iterrows()):
            tw = c["tw"].set_axis(c["tw"].index - clock).astype(float)
            t0 = tw.index[0]
            twp = pd.DataFrame({"predicted": tw, "observed": obs.reindex(tw.index)}).dropna()
            rec = {"station": st, "issued": t0, "t0": t0, "cycle": c["label"], "lid": lid, "tw": tw,
                   "tw_pairs": twp, "obs": obs, "tw_stats": V._stats(twp), "coverage": 1.0,
                   "nws": None, "obs_source": "noaa"}
            iss = r.hml_issued_utc
            if iss is not None and not pd.isna(iss):
                ser = (fc[iss] - shift).loc[t0:t0 + H72]
                nwp = pd.DataFrame({"predicted": ser, "observed": obs.reindex(ser.index)}).dropna()
                rec["nws"] = {"lid": lid, "issued": iss, "capture": "IEM HML archive", "shift": shift,
                              "shift_median": datum["median"], "shift_sd": datum["sd"], "series": ser,
                              "pairs": nwp, "s36": V._stats(nwp[nwp.index <= t0 + H36]), "s72": V._stats(nwp),
                              "done72": ser.index.max() >= t0 + V.REACH_MIN}
            recs.append(rec)
        out[st] = recs
    return out


def main():
    src = sys.argv[1]
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    by = records(src, HmlArchive("input/nws_hml_oct2025"))
    OUT.mkdir(parents=True, exist_ok=True)
    jobs, sections = [], []
    for st, recs in by.items():
        sdir = OUT / "animations" / st
        sec = {"station": st, "forecasts": [], "table_png": OUT / "tables" / f"{st}_forecast_table.png"}
        sec["table_png"].parent.mkdir(parents=True, exist_ok=True)
        M.table_png(st, recs, sec["table_png"], LABEL)
        items = []
        for i, r in enumerate(recs):
            color = M.seq_color(i)
            horizon = 72 if (r["nws"] and r["nws"]["done72"]) else 36
            spec = copy.copy(V.build_spec(r, horizon, None))
            spec["tw_color"] = color
            spec["rows"] = [dict(spec["rows"][0], color=color)] + spec["rows"][1:]
            spec["obs_label"] = "NOAA Observation"
            spec["heading"] = f"{animate.cycle_label(r['issued'])} 36-hr Forecast"
            spec["notes"] = [n.replace("Tidewatch 3-day hindcast and 30-day hindcast", "NOAA CO-OPS 6-min (verification workbook, "
                                       "extended with NWS HML obs)").replace("Tidewatch 3-day hindcast", "NOAA CO-OPS 6-min "
                                       "(verification workbook)").replace("(median NWPS minus Tidewatch observation)",
                                       "(workbook datum check)").replace("NWS NWPS", "NWS HML archive")
                             for n in spec["notes"]]
            spec["subtitle"] = (f"{station_name(st)}  (Tidewatch {st} / NWS {r['lid']})   ·   "
                                f"{animate.cycle_label(r['issued'])} UTC   ·   forecast {i + 1} of {len(recs)}, {LABEL}")
            mp4 = sdir / f"{i + 1:02d}_{st}_{animate.file_label(r['issued'])}_{horizon}hr.mp4"
            if not mp4.exists():
                jobs.append(("forecast", spec, mp4))
            sec["forecasts"].append({"issued": r["issued"], "skill": r["tw_stats"].get("Skill", np.nan),
                                     "nws": bool(r["nws"]), "mp4": mp4, "poster": mp4.with_suffix(".jpg")})
            items.append({"series": r["tw"].loc[r["t0"]:r["t0"] + H36], "issued": r["issued"],
                          "skill": r["tw_stats"].get("Skill", np.nan), "color": color})
        comp = sdir / f"{len(recs) + 1:02d}_{st}_composite.mp4"
        if not comp.exists():
            jobs.append(("composite", {"items": items, "obs": recs[0]["obs"],
                                       "title": f"{station_name(st)} ({st}) — Tidewatch 36-hour forecasts, {LABEL}",
                                       "subtitle": "Observed water level in blue; each 36-h forecast in sequence color, "
                                                   "labeled with its start (UTC) and Tidewatch Skill"}, comp))
        sec["composite"], sec["composite_poster"] = comp, comp.with_suffix(".jpg")
        sections.append(sec)
    log.info("%d animations to render", len(jobs))
    if jobs:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for p in ex.map(M._job, jobs):
                log.info("wrote %s", p)

    rows = []
    for st, recs in by.items():
        m = lambda k, key="tw_stats": float(np.nanmean([(r[key] if key == "tw_stats" else (r["nws"] or {}).get(key) or {}).get(k, np.nan)  # noqa: E731
                                                       for r in recs]))
        rows.append({"Station": st, "Name": station_name(st), "n": len(recs),
                     "TW RMSE (ft)": m("RMSE"), "TW Skill": m("Skill"),
                     "NWS 36-h RMSE": m("RMSE", "s36"), "NWS 36-h Skill": m("Skill", "s36"),
                     "NWS 72-h RMSE": m("RMSE", "s72")})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT / "station_summary.csv", index=False)
    n = sum(len(v) for v in by.values())
    summary = {
        "forecasts": n, "runs": n, "stations": len(by), "table": tab,
        "title": f"Tidewatch vs. NWS, {LABEL}",
        "subtitle": "SWPT, LEWI, WMPT and YRCG, 9-16 October 2025, with NWS forecasts from the IEM HML archive",
        "intro": [
            f"{n} Tidewatch 36-hour forecasts (14 per station), from Derek's verification workbooks.",
            "Each station: a table of its forecasts, then one animation per forecast (colored in sequence red, orange, "
            "yellow, green, blue, indigo, violet), then a composite of all 14.",
            "NWS forecasts: the archived HML issuance matching each hydrograph digitised during the shutdown "
            "(same issue time), verified over the same 36 h and out to 72 h.",
            "Observations: NOAA CO-OPS 6-min, ft above MLLW. All times UTC (the SWPT, LEWI and WMPT workbooks are on EDT; "
            "shifted here).",
            "Tidewatch Skill = 0.25 [R² + e^(−|Bias|/σ) + e^(−MAE/σ) + e^(−RMSE/σ)], σ = SD of observations in the window.",
        ],
    }
    deck = OUT / "Tidewatch_vs_NWS_Oct2025_KingTide_Noreaster.pptx"
    M.build_deck(sections, summary, deck, LABEL)
    log.info("deck %s", deck)


if __name__ == "__main__":
    main()
