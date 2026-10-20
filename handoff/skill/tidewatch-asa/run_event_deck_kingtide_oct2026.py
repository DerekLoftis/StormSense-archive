"""King tide weekend (8-12 Oct 2026) after-event situation report deck.

Every archived Tidewatch 36-hour forecast issued from 8 Oct 00Z to now, in the
monthly-deck format (station table, one animation per forecast, composite),
with the archived NWS NWPS forecast for comparison. Forecasts whose window has
not ended yet are verified with the observations available so far and marked
as partial. A synopsis of the other archived guidance (STOFS-2D, STOFS-3D,
NWM coastal, CBOFS) is appended at the end (asa/event_models.py).

    python3 run_event_deck_kingtide_oct2026.py render [workers]
    python3 run_event_deck_kingtide_oct2026.py deck
"""
import copy
import os
import logging
import pickle
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asa import animate, config, data, monthly_deck as M, nws, validation as V  # noqa: E402
from asa.data import station_name  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("kingtide")

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT.parent / "tidewatch-archive" / "data"
OUT = ROOT / "output" / "event_decks" / os.environ.get("KT_OUT", "2026-10_kingtide_v2")
LABEL = "Catch the King Tide, 8-12 Oct 2026"
T_LO = pd.Timestamp("2026-10-08 00:00", tz="UTC")
T_HI = pd.Timestamp("2026-10-12 18:00", tz="UTC")   # last run in the event window
H36, H72 = pd.Timedelta(hours=36), pd.Timedelta(hours=72)
MIN_PAIRS = 10            # 1 h of 6-min observations before a forecast is animated


def build_records():
    roots = [os.environ.get("TW_ROOT", str(ARCHIVE / "tidewatch"))]
    forecasts = [f for f in data.load_forecasts(roots, "MLLW") if T_LO <= f.issued <= T_HI]
    obs = data.load_observations(roots, "MLLW")
    arch = nws.NwpsArchive(ARCHIVE / "nwps")
    obs_end = {st: s.dropna().index.max() for st, s in obs.items() if s is not None and len(s)}
    shifts, recs, pending = {}, [], []
    for f in forecasts:
        st = f.station
        o = obs.get(st)
        lid = nws.NWPS_LID.get(st)
        if lid and st not in shifts:
            shifts[st] = nws.datum_shift(arch.observed(lid), o)
        obs_source = "tidewatch"
        if st in V.USE_NWPS_OBS and lid:
            alt = arch.observed(lid)
            if len(data.pair(f, alt)) >= MIN_PAIRS:
                o, obs_source = alt, "nwps"
            else:
                obs_source = "tidewatch-aliased"
        twp = data.pair(f, o)
        last = o.dropna().index.max() if o is not None and len(o) else None
        partial = last is None or last < f.end
        if partial:
            elapsed = (min(last, f.end) - f.start) / pd.Timedelta(minutes=6) + 1 if last is not None else 0
            cov = len(twp) / elapsed if elapsed > 0 else 0.0
        else:
            cov = data.coverage(f, twp)
        rec = {"station": st, "issued": f.issued, "t0": f.start, "cycle": f.archive_cycle, "lid": lid,
               "tw": f.data.set_index("time")["px30"].dropna(), "tw_pairs": twp, "obs": o,
               "tw_stats": V._stats(twp), "coverage": cov, "nws": None, "obs_source": obs_source,
               "partial": partial, "obs_last": last,
               "hours_verified": len(twp) * 0.1}
        nf = arch.forecast_for(lid, f.issued, f.start) if lid else None
        if nf is not None:
            med, sd, _ = shifts.get(st, (np.nan, np.nan, 0))
            shift = nws.applied_shift(med, sd)
            ser = (nf.series - shift).loc[f.start:f.start + H72]
            nwp = pd.DataFrame({"predicted": ser, "observed": o.reindex(ser.index) if o is not None else np.nan}).dropna()
            p36 = nwp[nwp.index <= f.start + H36]
            reach = last is not None and last >= f.start + pd.Timedelta(hours=42)
            rec["nws"] = {"lid": lid, "issued": nf.issued, "capture": nf.capture, "shift": shift,
                          "shift_median": med, "shift_sd": sd, "series": ser, "pairs": nwp,
                          "s36": V._stats(p36), "s72": V._stats(nwp), "done72": bool(reach),
                          "partial72": bool(last is None or last < f.start + H72)}
        if len(twp) >= MIN_PAIRS and (cov >= config.MIN_COVERAGE or partial):
            recs.append(rec)
        else:
            pending.append(rec)
    return recs, pending, shifts, obs


def spec_for(r, i, n, color):
    horizon = 72 if (r["nws"] and r["nws"]["done72"]) else 36
    spec = copy.copy(V.build_spec(r, horizon, None))
    spec["tw_color"] = color
    spec["rows"] = [dict(spec["rows"][0], color=color)] + spec["rows"][1:]
    st = r["station"]
    spec["obs_label"] = "Tidewatch Observation" if r["obs_source"] == "tidewatch-aliased" else (
        "NWPS Observation" if r["obs_source"] == "nwps" else M.obs_label(st))
    spec["heading"] = f"{animate.cycle_label(r['issued'])} 36-hr Forecast"
    spec["notes"] = [x for x in spec["notes"] if not x.startswith("No NWS NWPS forecast")]
    if r["partial"] or (r["nws"] and horizon == 72 and r["nws"]["partial72"]):
        end = r["t0"] + (H72 if horizon == 72 else H36)
        spec["notes"].insert(0, f"PARTIAL: verified with observations available through {r['obs_last']:%d %b %H:%MZ} "
                                f"({min(r['obs_last'], end) - r['t0'] if r['obs_last'] else pd.Timedelta(0)} "
                                f"of the {horizon} h window); statistics cover that part only.".replace(" days ", " d ")
                             .replace("0 d ", ""))
    if st in M.COPY_OF:
        spec["notes"].insert(0, f"Caution: {M.COPY_OF[st]}")
    elif st in M.ALIASED and r["obs_source"] != "tidewatch-aliased":
        spec["notes"].insert(0, f"Caution: Tidewatch publishes the same observation series for {st} as for "
                                f"{M.ALIASED[st]} (aliasing check).")
    if not r["nws"]:
        spec["title"] = "VIMS Tidewatch 3.0 — 36-hour Storm Tide Forecast Validation"
    spec["subtitle"] = (f"{station_name(st)}  (Tidewatch {st}{' / NWPS ' + r['lid'] if r['lid'] else ''})   ·   "
                        f"{animate.cycle_label(r['issued'])} run   ·   forecast {i + 1} of {n}, {LABEL}")
    return spec, horizon


def plan():
    recs, pending, shifts, obs = build_records()
    _, _, by, order, sections, jobs = plan_from(recs)
    return recs, pending, by, order, sections, jobs, shifts, obs


def plan_from(recs):
    pending = []
    by = {}
    for r in recs:
        by.setdefault(r["station"], []).append(r)
    order = M.station_order(set(by))
    jobs, sections = [], []
    for st in order:
        rs = sorted(by[st], key=lambda r: r["issued"])
        sdir = OUT / "animations" / st
        sec = {"station": st, "forecasts": [], "table_png": OUT / "tables" / f"{st}_forecast_table.png"}
        items = []
        for i, r in enumerate(rs):
            color = M.seq_color(i)
            spec, horizon = spec_for(r, i, len(rs), color)
            tag = "partial" if (r["partial"] or (r["nws"] and horizon == 72 and r["nws"]["partial72"])) else "full"
            mp4 = sdir / f"{i + 1:02d}_{st}_{animate.file_label(r['issued'])}_{horizon}hr_{tag}.mp4"
            if not mp4.exists():
                jobs.append(("forecast", spec, mp4))
            sec["forecasts"].append({"issued": r["issued"], "skill": r["tw_stats"].get("Skill", np.nan),
                                     "nws": bool(r["nws"]), "partial": tag == "partial",
                                     "mp4": mp4, "poster": mp4.with_suffix(".jpg")})
            items.append({"series": r["tw"].loc[r["t0"]:r["t0"] + H36], "issued": r["issued"],
                          "skill": r["tw_stats"].get("Skill", np.nan), "color": color})
        comp = sdir / f"{len(rs) + 1:02d}_{st}_composite.mp4"
        if not comp.exists():
            jobs.append(("composite", {"items": items, "obs": rs[0]["obs"],
                                       "title": f"{station_name(st)} ({st}) — Tidewatch 36-hour forecasts, {LABEL}",
                                       "subtitle": "Observed water level in blue (through "
                                                   f"{rs[-1]['obs_last']:%d %b %H:%MZ}); each 36-h forecast in sequence "
                                                   "color, labeled with its issuance (UTC) and Tidewatch Skill"}, comp))
        sec["composite"], sec["composite_poster"] = comp, comp.with_suffix(".jpg")
        sections.append(sec)
    return recs, pending, by, order, sections, jobs


def render(workers):
    OUT.mkdir(parents=True, exist_ok=True)
    recs, pending, by, order, sections, jobs, shifts, obs = plan()
    for sec in sections:
        sec["table_png"].parent.mkdir(parents=True, exist_ok=True)
        M.table_png(sec["station"], sorted(by[sec["station"]], key=lambda r: r["issued"]), sec["table_png"], LABEL)
    with open(OUT / "records.pkl", "wb") as fh:
        pickle.dump({"recs": recs, "pending": pending, "order": order, "shifts": shifts,
                     "obs": {k: v.loc["2026-10-01":] for k, v in obs.items() if v is not None}}, fh)
    log.info("%d stations, %d forecasts (%d partial), %d pending, %d animations to render", len(sections), len(recs),
             sum(r["partial"] for r in recs), len(pending), len(jobs))
    # Composites first is not needed; render in station order.
    if jobs:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for p in ex.map(M._job, jobs):
                log.info("wrote %s", p)


# --------------------------------------------------------------------------- deck
def _picture_slide(prs, title, sub, png, top=1.35, max_h=5.95, note=None):
    from PIL import Image
    from pptx.util import Inches, Pt

    s = prs.slides.add_slide(prs.slide_layouts[6])
    M._title(s, title, sub)
    im = Image.open(png)
    w = 12.6
    h = w * im.height / im.width
    if h > max_h:
        h, w = max_h, max_h * im.width / im.height
    s.shapes.add_picture(str(png), Inches((13.333 - w) / 2), Inches(top), Inches(w), Inches(h))
    if note:
        tb = s.shapes.add_textbox(Inches(0.4), Inches(7.05), Inches(12.5), Inches(0.4)).text_frame
        tb.word_wrap = True
        tb.paragraphs[0].text = note
        tb.paragraphs[0].font.size = Pt(11)
        tb.paragraphs[0].font.italic = True
    return s


def _text_slide(prs, title, sub, lines, size=17):
    from pptx.util import Inches, Pt

    s = prs.slides.add_slide(prs.slide_layouts[6])
    M._title(s, title, sub)
    tb = s.shapes.add_textbox(Inches(0.5), Inches(1.45), Inches(12.3), Inches(5.8)).text_frame
    tb.word_wrap = True
    for i, line in enumerate(lines):
        p = tb.paragraphs[0] if i == 0 else tb.add_paragraph()
        p.text = line
        p.font.size = Pt(size)
        p.space_after = Pt(8)
    return s


def _move(prs, slide, index):
    lst = prs.slides._sldIdLst
    el = next(x for x in lst if prs.slides.part.related_part(x.rId).slide is slide)
    lst.remove(el)
    lst.insert(index, el)


def deck():
    from pptx import Presentation

    R = pickle.load(open(OUT / "records.pkl", "rb"))
    recs = R["recs"]
    _, _, by, order, sections, jobs = plan_from(recs)
    missing = [j[2] for j in jobs]
    if missing:
        log.warning("%d animations missing, e.g. %s", len(missing), missing[:3])
        sections = [sec for sec in sections if all(f["mp4"].exists() for f in sec["forecasts"]) and sec["composite"].exists()]
    obs_last = max(r["obs_last"] for r in recs if r["obs_last"] is not None)
    rows = []
    for st in order:
        rs = by[st]
        m = lambda k, key="tw_stats": float(np.nanmean([(r[key] if key == "tw_stats" else ((r["nws"] or {}).get(key) or {})).get(k, np.nan)  # noqa: E731
                                                       for r in rs]))
        rows.append({"Station": st + (" ⚠" if st in M.ALIASED else ""), "Name": station_name(st), "n": len(rs),
                     "R²": m("R2"), "Bias (ft)": m("Bias"), "RMSE (ft)": m("RMSE"), "Tidewatch Skill": m("Skill"),
                     "NWS 36-h RMSE": m("RMSE", "s36")})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT / "station_summary.csv", index=False)
    tab["NWS 36-h RMSE"] = [f"{v:.3f}" if pd.notna(v) else "–" for v in tab["NWS 36-h RMSE"]]
    n = len(recs)
    npart = sum(r["partial"] for r in recs)
    nn = sum(1 for r in recs if r["nws"])
    summary = {
        "forecasts": n, "runs": len({r["issued"] for r in recs}), "stations": len(order), "table": tab,
        "title": "After-event situation report: 2026 Catch the King Tide Weekend",
        "subtitle": f"Every archived VIMS Tidewatch forecast, 8 Oct 00Z to 12 Oct 18Z, verified over its completed window "
                    f"(observations through {obs_last:%d %b %H:%MZ})",
        "intro": [
            f"{n} Tidewatch 36-hour forecasts from {len({r['issued'] for r in recs})} runs at {len(order)} stations. "
            + ("Every 36-hour window has now been completely observed." if not npart else
               f"{npart} are only partly verified (their slides say PARTIAL)."),
            "First: observed peaks, then the Monday-morning outlook for Norfolk and Hampton Roads as it was sent "
            "(12 Oct), with what was observed afterwards.",
            "Then each station: a table of its forecasts, one animation per forecast (red, orange, yellow, green, blue, indigo, "
            "violet in sequence), and a composite of all of them.",
            f"NWS: the NWPS forecast archived with each Tidewatch run ({nn} of {n} forecasts have one), verified over the same 36 h "
            "and, where observations reach far enough, out to 72 h.",
            "Last: a synopsis of every other archived guidance source (STOFS-2D-Global, STOFS-3D-Atlantic, NWM coastal short and "
            "medium range, CBOFS) over the same period, with their forecast plots.",
            "Observations: Tidewatch's own 3-day hindcast (NWPS observations of the real gauge at DAHL and WADC), ft above MLLW. "
            "Tidewatch Skill = 0.25 [R² + e^(−|Bias|/σ) + e^(−MAE/σ) + e^(−RMSE/σ)].",
        ],
    }
    path = OUT / "Tidewatch_CatchTheKing_Oct8-12_2026_SitRep_v2.pptx"
    M.build_deck(sections, summary, path, LABEL)

    prs = Presentation(str(path))
    # Mark partial forecasts in their slide subtitles.
    part = {(r["station"], i) for st, rs in by.items() for i, r in enumerate(sorted(rs, key=lambda r: r["issued"]))
            if r["partial"]}
    for sl in prs.slides:
        for sh in sl.shapes:
            if not sh.has_text_frame or len(sh.text_frame.paragraphs) < 2:
                continue
            t0, t1 = sh.text_frame.paragraphs[0].text, sh.text_frame.paragraphs[1]
            if len(t0) > 58:                      # keep long station titles on one line
                from pptx.util import Pt
                for r_ in sh.text_frame.paragraphs[0].runs:
                    r_.font.size = Pt(28 * 58 / len(t0) if len(t0) < 80 else 20)
            mm = re.search(r"\(([A-Z0-9]{4})\)", t0)
            mi = re.search(r"forecast (\d+) of (\d+)", t1.text)
            if mm and mi and (mm.group(1), int(mi.group(1)) - 1) in part:
                r_ = t1.add_run()
                r_.text = f"  · PARTIAL (obs to {obs_last:%d %b %H:%MZ})"
                r_.font.size = t1.runs[0].font.size
                r_.font.bold = True
            break

    # Front matter: event peaks and today's outlook, right after the title and intro.
    OL = OUT / "outlook"
    pk = pd.read_csv(OL / "observed_event_peaks.csv", parse_dates=["peak_edt"])
    noaa = pk[pk.station.isin(["SWPT", "KIPT", "YRCG", "LEWI", "WMPT", "BISH", "SLIM", "WACH", "DAHL", "WADC", "USFM", "USJT"])]
    lines = [f"{r.station}  {r.name}: {r.peak_ft_mllw:.2f} ft MLLW at {r.peak_edt:%a %d %H:%M} EDT ({r.category})"
             for r in noaa.itertuples()]
    top = pk[~pk.station.isin(M.ALIASED.keys())].head(6)
    lines.append("Highest of the rest: " + "; ".join(f"{r.station} {r.peak_ft_mllw:.2f} ft ({r.peak_edt:%a %H:%M})"
                                                     for r in top.itertuples() if r.station not in set(noaa.station)))
    front = [
        _text_slide(prs, "Observed peaks, 8-14 Oct", "Highest water level at each gauge with NWS flood thresholds "
                    "(category from current NWS thresholds; DAHL and WADC from the real gauge's NWPS record)", lines, size=14),
        _picture_slide(prs, "Monday's outlook as issued: Sewells Point, Norfolk", "Latest forecast from every archived source as sent Monday "
                       "morning 12 Oct (observations then ran to 07:36 EDT), local time; later observations dotted", OL / "SWPT_today_all_sources.png"),
        _picture_slide(prs, "Monday's outlook as issued: Hampton Roads high tides", "Next two forecast high tides at each gauge "
                       "by source as sent Monday morning 12 Oct, and the observed high tides (last column)",
                       OL / "hampton_roads_next_high_tides.png"),
    ]
    for k, sl in enumerate(front):
        _move(prs, sl, 1 + k)

    # Synopsis of the other archived guidance.
    MD = OUT / "models"
    pr = pd.read_csv(MD / "paired_vs_tidewatch.csv")
    syn = []
    for r in pr.itertuples():
        syn.append(f"{r.source} ({r.stations} stations, {r.cycles} cycles): 36-h RMSE {r.RMSE:.2f} ft vs Tidewatch "
                   f"{r.TW_RMSE:.2f} at the same stations; bias {r.Bias:+.2f} vs {r.TW_Bias:+.2f}; skill {r.Skill:.3f} vs "
                   f"{r.TW_Skill:.3f}; mean |high-tide error| {r.peak_abs:.2f} vs {r.TW_peak_abs:.2f} ft "
                   f"(timing {r.peak_timing_min:+.0f} min).")
    _text_slide(prs, "Synopsis: all archived guidance, cycles 8-12 Oct",
                "Each source against Tidewatch at the same stations (DAHL and WADC left out: Tidewatch fits another gauge there)",
                syn + ["Bottom line: NWS and STOFS-2D scored best, with Tidewatch close behind (about 0.04 ft more RMSE at the same stations); "
                       "CBOFS was a little behind Tidewatch; STOFS-3D ran about 0.25 ft low; NWM coastal ran 0.6-0.7 ft low with "
                       "damped high tides (about 0.9 ft low at high water) at every gauge."],
                size=14)
    _picture_slide(prs, "Synopsis: each source vs Tidewatch", "Same stations, cycles issued 8 Oct 00Z to 12 Oct 18Z", MD / "bars_all_sources.png")
    for metric, sub in (("RMSE", "Mean 36-h RMSE (ft) by station and source"), ("Bias", "Mean 36-h bias (ft): forecast minus observed"),
                        ("Skill", "Mean Tidewatch Skill (36 h) by station and source")):
        _picture_slide(prs, f"Synopsis: {metric} by station", sub, MD / f"heat_{metric}.png",
                       note="DAHL and WADC: Tidewatch's forecasts there are fitted to another gauge's record (aliasing check), "
                            "so its scores against the real gauges are poor. STOFS-2D kept at NOAA gauges only.")
    for st in ["SWPT", "KIPT", "YRCG", "LEWI", "WMPT", "BISH", "DAHL", "WADC", "WACH", "USFM", "USJT", "USSF", "USPT"]:
        png = MD / f"forecasts_{st}.png"
        if png.exists():
            _picture_slide(prs, f"Forecast plots: {station_name(st)} ({st})", "Every archived cycle of every source issued 8 Oct 00Z to 12 Oct 18Z, "
                           "colored by issue time; observations blue", png, top=1.2, max_h=6.2)
    _text_slide(prs, "Notes and caveats", "", [
        "Datums: STOFS-2D and STOFS-3D point files are in ft MLLW; NWM coastal is in ft NAVD88, shifted with NOAA's NAVD88-MLLW "
        "at NOAA gauges and with Tidewatch's own MLLW-NAVD offset (1.645 ft at most USGS gauges) elsewhere; CBOFS zeta is m MSL, "
        "shifted with NOAA's MSL-MLLW (nearest station node within 3 km).",
        "STOFS-2D points at USGS gauges (USFM, USJT, USSF) sat 0.7-1.3 ft below the observations, so they were left out as "
        "not comparable. NWM's low bias appears at NOAA gauges with NOAA's own datums, so it is not a conversion error.",
        f"Observations run through {obs_last:%d %b %H:%MZ}, past the end of every 36-h Tidewatch window and every 72-h NWS window.",
        "Flood categories use current NWS thresholds (ft MLLW).",
        f"Files: output/event_decks/{OUT.name}/ (records, tables, animations, models/*.csv, outlook/*.csv).",
    ], size=15)
    prs.save(str(path))
    log.info("deck %s (%d slides)", path, len(prs.slides))
    return path


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "render"
    if cmd == "render":
        render(int(sys.argv[2]) if len(sys.argv) > 2 else 4)
    elif cmd == "deck":
        deck()
    elif cmd == "plan":
        recs, pending, by, order, sections, jobs, shifts, obs = plan()
        print(len(recs), "records", sum(r["partial"] for r in recs), "partial", len(pending), "pending", len(jobs), "jobs")
        print({st: len(v) for st, v in by.items()})
        print(sorted({(r["station"], str(r["issued"])) for r in pending})[:60])
