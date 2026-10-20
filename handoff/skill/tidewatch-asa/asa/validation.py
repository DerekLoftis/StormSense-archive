"""Tidewatch vs NWS NWPS forecast validation: animations, tables, workbooks.

For every NOAA station and Tidewatch run:
  * once the 36-hour window has passed and the Tidewatch 3-day hindcast
    covers it, a ~5 s animation of the 36-hour forecast (with the NWS
    forecast over the same 36 hours when one was archived);
  * once 72 hours have passed, a ~6 s animation that also verifies the NWS
    forecast out to 72 hours (grey diamonds beyond the Tidewatch window),
    plus a station table PNG and a monthly workbook in the layout of Derek's
    October 2025 SWPT/YRCG/WMPT/LEWI set.

Animations already on disk are not redrawn, so this is cheap to run after
every 6-hourly capture.
"""
import logging
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import animate, config, data, metrics, nws
from .data import station_name
from .stations import STATIONS

log = logging.getLogger("asa.validation")

NOAA_STATIONS = [s["tidewatch_id"] for s in STATIONS if s["source"] == "NOAA"]
OUT_DIR = "validation_animations"
STATS_FILE = "tw_vs_nws_stats.csv"
TABLE_CYCLES = 14
# Tidewatch publishes these stations' observations as byte-for-byte copies of
# another gauge's series (output/aliasing/ALIASING.md), so they are validated
# against the NWS NWPS observations for the real gauge where those exist.
USE_NWPS_OBS = {"DAHL", "WADC"}

H36, H72 = pd.Timedelta(hours=36), pd.Timedelta(hours=72)
REACH_MIN = pd.Timedelta(hours=60)
ROW_COLORS = ["#FF0000", "#FFC000", "#FFFF00", "#92D050", "#00B0F0", "#9DD3FF", "#CC66FF"]


def _stats(df):
    if df is None or len(df) < 3:
        return {"N": 0 if df is None else len(df)}
    s = metrics.statistics(df)
    s["Skill"] = metrics.skill_score(s, s["SDobs"])
    return s


def nwps_root(roots):
    for r in data.archive_roots(roots):
        cand = Path(r).parent / "nwps"
        if cand.exists():
            return cand
    return None


def build_records(roots, now=None, stations=None, archive=None):
    now = now or pd.Timestamp.now(tz="UTC")
    stations = stations or NOAA_STATIONS
    forecasts = data.load_forecasts(roots, "MLLW", stations)
    obs = data.load_observations(roots, "MLLW")
    root = nwps_root(roots)
    arch = archive or (nws.NwpsArchive(root) if root else None)
    shifts = {}
    records = []
    for f in forecasts:
        st = f.station
        if now < f.end:
            continue
        lid = nws.NWPS_LID.get(st)
        if arch and lid and st not in shifts:
            shifts[st] = nws.datum_shift(arch.observed(lid), obs.get(st))
        o = obs.get(st)
        obs_source = "tidewatch"
        if st in USE_NWPS_OBS and arch and lid:
            alt = arch.observed(lid)
            if data.coverage(f, data.pair(f, alt)) >= config.MIN_COVERAGE:
                o, obs_source = alt, "nwps"
            else:
                obs_source = "tidewatch-aliased"
        twp = data.pair(f, o)
        cov = data.coverage(f, twp)
        if cov < config.MIN_COVERAGE:
            continue
        t0 = f.start
        rec = {"station": st, "issued": f.issued, "t0": t0, "cycle": f.archive_cycle, "lid": lid,
               "tw": f.data.set_index("time")["px30"].dropna(), "tw_pairs": twp, "obs": o,
               "tw_stats": _stats(twp), "coverage": cov, "nws": None, "obs_source": obs_source}
        nf = arch.forecast_for(lid, f.issued, t0) if (arch and lid) else None
        if nf is not None:
            med, sd, n = shifts.get(st, (np.nan, np.nan, 0))
            shift = nws.applied_shift(med, sd)
            ser = (nf.series - shift).loc[t0:t0 + H72]
            nwp = pd.DataFrame({"predicted": ser, "observed": o.reindex(ser.index)}).dropna()
            p36 = nwp[nwp.index <= t0 + H36]
            # Some gauges' forecasts run exactly 72 h from issuance, so an issuance a few hours
            # before the Tidewatch run ends short of t0 + 72 h; accept 60 h or more.
            last = ser.index.max() if len(ser) else t0
            hours = int((min(last, t0 + H72) - t0) / pd.Timedelta(hours=1)) + 1
            done72 = bool(now >= t0 + H72 and last >= t0 + REACH_MIN
                          and len(nwp) >= config.MIN_COVERAGE * hours)
            rec["nws"] = {"lid": lid, "issued": nf.issued, "capture": nf.capture, "shift": shift,
                          "shift_median": med, "shift_sd": sd, "series": ser, "pairs": nwp,
                          "s36": _stats(p36), "s72": _stats(nwp) if done72 else None, "done72": done72}
        records.append(rec)
    return records, shifts


def _row(glyph, color, label, stats, glyph2=None, gsize=17):
    return {"glyph": glyph, "glyph2": glyph2, "color": color, "label": label, "stats": stats, "gsize": gsize}


def build_spec(rec, horizon, nwps_start=None):
    st, t0 = rec["station"], rec["t0"]
    t0_label = t0.round("h")
    lid = rec["lid"] or "none"
    n = rec["nws"]
    twp = rec["tw_pairs"]
    m, c = np.polyfit(twp["predicted"].to_numpy(float), twp["observed"].to_numpy(float), 1)
    rows = [_row("●", animate.TW_COLOR, f"VIMS Tidewatch −36 hr (n={rec['tw_stats']['N']})", rec["tw_stats"], gsize=20)]
    notes = []
    nws_series = nws_pairs = None
    if n is not None:
        nws_series, nws_pairs = n["series"], n["pairs"]
        if horizon == 36:
            nws_series = nws_series[nws_series.index <= t0 + H36]
            nws_pairs = nws_pairs[nws_pairs.index <= t0 + H36]
        rows.append(_row("■", "black", f"NWS Forecast −36 hr (n={n['s36']['N']})", n["s36"]))
        if horizon == 72:
            rows.append(_row("■", "black", f"NWS Forecast −72 hr (n={n['s72']['N']})", n["s72"], glyph2="◆"))
            notes.append("Grey diamonds lie beyond the 36-hour Tidewatch window and count only toward the −72 hr statistics.")
        shift = (f"datum shift of {-n['shift']:+.3f} ft applied (median NWPS minus Tidewatch observation)."
                 if n["shift"] else "no datum shift was required at this gauge.")
        notes.append(f"NWS NWPS {n['lid']} forecast issued {n['issued']:%b %d %H:%MZ}; {shift}")
    elif nwps_start is not None and t0 < nwps_start:
        notes.append(f"No NWS NWPS forecast: the NWPS archive begins {nwps_start:%d %b %Y %H}Z.")
    else:
        notes.append("No NWS NWPS forecast was archived within 3 hours of this Tidewatch run.")
    src = f"NWS NWPS {lid} observed stage" if rec["obs_source"] == "nwps" else (
        "Tidewatch 3-day hindcast" + (" and 30-day hindcast" if horizon == 72 else ""))
    if rec["obs_source"] == "tidewatch-aliased":
        notes.append(f"Caution: Tidewatch's {st} observations copy another gauge's series and no NWPS "
                     "observations cover this window, so this compares the forecast with the wrong gauge.")
    notes.append(f"Observations: {src}, ft above MLLW. "
                 "Skill = 0.25[R² + e^(−|Bias|/σ) + e^(−MAE/σ) + e^(−RMSE/σ)], σ = SD of observations.")
    if horizon == 72:
        title = "VIMS Tidewatch 3.0 vs. NWS NWPS — 72-hour Forecast Envelope"
    elif n is not None:
        title = "VIMS Tidewatch 3.0 vs. NWS NWPS — 36-hour Storm Tide Forecast"
    else:
        title = "VIMS Tidewatch 3.0 — 36-hour Storm Tide Forecast Validation"
    return {
        "station": st, "horizon": horizon, "t0": t0_label,
        "title": title,
        "subtitle": f"{station_name(st)}  (Tidewatch {st} / NWPS {lid})   ·   {animate.cycle_label(t0_label)} cycle",
        "tw": rec["tw"], "tw_pairs": twp, "obs": rec["obs"], "nws": nws_series, "nws_pairs": nws_pairs,
        "fit": (m, c), "rows": rows, "notes": notes,
        "obs_label": {"nwps": "NWPS Observation", "tidewatch-aliased": "Tidewatch Observation"}.get(
            rec["obs_source"], "NOAA Observation"),
    }


def animation_path(out_root, station, t0, horizon):
    t0 = t0.round("h")
    return out_root / station / f"{horizon}hr" / f"{station}_{animate.file_label(t0)}_{horizon}hr.mp4"


def _render(args):
    spec, path = args
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".part.mp4")
    animate.render(spec, tmp)
    tmp.replace(path)
    return str(path)


def _flat(prefix, s):
    keys = ["N", "R2", "Bias", "MAE", "RMSE", "SDobs", "Skill"]
    return {f"{prefix}_{k}": (s or {}).get(k, np.nan) for k in keys}


def stats_table(records):
    rows = []
    for r in records:
        n = r["nws"] or {}
        row = {"station": r["station"], "issued": r["issued"], "forecast_start": r["t0"].round("h"),
               "nws_lid": r["lid"], "nws_issued": n.get("issued"), "nws_capture": n.get("capture"),
               "nws_shift_ft": n.get("shift"), **_flat("tw36", r["tw_stats"]),
               **_flat("nws36", n.get("s36")), **_flat("nws72", n.get("s72"))}
        row["dRMSE36"] = row["nws36_RMSE"] - row["tw36_RMSE"]
        rows.append(row)
    return pd.DataFrame(rows)


def table_png(station, recs, path):
    """Station table in the layout of Derek's SWPT/YRCG/WMPT/LEWI PNGs."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": animate._FONT})
    n = len(recs)
    fig = plt.figure(figsize=(27.72, 3.5 + 0.5 * n), dpi=100)
    ax = fig.add_axes([0.005, 0.0, 0.99, 1.0])
    ax.set_axis_off()
    H = 2.6 + n + 1.9
    ax.set_xlim(0, 100)
    ax.set_ylim(H, 0)
    xs = {"#": 2.2, "start": 4, "d": 32.5, "tR2": 41, "tB": 48.5, "tM": 56, "tR": 63.5,
          "nR2": 71, "nB": 77.5, "nM": 84, "nR": 91.5}
    fs = 15
    ax.plot([0.5, 99.5], [0.15, 0.15], color="black", lw=1.5)
    ax.text(52, 0.75, "VIMS Tidewatch  (36-hr window, 6-min)", ha="center", va="center", fontsize=fs, fontweight="bold")
    ax.text(81, 0.75, "NWS NWPS  (same 36-hr window, hourly)", ha="center", va="center", fontsize=fs, fontweight="bold")
    ax.plot([37.5, 66.5], [1.25, 1.25], color="black", lw=1)
    ax.plot([67.5, 99.5], [1.25, 1.25], color="black", lw=1)
    hdr = {"#": "#", "start": "Forecast Start", "d": "ΔRMSE", "tR2": "R²", "tB": "Bias", "tM": "MAE",
           "tR": "RMSE", "nR2": "R²", "nB": "Bias", "nM": "MAE", "nR": "RMSE"}
    for k, h in hdr.items():
        ax.text(xs[k] + (12 if k == "start" else 0), 1.8, h, ha="center", va="center", fontsize=fs, fontweight="bold")
    ax.plot([0.5, 99.5], [2.35, 2.35], color="black", lw=1.5)

    def f3(v, nd=3):
        return "–" if pd.isna(v) else f"{v:.{nd}f}".replace("-", "−")

    cols = {"tR2": [], "tB": [], "tM": [], "tR": [], "nR2": [], "nB": [], "nM": [], "nR": [], "d": []}
    for i, r in enumerate(recs):
        y = 2.6 + i + 0.5
        t, s = r["tw_stats"], r["nws"]["s36"]
        d = s.get("RMSE", np.nan) - t.get("RMSE", np.nan)
        color = ROW_COLORS[i % len(ROW_COLORS)]
        ax.add_patch(matplotlib.patches.Rectangle((xs["start"], y - 0.45), 25, 0.9, facecolor=color,
                                                  edgecolor="#999999", lw=0.8))
        ax.text(xs["#"], y, str(i + 1), ha="center", va="center", fontsize=fs)
        ax.text(xs["start"] + 0.6, y, f"{animate.cycle_label(r['t0'].round('h'))} 36-hr Forecast", va="center",
                fontsize=fs, color="white" if color == "#FF0000" else "black")
        ax.text(xs["d"], y, f3(d), ha="center", va="center", fontsize=fs, fontweight="bold",
                color="#C00000" if d > 0 else "#006100")
        vals = {"tR2": f3(t.get("R2"), 4), "tB": f3(t.get("Bias")), "tM": f3(t.get("MAE")), "tR": f3(t.get("RMSE")),
                "nR2": f3(s.get("R2"), 4), "nB": f3(s.get("Bias")), "nM": f3(s.get("MAE")), "nR": f3(s.get("RMSE"))}
        for k, v in vals.items():
            ax.text(xs[k], y, v, ha="center", va="center", fontsize=fs)
        for k, v in zip(["tR2", "tB", "tM", "tR", "nR2", "nB", "nM", "nR", "d"],
                        [t.get("R2"), t.get("Bias"), t.get("MAE"), t.get("RMSE"),
                         s.get("R2"), s.get("Bias"), s.get("MAE"), s.get("RMSE"), d]):
            cols[k].append(v)
    y = 2.6 + n + 0.6
    ax.plot([0.5, 99.5], [y - 0.55, y - 0.55], color="black", lw=1.5)
    ax.text(xs["start"] + 12.5, y, f"Mean Value Across All {n} Forecast Cycles", ha="center", va="center",
            fontsize=fs, fontweight="bold")
    for k, v in cols.items():
        mval = np.nanmean(v) if len(v) else np.nan
        ax.text(xs[k], y, f3(mval, 4 if k.endswith("R2") else 3), ha="center", va="center", fontsize=fs,
                fontweight="bold", color=("#C00000" if mval > 0 else "#006100") if k == "d" else "black")
    ax.plot([0.5, 99.5], [y + 0.55, y + 0.55], color="black", lw=1.5)
    shifts = sorted({r["nws"]["shift"] for r in recs})
    shift_txt = ("no datum shift required at this gauge" if shifts == [0.0]
                 else "NWS datum shift " + ", ".join(f"{-v:+.3f}" for v in shifts) + " ft applied")
    ax.text(0.5, y + 1.05, f"{station_name(station)} (VIMS Tidewatch {station} / NWS NWPS {recs[0]['lid']}). "
            "Bias = mean(forecast − observation), ft above MLLW.  ΔRMSE = NWS − Tidewatch.  "
            f"Observations from the Tidewatch 3-day hindcast; {shift_txt}.",
            fontsize=12.5, style="italic", color="#444444", va="center")
    fig.savefig(path, dpi=100, facecolor="white")
    plt.close(fig)


def workbook(station, recs, path):
    """Monthly workbook in Derek's layout: ReadMe, Summary (live formulas), T##/N## sheets."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    navy = PatternFill("solid", fgColor="12355B")
    white = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    wb = Workbook()
    rd = wb.active
    rd.title = "ReadMe"
    lid = recs[0]["lid"]
    lines = [
        f"{station} / {lid} — VIMS Tidewatch vs. NWS NWPS forecast verification, {recs[0]['t0']:%B %Y}",
        "",
        f"Station   : {station_name(station)}. NWS NWPS gauge {lid}. Stage is ft above MLLW throughout.",
        "",
        "Summary   : per-cycle skill for Tidewatch (36-hr) and NWS NWPS (same 36-hr window, and full 72-hr window).",
        "T01..     : Tidewatch 6-min storm-tide forecast (px30) paired with the observation at the same timestamp.",
        "N01..     : NWS NWPS hourly forecast stage from the archived stageflow JSON, same observations.",
        "",
        "Observations: VIMS Tidewatch 3-day hindcast (36-hr) and 30-day hindcast (72-hr) CSVs, ft above MLLW.",
        "NWS forecast: the NWPS stageflow capture nearest each Tidewatch run (within -1/+3 h).",
        "Datum check: median of NWPS observed minus Tidewatch observed at matching times; subtracted from the NWS",
        f"  forecast when |median| >= {nws.SHIFT_MIN_FT} ft and its SD <= {nws.SHIFT_MAX_SD_FT} ft.",
        "Tidewatch Skill S = 0.25*[R^2 + EXP(-|Bias|/SD) + EXP(-MAE/SD) + EXP(-RMSE/SD)]; SD = STDEVP of observations.",
        "Times are UTC.",
    ]
    for i, line in enumerate(lines, 1):
        rd.cell(i, 1, line).font = Font(name="Consolas", size=10, bold=(i == 1))
    rd.column_dimensions["A"].width = 120

    ws = wb.create_sheet("Summary")
    groups = [(5, "VIMS Tidewatch (36-hr window, 6-min)"), (10, "NWS NWPS (same 36-hr window, hourly)"),
              (15, "NWS NWPS (full 72-hr window, hourly)")]
    for col, text in groups:
        ws.cell(1, col, text).font = Font(name="Arial", bold=True, size=10)
    hdr = ["#", "Forecast Start", "Tidewatch cycle", "NWPS capture", "n", "R²", "Bias (ft)", "MAE (ft)", "RMSE (ft)",
           "n", "R²", "Bias (ft)", "MAE (ft)", "RMSE (ft)", "n", "R²", "Bias (ft)", "MAE (ft)", "RMSE (ft)",
           "ΔRMSE (NWS−TW)", "TW Skill", "NWS −36 Skill", "NWS −72 Skill"]
    for j, h in enumerate(hdr, 1):
        c = ws.cell(2, j, h)
        c.font, c.fill = white, navy
        c.alignment = Alignment(horizontal="center", wrap_text=True)
    for i, r in enumerate(recs, 1):
        k = i + 2
        T, N = f"T{i:02d}", f"N{i:02d}"
        t = wb.create_sheet(T)
        for j, h in enumerate(["time (UTC)", "Tidewatch px30 (ft MLLW)", "Observed (ft MLLW)", "Diff (pred−obs)", "Abs diff"], 1):
            t.cell(1, j, h).font = Font(name="Arial", bold=True, size=10)
        t.cell(1, 7, animate.cycle_label(r["t0"].round("h")))
        p = r["tw_pairs"]
        for q, (ts, row) in enumerate(p.iterrows(), 2):
            t.cell(q, 1, ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
            t.cell(q, 2, float(row["predicted"]))
            t.cell(q, 3, float(row["observed"]))
            t.cell(q, 4, f"=B{q}-C{q}")
            t.cell(q, 5, f"=ABS(D{q})")
        tl = len(p) + 1
        nn = wb.create_sheet(N)
        for j, h in enumerate(["time (UTC)", "NWPS stage (ft MLLW, datum-corrected)", "Observed (ft MLLW)",
                               "Diff (pred−obs)", "Abs diff", "In 36-hr TW window"], 1):
            nn.cell(1, j, h).font = Font(name="Arial", bold=True, size=10)
        n = r["nws"]
        nn.cell(1, 8, f"NWPS {n['lid']} forecast issued {n['issued']:%Y-%m-%d %H:%MZ}; capture {n['capture']}; "
                      f"datum shift subtracted {n['shift']:+.3f} ft")
        np_ = n["pairs"]
        for q, (ts, row) in enumerate(np_.iterrows(), 2):
            nn.cell(q, 1, ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
            nn.cell(q, 2, round(float(row["predicted"]), 4))
            nn.cell(q, 3, float(row["observed"]))
            nn.cell(q, 4, f"=B{q}-C{q}")
            nn.cell(q, 5, f"=ABS(D{q})")
            nn.cell(q, 6, "=TRUE()" if ts <= r["t0"] + H36 else "=FALSE()")
        nl = len(np_) + 1
        for ws_ in (t, nn):
            ws_.column_dimensions["A"].width = 22
            for col in "BCDEF":
                ws_.column_dimensions[col].width = 18
        TB, TC, TD = f"{T}!B2:B{tl}", f"{T}!C2:C{tl}", f"{T}!D2:D{tl}"
        NB, NC, ND, NE, NF = (f"{N}!{x}2:{x}{nl}" for x in "BCDEF")
        vals = [i, f"{animate.cycle_label(r['t0'].round('h'))} 36-hr Forecast", r["cycle"], n["capture"],
                f"=COUNT({TB})", f"=RSQ({TB},{TC})", f"=AVERAGE({TD})", f"=AVERAGE({T}!E2:E{tl})",
                f"=SQRT(SUMSQ({TD})/COUNT({TD}))",
                f"=COUNTIF({NF},TRUE())", f"=RSQ(IF({NF},{NB}),IF({NF},{NC}))",
                f"=AVERAGEIF({NF},TRUE(),{ND})", f"=AVERAGEIF({NF},TRUE(),{NE})",
                f"=SQRT(SUMPRODUCT(--{NF},{ND}^2)/COUNTIF({NF},TRUE()))",
                f"=COUNT({NB})", f"=RSQ({NB},{NC})", f"=AVERAGE({ND})", f"=AVERAGE({NE})",
                f"=SQRT(SUMSQ({ND})/COUNT({ND}))", f"=N{k}-I{k}",
                f"=0.25*(F{k}+EXP(-ABS(G{k})/STDEVP({TC}))+EXP(-H{k}/STDEVP({TC}))+EXP(-I{k}/STDEVP({TC})))",
                f"=0.25*(K{k}+EXP(-ABS(L{k})/STDEVP(IF({NF},{NC})))+EXP(-M{k}/STDEVP(IF({NF},{NC})))"
                f"+EXP(-N{k}/STDEVP(IF({NF},{NC}))))",
                f"=0.25*(P{k}+EXP(-ABS(Q{k})/STDEVP({NC}))+EXP(-R{k}/STDEVP({NC}))+EXP(-S{k}/STDEVP({NC})))"]
        for j, v in enumerate(vals, 1):
            c = ws.cell(k, j, v)
            c.font = Font(name="Arial", size=10)
            if j >= 5:
                c.number_format = "0" if j in (5, 10, 15) else "0.000"
                c.alignment = Alignment(horizontal="center")
        ws.cell(k, 2).fill = PatternFill("solid", fgColor=ROW_COLORS[(i - 1) % len(ROW_COLORS)][1:])
    last = len(recs) + 2
    ws.cell(last + 1, 2, f"Mean Value Across All {len(recs)} Forecast Cycles").font = Font(name="Arial", bold=True, size=10)
    for j in list(range(6, 10)) + list(range(11, 15)) + list(range(16, 24)):
        col = ws.cell(2, j).column_letter
        c = ws.cell(last + 1, j, f"=AVERAGE({col}3:{col}{last})")
        c.font, c.number_format = Font(name="Arial", bold=True, size=10), "0.000"
        c.alignment = Alignment(horizontal="center")
    for j, w in enumerate([5, 36, 22, 20] + [9] * 19, 1):
        ws.column_dimensions[ws.cell(2, j).column_letter].width = w
    ws.freeze_panes = "C3"
    wb.move_sheet("Summary", offset=-1)
    wb.save(path)


def run(roots=None, output_root=None, now=None, stations=None, workers=4, force=False, limit=None):
    roots = data.archive_roots(roots)
    out = Path(output_root or config.OUTPUT_ROOT) / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    records, shifts = build_records(roots, now, stations)
    root = nwps_root(roots)
    nwps_start = None
    if root:
        caps = [nws._capture_time(p.name) for p in root.glob("*/*/*") if p.is_dir()]
        caps = [c for c in caps if c is not None]
        nwps_start = min(caps) if caps else None

    jobs = []
    for r in records:
        p36 = animation_path(out, r["station"], r["t0"], 36)
        if force or not p36.exists():
            jobs.append((build_spec(r, 36, nwps_start), p36))
        if r["nws"] and r["nws"]["done72"]:
            p72 = animation_path(out, r["station"], r["t0"], 72)
            if force or not p72.exists():
                jobs.append((build_spec(r, 72, nwps_start), p72))
    if limit:
        jobs = jobs[:limit]
    log.info("%d validation records, %d animations to render", len(records), len(jobs))
    done = []
    if jobs:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for p in ex.map(_render, jobs):
                done.append(p)
                log.info("wrote %s", p)

    table = stats_table(records)
    if stations and (out / STATS_FILE).exists():  # keep the other stations' rows
        prev = pd.read_csv(out / STATS_FILE)
        table = pd.concat([prev[~prev["station"].isin(stations)], table], ignore_index=True)
    if not table.empty:
        table.sort_values(["station", "forecast_start"]).to_csv(out / STATS_FILE, index=False)
    sh = pd.DataFrame([{"station": s, "nwps_lid": nws.NWPS_LID.get(s), "median_nwps_minus_tw_ft": v[0],
                        "sd_ft": v[1], "n": v[2], "applied_shift_ft": nws.applied_shift(v[0], v[1])}
                       for s, v in sorted(shifts.items())])
    if stations and (out / "nwps_datum_check.csv").exists():
        prev = pd.read_csv(out / "nwps_datum_check.csv")
        sh = pd.concat([prev[~prev["station"].isin(stations)], sh], ignore_index=True)
    if not sh.empty:
        sh.sort_values("station").to_csv(out / "nwps_datum_check.csv", index=False)

    by_station = {}
    for r in records:
        if r["nws"] and r["nws"]["done72"]:
            by_station.setdefault(r["station"], []).append(r)
    for st, recs in by_station.items():
        recs.sort(key=lambda r: r["t0"])
        table_png(st, recs[-TABLE_CYCLES:], out / st / f"{st}_Tidewatch_vs_NWS_36hr_table.png")
        months = {}
        for r in recs:
            months.setdefault(r["t0"].strftime("%B_%Y"), []).append(r)
        for mon, mrecs in months.items():
            workbook(st, mrecs, out / st / f"{st}_{mon}_Tidewatch_vs_NWS.xlsx")
    return {"records": len(records), "rendered": done, "tables": sorted(by_station)}
