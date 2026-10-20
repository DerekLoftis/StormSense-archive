"""Re-verify Derek's October 2025 Tidewatch vs NWS workbooks with archived NWS forecasts.

The NWS series in those workbooks were digitised from NWPS hydrograph images
during the federal shutdown. IEM's archive of issued HML products holds the
forecasts NWS actually sent. This script:
  1. matches each digitised hydrograph to the HML issuance with the same issue time;
  2. compares HML with the digitised values (digitisation error);
  3. rescores NWS with the HML values over the same 36-hr and 72-hr windows, with
     Tidewatch Skill for every series;
  4. writes copies of the four workbooks with H01..H14 sheets, new Summary
     columns, an "HML issuances" sheet scoring every archived issuance of the
     event, and a ReadMe addendum.

Usage: python3 run_oct2025_hml.py <dir with the 4 xlsx> [hml_dir] [out_dir]
"""
import glob
import re
import sys
from copy import copy
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from asa.nws import HmlArchive

STATIONS = {  # station: (NWS LID, datum correction subtracted in the workbook, ft)
    "SWPT": ("SWPV2", 0.0), "LEWI": ("LWTV2", 0.0),
    "WMPT": ("WNDV2", 0.0444), "YRCG": ("YKTV2", 0.0452),
}
EDT = pd.Timedelta(hours=4)
MATCH_TOL = pd.Timedelta(minutes=20)


def skill(f, o):
    j = pd.concat([f.rename("f"), o.rename("o")], axis=1, sort=True).dropna()
    if len(j) < 3:
        return dict(n=len(j))
    d = j.f - j.o
    sd = j.o.std(ddof=0)
    r2 = np.corrcoef(j.f, j.o)[0, 1] ** 2
    b, mae, rmse = d.mean(), d.abs().mean(), np.sqrt((d ** 2).mean())
    s = 0.25 * (r2 + np.exp(-abs(b) / sd) + np.exp(-mae / sd) + np.exp(-rmse / sd))
    return dict(n=len(j), r2=r2, bias=b, mae=mae, rmse=rmse, sd=sd, skill=s)


def rows(ws):
    return [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]


def truthy(v):
    return v is True or (isinstance(v, str) and v.upper().startswith("=TRUE"))


def read_workbook(path):
    wb = openpyxl.load_workbook(path)
    obs, cyc = {}, []
    i = 1
    while f"T{i:02d}" in wb.sheetnames:
        T, N = rows(wb[f"T{i:02d}"]), rows(wb[f"N{i:02d}"])
        for r in T + N:
            if r[2] is not None:
                obs[pd.Timestamp(r[0])] = r[2]
        meta = wb[f"N{i:02d}"]["H2"].value or ""
        m = re.search(r"issued (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) EDT", meta)
        cyc.append(dict(
            i=i, label=wb["Summary"].cell(i + 2, 2).value, meta=meta,
            image=wb[f"N{i:02d}"]["H1"].value,
            issued=pd.Timestamp(m.group(1), tz="UTC") + EDT if m else None,
            tw=pd.Series([r[1] for r in T], index=pd.to_datetime([r[0] for r in T])),
            nd=pd.Series([r[1] for r in N], index=pd.to_datetime([r[0] for r in N]), dtype=float),
            win=pd.Series([truthy(r[5]) for r in N], index=pd.to_datetime([r[0] for r in N])),
        ))
        i += 1
    return wb, pd.Series(obs).sort_index(), cyc


def time_offset(hobs, obs):
    best = None
    for h in range(-6, 7):
        s = hobs.set_axis(hobs.index + pd.Timedelta(hours=h))
        j = pd.concat([s.rename("h"), obs.rename("o")], axis=1, sort=True).dropna()
        if len(j) > 100:
            sd = (j.h - j.o).std(ddof=0)
            if best is None or sd < best[0]:
                best = (sd, h)
    return pd.Timedelta(hours=best[1]) if best and best[1] else pd.Timedelta(0)


def match(fc, issued):
    if issued is None or not fc:
        return None, "no issue time"
    best = min(fc, key=lambda t: abs(t - issued))
    if abs(best - issued) <= MATCH_TOL:
        return best, "same issuance"
    prior = [t for t in fc if t <= issued]
    return (max(prior), f"nearest earlier issuance ({(issued - max(prior)) / pd.Timedelta(hours=1):.1f} h before)") if prior else (None, "none")


def analyse(station, wb_path, hml):
    lid, shift = STATIONS[station]
    wb, obs, cyc = read_workbook(wb_path)
    fc = hml._forecasts(lid)
    hobs = hml.observed(lid)
    # Some workbooks label local (EDT) times as UTC. Find the whole-hour offset that
    # puts the HML observations (true UTC) on the workbook's clock.
    clock = time_offset(hobs, obs)
    if clock:
        hobs = hobs.copy()
        hobs.index = hobs.index + clock
        fc = {t + clock: s.set_axis(s.index + clock) for t, s in fc.items()}
    dj = pd.concat([hobs.rename("h"), obs.rename("o")], axis=1, sort=True).dropna()
    datum = dict(median=float((dj.h - dj.o).median()) if len(dj) else np.nan,
                 sd=float((dj.h - dj.o).std(ddof=0)) if len(dj) else np.nan, n=len(dj))
    # Observations for scoring: the workbook's NOAA values, extended with HML obs (datum-corrected).
    o_all = obs.combine_first(hobs - (datum["median"] if len(dj) else shift)) if len(hobs) else obs
    o_hr = o_all[o_all.index.minute == 0]
    out = []
    for c in cyc:
        t_iss, how = match(fc, c["issued"] + clock if c["issued"] is not None else None)
        h = (fc[t_iss] - shift) if t_iss is not None else pd.Series(dtype=float)
        hh = h.reindex(c["nd"].index)
        w36 = c["win"][c["win"]].index
        r = dict(station=station, cycle=c["i"], label=c["label"], image=c["image"],
                 digitised_issued_utc=c["issued"], hml_issued_utc=t_iss, match=how,
                 hml_points=int(hh.notna().sum()))
        o_tw = obs.reindex(c["tw"].index)
        for k, v in skill(c["tw"], o_tw).items():
            r[f"tw_{k}"] = v
        for tag, f, idx in [("dig36", c["nd"], w36), ("dig72", c["nd"], c["nd"].index),
                            ("hml36", hh, w36), ("hml72", hh, c["nd"].index)]:
            for k, v in skill(f.reindex(idx), o_hr.reindex(idx)).items():
                r[f"{tag}_{k}"] = v
        d = (c["nd"] - hh).dropna()
        r.update(dig_minus_hml_n=len(d), dig_minus_hml_mean=d.mean() if len(d) else np.nan,
                 dig_minus_hml_rmse=np.sqrt((d ** 2).mean()) if len(d) else np.nan,
                 dig_minus_hml_maxabs=d.abs().max() if len(d) else np.nan,
                 dig_peak=c["nd"].max(), hml_peak=hh.max(),
                 dig_peak_time=c["nd"].idxmax(), hml_peak_time=hh.idxmax() if hh.notna().any() else None)
        # Timing check: shift the digitised trace by whole hours against the full HML series.
        if t_iss is not None:
            full = fc[t_iss] - shift
            lags = []
            for L in (-2, -1, 0, 1, 2):
                dd = (c["nd"].set_axis(c["nd"].index + pd.Timedelta(hours=L)) - full).dropna()
                lags.append((np.sqrt((dd ** 2).mean()), L))
            r["dig_best_lag_h"], r["dig_rmse_at_lag"] = min(lags)[1], min(lags)[0]
        out.append(r)
        c["hml"], c["hml_issued"], c["match"] = hh, t_iss, how
        c["hml_issued_utc"] = t_iss - clock if t_iss is not None else None
        r["hml_issued_utc"] = c["hml_issued_utc"]
    # Every archived issuance during the event, scored over its first 72 h.
    t0, t1 = obs.index.min() - pd.Timedelta(days=1), obs.index.max()
    used = {c["hml_issued"] for c in cyc}
    allrows = []
    for t in sorted(fc):
        if not (t0 <= t <= t1):
            continue
        s = (fc[t] - shift)
        s = s[(s.index >= t) & (s.index <= t + pd.Timedelta(hours=72))]
        st36 = skill(s[s.index <= t + pd.Timedelta(hours=36)], o_hr)
        st72 = skill(s, o_hr)
        allrows.append(dict(station=station, issued_utc=t - clock, issued_edt=t - clock - EDT, points=len(s),
                            first=s.index.min() - clock, last=s.index.max() - clock, peak=s.max(), peak_time=s.idxmax() - clock,
                            digitised=t in used,
                            **{f"f36_{k}": v for k, v in st36.items()}, **{f"f72_{k}": v for k, v in st72.items()}))
    datum["clock_h"] = clock / pd.Timedelta(hours=1)
    return wb, cyc, pd.DataFrame(out), pd.DataFrame(allrows), datum, o_hr


# ---------------------------------------------------------------- workbook
BOLD = Font(name="Calibri", size=11, bold=True)
CENTER = Alignment(horizontal="center")


def _hdr(ws, cell, text):
    ws[cell] = text
    ws[cell].font = BOLD
    ws[cell].alignment = CENTER


def write_workbook(wb, station, cyc, res, allis, datum, o_hr, path):
    lid, shift = STATIONS[station]
    # H## sheets after each N##
    for c in cyc:
        i = c["i"]
        name = f"H{i:02d}"
        ws = wb.create_sheet(name, wb.sheetnames.index(f"N{i:02d}") + 1)
        heads = ["time (UTC)", f"NWS HML stage (ft MLLW{', datum-corrected' if shift else ''})",
                 "NOAA obs (ft MLLW)", "Diff (pred−obs)", "Abs diff", "In 36-hr TW window",
                 "Digitised NWPS stage (N sheet)", "Digitised − HML",
                 "HML, 36-hr window", "Obs, 36-hr window with HML", "Obs with HML", "Diff², 36-hr window",
                 "Obs, 36-hr window"]
        for j, h in enumerate(heads, 1):
            ws.cell(1, j, h).font = BOLD
        iss = c["hml_issued_utc"]
        ws["O1"] = (f"IEM HML archive, {lid}: forecast issued {iss:%Y-%m-%d %H:%M} UTC "
                    f"({(iss - EDT):%Y-%m-%d %H:%M} EDT); {c['match']}") if iss is not None else \
            f"No archived HML issuance found for {c['image']}"
        ws["O1"].font = BOLD
        ws["O2"] = f"Digitised source: {c['image']} ({c['meta']})"
        ws["O3"] = "Columns I-M are helpers for the Summary formulas."
        nsheet = f"N{i:02d}"
        for k, t in enumerate(c["nd"].index, 2):
            ws.cell(k, 1, t.strftime("%Y-%m-%dT%H:%M:%SZ"))
            v = c["hml"].get(t)
            if v is not None and not pd.isna(v):
                ws.cell(k, 2, round(float(v), 4)).number_format = "0.000"
            ws.cell(k, 3, f"={nsheet}!C{k}")
            ws.cell(k, 4, f'=IF(OR(B{k}="",C{k}=""),"",B{k}-C{k})').number_format = "0.000"
            ws.cell(k, 5, f'=IF(D{k}="","",ABS(D{k}))').number_format = "0.000"
            ws.cell(k, 6, f"={nsheet}!F{k}")
            ws.cell(k, 7, f"={nsheet}!B{k}").number_format = "0.000"
            ws.cell(k, 8, f'=IF(B{k}="","",G{k}-B{k})').number_format = "0.000"
            ws.cell(k, 9, f'=IF(AND(F{k},ISNUMBER(D{k})),B{k},"")')
            ws.cell(k, 10, f'=IF(AND(F{k},ISNUMBER(D{k})),C{k},"")')
            ws.cell(k, 11, f'=IF(ISNUMBER(D{k}),C{k},"")')
            ws.cell(k, 12, f'=IF(AND(F{k},ISNUMBER(D{k})),D{k}^2,"")')
            ws.cell(k, 13, f'=IF(F{k},C{k},"")')
        ws.column_dimensions["A"].width = 22
        for col in "BCDEFGHIJKLM":
            ws.column_dimensions[col].width = 15
        ws.freeze_panes = "A2"

    # Summary: new column groups to the right of T
    ws = wb["Summary"]
    nrow = len(cyc)
    groups = [("U", "U", "NWS HML archive issuance"),
              ("V", "Z", "NWS HML archive (same 36-hr window, hourly)"),
              ("AA", "AE", "NWS HML archive (full 72-hr window, hourly)"),
              ("AF", "AF", ""),
              ("AG", "AK", "Digitised vs. HML (72-hr, ft)"),
              ("AL", "AP", "Tidewatch Skill (0-1)"),
              ("AQ", "AR", "Digitised timing check")]
    for a, b, text in groups:
        if text:
            _hdr(ws, f"{a}1", text)
            if a != b:
                ws.merge_cells(f"{a}1:{b}1")
    heads = ["Issued (UTC)", "n", "R²", "Bias (ft)", "MAE (ft)", "RMSE (ft)", "n", "R²", "Bias (ft)",
             "MAE (ft)", "RMSE (ft)", "ΔRMSE (HML−TW)", "n", "Mean diff", "RMSE diff", "Max |diff|",
             "Peak diff", "Tidewatch 36-hr", "NWS digitised 36-hr", "NWS HML 36-hr", "NWS digitised 72-hr",
             "NWS HML 72-hr", "Best shift (h)", "RMSE after shift"]
    for j, h in enumerate(heads):
        _hdr(ws, f"{get_column_letter(21 + j)}2", h)
    for c in cyc:
        k, i = c["i"] + 2, c["i"]
        H, N, T = f"H{i:02d}", f"N{i:02d}", f"T{i:02d}"
        HB, HD, HE, HF = (f"{H}!B2:B80", f"{H}!D2:D80", f"{H}!E2:E80", f"{H}!F2:F80")
        HI, HJ, HK, HL, HM = (f"{H}!I2:I80", f"{H}!J2:J80", f"{H}!K2:K80", f"{H}!L2:L80", f"{H}!M2:M80")
        NC, TC = f"{N}!C2:C80", f"{T}!C2:C400"
        iss = c["hml_issued_utc"]

        def sk(r2, b, mae, rmse, sd):
            return (f"=0.25*({r2}{k}+EXP(-ABS({b}{k})/STDEVP({sd}))+EXP(-{mae}{k}/STDEVP({sd}))"
                    f"+EXP(-{rmse}{k}/STDEVP({sd})))")
        vals = [f"{iss:%Y-%m-%d %H:%M}" if iss is not None else "none",
                f"=COUNT({HI})", f"=RSQ({HJ},{HI})",
                f"=AVERAGEIF({HF},TRUE(),{HD})", f"=AVERAGEIF({HF},TRUE(),{HE})",
                f"=SQRT(SUM({HL})/V{k})",
                f"=COUNT({HB})", f"=RSQ({HK},{HB})",
                f"=AVERAGE({HD})", f"=AVERAGE({HE})", f"=SQRT(SUMSQ({HD})/COUNT({HD}))",
                f"=Z{k}-I{k}",
                f"=COUNT({H}!H2:H80)", f"=AVERAGE({H}!H2:H80)", f"=SQRT(SUMSQ({H}!H2:H80)/COUNT({H}!H2:H80))",
                f"=MAX(MAX({H}!H2:H80),-MIN({H}!H2:H80))", f"=MAX({N}!B2:B80)-MAX({HB})",
                sk("F", "G", "H", "I", TC), sk("K", "L", "M", "N", HM), sk("W", "X", "Y", "Z", HJ),
                sk("P", "Q", "R", "S", NC), sk("AB", "AC", "AD", "AE", HK)]
        rr = res[res.cycle == i].iloc[0]
        vals += [int(rr.dig_best_lag_h), round(float(rr.dig_rmse_at_lag), 4)]
        for j, v in enumerate(vals):
            cell = ws.cell(k, 21 + j, v)
            cell.alignment = CENTER
            cell.number_format = "General" if j in (0, 1, 6, 12, 22) else "0.000"
    k = nrow + 3
    for j in range(1, 24):
        col = get_column_letter(21 + j)
        if j in (1, 6, 12, 22):
            continue
        cell = ws.cell(k, 21 + j, f"=AVERAGE({col}3:{col}{nrow + 2})")
        cell.font, cell.alignment, cell.number_format = BOLD, CENTER, "0.000"
    for j in range(24):
        ws.column_dimensions[get_column_letter(21 + j)].width = 17 if j == 0 else 11

    # HML issuances sheet
    ws = wb.create_sheet("HML issuances", 2)
    cols = ["issued (UTC)", "issued (EDT)", "used for a digitised hydrograph", "points (≤72 h)",
            "peak (ft MLLW)", "peak time (UTC)", "36-h n", "36-h Bias", "36-h MAE", "36-h RMSE",
            "36-h Skill", "72-h n", "72-h Bias", "72-h MAE", "72-h RMSE", "72-h Skill"]
    for j, h in enumerate(cols, 1):
        ws.cell(1, j, h).font = BOLD
    for r_, row in enumerate(allis.itertuples(), 2):
        v = [f"{row.issued_utc:%Y-%m-%d %H:%M}", f"{row.issued_edt:%Y-%m-%d %H:%M}",
             "yes" if row.digitised else "no", row.points, row.peak, f"{row.peak_time:%Y-%m-%d %H:%M}",
             row.f36_n, getattr(row, "f36_bias", None), getattr(row, "f36_mae", None),
             getattr(row, "f36_rmse", None), getattr(row, "f36_skill", None), row.f72_n,
             getattr(row, "f72_bias", None), getattr(row, "f72_mae", None),
             getattr(row, "f72_rmse", None), getattr(row, "f72_skill", None)]
        for j, x in enumerate(v, 1):
            x = None if (isinstance(x, float) and np.isnan(x)) else x
            cell = ws.cell(r_, j, round(float(x), 4) if isinstance(x, (float, np.floating)) else x)
            if j >= 8 and j not in (12,):
                cell.number_format = "0.000"
    ws.cell(len(allis) + 3, 1, "Scored against NOAA observations (hourly) from the workbook, extended with "
            "NWS HML observations where the workbook stops. Statistics are values, not formulas.")
    for j in range(1, 17):
        ws.column_dimensions[get_column_letter(j)].width = 16
    ws.freeze_panes = "A2"

    # ReadMe addendum
    ws = wb["ReadMe"]
    r0 = ws.max_row + 2
    s = res
    lines = [
        "Addendum (8 Oct 2026): NWS forecasts recovered from the IEM HML archive",
        "",
        f"  The Iowa Environmental Mesonet keeps every HML product NWS issued. Its archive holds the {lid} forecasts",
        "  issued during the shutdown (mesonet.agron.iastate.edu/cgi-bin/request/hml.py). HML values are published to",
        "  0.1 ft, so a perfect digitisation would still differ from them by up to ±0.05 ft (RMS 0.029 ft).",
        "",
        "  H01..H14 : the HML forecast with the same issue time as each digitised hydrograph, at the same hours as",
        f"             the N sheet{f', minus the same {shift:+.4f} ft datum correction' if shift else ''}; column G repeats the digitised value.",
        "  Summary  : columns U-AP add the HML scores (36-hr and 72-hr), digitised-vs-HML differences and",
        "             Tidewatch Skill for every series (S = 0.25[R² + e^(-|Bias|/SD) + e^(-MAE/SD) + e^(-RMSE/SD)]).",
        "  HML issuances : every archived issuance during the event, scored over its first 36 h and 72 h.",
        "",
        f"  Datum check against HML observations: NWS obs − NOAA obs = {datum['median']:+.4f} ft "
        f"(sd {datum['sd']:.4f}, n={datum['n']}).",
        f"  Digitised − HML over all 14 hydrographs: mean {s.dig_minus_hml_mean.mean():+.3f} ft, "
        f"RMSE {np.sqrt((s.dig_minus_hml_rmse ** 2).mean()):.3f} ft, largest {s.dig_minus_hml_maxabs.max():.3f} ft.",
        f"  Mean 36-hr RMSE: Tidewatch {s.tw_rmse.mean():.3f}, NWS digitised {s.dig36_rmse.mean():.3f}, "
        f"NWS HML {s.hml36_rmse.mean():.3f} ft. Mean 72-hr RMSE: digitised {s.dig72_rmse.mean():.3f}, "
        f"HML {s.hml72_rmse.mean():.3f} ft.",
        f"  Timing: {int((s.dig_best_lag_h == 1).sum())} of {len(s)} digitised traces run one hour early against the archived forecast",
        "  (the value digitised for hour t is NWS's forecast for t + 1 h). Shifted, they match HML",
        f"  to RMSE {s.dig_rmse_at_lag.mean():.3f} ft on average, close to the 0.029 ft floor set by HML's 0.1-ft rounding: the",
        "  vertical digitisation was sound; the hour assignment was not. Summary columns AQ-AR give the best shift per cycle.",
        *([f"  Clock: the time columns of this workbook are EDT, not UTC as labelled. NWS HML observations (UTC) match the",
           f"  NOAA observations here only when moved {datum['clock_h']:+.0f} h, so the HML series are placed on the workbook's",
           "  clock. Tidewatch, observations and the digitised series share that clock, so the original statistics stand."]
          if datum.get("clock_h") else []),
        f"  Mean Tidewatch Skill: Tidewatch {s.tw_skill.mean():.3f}, NWS digitised 36-hr {s.dig36_skill.mean():.3f}, "
        f"NWS HML 36-hr {s.hml36_skill.mean():.3f}.",
    ]
    for j, line in enumerate(lines):
        ws.cell(r0 + j, 1, line)
    ws.cell(r0, 1).font = BOLD
    wb.save(path)


def main():
    src = Path(sys.argv[1])
    hml_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("input/nws_hml_oct2025")
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("output/oct2025_hml")
    out.mkdir(parents=True, exist_ok=True)
    hml = HmlArchive(hml_dir)
    res_all, iss_all = [], []
    for st in STATIONS:
        f = sorted(glob.glob(str(src / f"*{st}_Tidewatch_vs_NWS_skill_stats.xlsx")))[-1]
        wb, cyc, res, allis, datum, o_hr = analyse(st, f, hml)
        write_workbook(wb, st, cyc, res, allis, datum, o_hr, out / f"{st}_Tidewatch_vs_NWS_skill_stats_HML.xlsx")
        res["datum_median"], res["datum_sd"] = datum["median"], datum["sd"]
        res_all.append(res)
        iss_all.append(allis)
        print(st, "datum", datum, "matched", (res.match == "same issuance").sum(), "of", len(res))
    pd.concat(res_all).to_csv(out / "cycle_comparison.csv", index=False, float_format="%.4f")
    pd.concat(iss_all).to_csv(out / "hml_issuances.csv", index=False, float_format="%.4f")


if __name__ == "__main__":
    main()
