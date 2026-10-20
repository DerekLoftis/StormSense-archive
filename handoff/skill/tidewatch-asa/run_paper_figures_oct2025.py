"""Journal figures and tables for the October 2025 king tide + nor'easter, NWS from the IEM HML archive.

Redraws Derek's figure sets (7-panel time series + regression, cycles 1-7 and
8-14) and his 36-hr statistics tables for SWPT, LEWI, WMPT and YRCG, with the
digitised NWS hydrographs replaced by the archived HML issuances that have the
same issue time (run_oct2025_hml.py). Times are EDT on every station: the
SWPT, LEWI and WMPT workbooks are already on EDT; YRCG's is UTC and is shifted.

Usage: python3 run_paper_figures_oct2025.py <dir with the 4 xlsx>
Output: output/oct2025_hml/paper_figures/
"""
import glob
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402

import run_oct2025_hml as R  # noqa: E402
from asa.nws import HmlArchive  # noqa: E402

OUT = Path("output/oct2025_hml/paper_figures")
COLORS = ["#FF0000", "#FFC000", "#FFFF00", "#92D050", "#00B0F0", "#9DD3FF", "#CC66FF"]
TEXT_ON = {"#FF0000": "white"}
OBS_BLUE = "#5B9BD5"
NAMES = {
    "SWPT": "Sewells Point, VA — Hampton Roads at the mouth of the Elizabeth River",
    "LEWI": "Lewisetta, VA — Potomac River",
    "WMPT": "Windmill Point, VA — Chesapeake Bay at the mouth of the Rappahannock",
    "YRCG": "York River Coast Guard Training Center, VA",
}
# HAT/LAT above MLLW, as listed in Derek's manuscript notes: SWPT from the workbook Compare sheet, YRCG from
# the workbook OBS sheet, WMPT (NOAA 8636580, 1983-2001, from NAVD88) and LEWI (NOAA 8635750, 1983-2001).
DATUMS = {"SWPT": (3.70, -0.87), "LEWI": (1.96, -0.48), "WMPT": (2.00, -0.59), "YRCG": (3.49, -0.79)}
YLIM = {"SWPT": (-1, 7), "LEWI": (-1, 5), "WMPT": (-1, 5), "YRCG": (-1, 7)}
RLIM = {"SWPT": (0.5, 7), "LEWI": (0.5, 4.5), "WMPT": (0.5, 4.5), "YRCG": (0.5, 7)}
EDT = pd.Timedelta(hours=4)


def stats(f, o):
    j = pd.concat([f.rename("f"), o.rename("o")], axis=1, sort=True).dropna()
    d = j.f - j.o
    sd = j.o.std(ddof=0)
    r2 = np.corrcoef(j.f, j.o)[0, 1] ** 2
    b, mae, rmse = d.mean(), d.abs().mean(), np.sqrt((d ** 2).mean())
    sk = 0.25 * (r2 + np.exp(-abs(b) / sd) + np.exp(-mae / sd) + np.exp(-rmse / sd))
    return dict(n=len(j), r2=r2, bias=b, mae=mae, rmse=rmse, skill=sk)


def label(t):
    h = t.hour % 12 or 12
    return f"{t:%b}. {t:%d} {t:%A} {h}{'AM' if t.hour < 12 else 'PM'}"


def load(src, hml):
    data = {}
    for st in R.STATIONS:
        path = sorted(glob.glob(str(Path(src) / f"*{st}_Tidewatch_vs_NWS_skill_stats.xlsx")))[-1]
        _, cyc, res, _, datum, _ = R.analyse(st, path, hml)
        _, wobs, _ = R.read_workbook(path)
        # workbook clock = UTC + clock_h; EDT = UTC - 4 h
        to_edt = pd.Timedelta(hours=-datum["clock_h"]) - EDT
        mv = lambda s: s.set_axis(s.index + to_edt)  # noqa: E731
        obs = mv(wobs).astype(float)
        # Extend the workbook's NOAA record (9-16 Oct) with the NWS HML observations, which match it to 0.003 ft.
        lid, shift = R.STATIONS[st]
        hobs = hml.observed(lid) - shift
        obs = obs.combine_first(hobs.set_axis(hobs.index - EDT)).sort_index()
        rows = []
        for c, (_, r) in zip(cyc, res.iterrows()):
            tw, nws, win = mv(c["tw"]).astype(float), mv(c["hml"]).astype(float), mv(c["win"])
            nws = nws.dropna()
            in36 = nws[win.reindex(nws.index).fillna(False).astype(bool)]
            out36 = nws.drop(in36.index)
            o_tw, o_n = obs.reindex(tw.index), obs.reindex(nws.index)
            rows.append(dict(i=c["i"], start=tw.index[0], end=tw.index[-1], label=label(tw.index[0].tz_localize(None)),
                             tw=tw, nws=nws, in36=in36, out36=out36, issued=r.hml_issued_utc,
                             s_tw=stats(tw, o_tw), s36=stats(in36, obs.reindex(in36.index)), s72=stats(nws, o_n),
                             fit=np.polyfit(tw.values, o_tw.values, 1)))
        data[st] = dict(obs=obs, cyc=rows)
    return data


def day(t):
    return (t - pd.Timestamp("2025-10-01", tz=t.tz)) / pd.Timedelta(days=1) + 1


def figure(st, d, idx, path):
    plt.rcParams.update({"font.family": "Carlito", "font.size": 9})
    fig = plt.figure(figsize=(15.8, 20.1))
    gs = fig.add_gridspec(len(idx), 2, width_ratios=[2.0, 1.0], hspace=0.42, wspace=0.07,
                          left=0.045, right=0.99, top=0.99, bottom=0.03)
    hat, lat = DATUMS[st]
    obs = d["obs"]
    ox = day(obs.index)
    for row, k in enumerate(idx):
        c = d["cyc"][k]
        col = COLORS[k % 7]
        ax = fig.add_subplot(gs[row, 0])
        ax.plot(ox, obs.values, color=OBS_BLUE, lw=2.6, zorder=2)
        ax.plot(day(c["tw"].index), c["tw"].values, color=col, lw=2.6, zorder=3)
        ax.plot(day(c["nws"].index), c["nws"].values, color="black", lw=0.7, zorder=4)
        ax.plot(day(c["nws"].index), c["nws"].values, ls="none", marker="s", ms=3.2, color="black", zorder=5)
        for y, lab in [(hat, "HAT83"), (lat, "LAT83")]:
            ax.axhline(y, color="#666666", ls=(0, (5, 3)), lw=1.1, zorder=1)
            ax.text(9.06, y + 0.12, lab, fontsize=8.5, color="#333333")
        x0, x1 = day(c["start"]), day(c["end"])
        for x in (x0, x1):
            ax.axvline(x, color="black", ls=(0, (5, 3)), lw=1.4, zorder=6)
        ylo, yhi = YLIM[st]
        ya = lat + 0.28
        ax.annotate("", xy=(x1, ya), xytext=(x0, ya), arrowprops=dict(arrowstyle="<->", lw=0.9))
        ax.text((x0 + x1) / 2, ya + 0.12, "36-hr Tidewatch forecast window", ha="center", fontsize=7.5)
        ax.set_xlim(9, 16)
        ax.set_ylim(ylo, yhi)
        ax.set_xticks(range(9, 17))
        ax.set_yticks(range(ylo, yhi + 1))
        ax.grid(color="#D9D9D9", lw=0.7)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color("#888888")
        ax.spines["bottom"].set_color("#888888")
        ax.tick_params(colors="#555555", labelsize=8.5, length=0)
        ax.set_xlabel("Day in October 2025 (EDT)", fontsize=9, color="#444444")
        ax.set_ylabel("Water Level (ft. above MLLW)", fontsize=9, color="#444444")
        ax.text(-0.045, 1.0, f"({chr(97 + k)})", transform=ax.transAxes, fontsize=11, fontweight="bold", va="top")
        h = [Line2D([], [], ls="none", marker="s", ms=4, color="black"),
             Line2D([], [], ls="none", marker="o", ms=5, color=OBS_BLUE),
             Line2D([], [], ls="none", marker="o", ms=5, color=col),
             Line2D([], [], color="#666666", ls=(0, (5, 3))), Line2D([], [], color="#666666", ls=(0, (5, 3)))]
        lab = ["NWS Forecast", "NOAA Observation", f"{c['start']:%m/%d} {c['label'].split(' ', 2)[2]} 36-hr Forecast",
               "HAT83", "LAT83"]
        leg1 = ax.legend(h[:1], lab[:1], loc="upper left", bbox_to_anchor=(0.66, 1.04), frameon=False, fontsize=8,
                         handlelength=1.2, borderaxespad=0)
        ax.add_artist(leg1)
        leg2 = ax.legend(h[1:], lab[1:], loc="upper left", bbox_to_anchor=(0.775, 1.04), frameon=False, fontsize=8,
                         handlelength=1.6, labelspacing=0.25, borderaxespad=0)
        leg2.set_zorder(10)

        # Regression
        rx = fig.add_subplot(gs[row, 1])
        lo, hi = RLIM[st]
        rx.plot([lo, hi], [lo, hi], color="#BBBBBB", ls=(0, (4, 3)), lw=0.9, zorder=1)
        o = obs
        rx.scatter(c["tw"].values, o.reindex(c["tw"].index).values, s=9, color=col, zorder=3, linewidths=0)
        rx.scatter(c["out36"].values, o.reindex(c["out36"].index).values, s=14, marker="D", color="#808080", zorder=4,
                   linewidths=0)
        rx.scatter(c["in36"].values, o.reindex(c["in36"].index).values, s=14, marker="s", color="black", zorder=5,
                   linewidths=0)
        m, b = c["fit"]
        xs = np.array([c["tw"].min(), c["tw"].max()])
        rx.plot(xs, m * xs + b, color="black", lw=1.1, zorder=6)
        rx.set_xlim(lo, hi)
        rx.set_ylim(lo, hi)
        rx.set_aspect("auto")
        ticks = range(1, int(hi) + 1)
        rx.set_xticks(ticks)
        rx.set_yticks(ticks)
        for s in ("top", "right"):
            rx.spines[s].set_visible(False)
        rx.tick_params(colors="#555555", labelsize=8.5, length=0)
        rx.set_xlabel("Forecast Prediction (ft. above MLLW)", fontsize=9, color="#444444")
        rx.set_ylabel("NOAA Observation (ft. above MLLW)", fontsize=9, color="#444444")
        tx = rx.transAxes
        # Title and fit in the empty upper-left; statistics in the empty lower-right (data lie along the 1:1 line).
        rx.text(0.02, 0.985, f"{c['label']} 36-hr Forecast", transform=tx, fontsize=8.5, va="top", fontweight="bold",
                zorder=8)
        rx.text(0.02, 0.925, f"f(x) = {m:.4f} x {'+' if b >= 0 else '−'} {abs(b):.4f}   (Tidewatch)", transform=tx,
                fontsize=8.5, va="top", zorder=8)
        rx.add_patch(plt.Rectangle((0.215, 0.025), 0.78, 0.30, transform=tx, facecolor="white", alpha=0.85,
                                   edgecolor="none", zorder=7))
        cols = [0.595, 0.69, 0.785, 0.875, 0.96]
        for x, hd in zip(cols, ["R²", "Bias", "MAE", "RMSE", "Skill"]):
            rx.text(x, 0.305, hd, transform=tx, fontsize=8.2, fontweight="bold", ha="center", va="top", zorder=8)
        rows = [("Tidewatch", c["s_tw"], [("o", col)]), ("NWS −36 hr", c["s36"], [("s", "black")]),
                ("NWS −72 hr", c["s72"], [("s", "black"), ("D", "#808080")])]
        for j, (name, s, glyphs) in enumerate(rows):
            y = 0.235 - j * 0.07
            for g, (mk, gc) in enumerate(glyphs):
                rx.plot([0.235 + g * 0.028], [y - 0.022], marker=mk, ms=4, color=gc, transform=tx, clip_on=False,
                        zorder=8)
            rx.text(0.29, y, f"{name} (n={s['n']})", transform=tx, fontsize=8.2, va="top", zorder=8)
            for x, v in zip(cols, [f"{s['r2']:.4f}", f"{s['bias']:.3f}", f"{s['mae']:.3f}", f"{s['rmse']:.3f}",
                                   f"{s['skill']:.3f}"]):
                rx.text(x, y, v.replace("-", "−"), transform=tx, fontsize=8.2, ha="center", va="top", zorder=8)
    fig.savefig(path, dpi=200)
    plt.close(fig)


def table_png(st, d, path):
    plt.rcParams.update({"font.family": "Carlito"})
    cyc = d["cyc"]
    n = len(cyc)
    fig = plt.figure(figsize=(20, 9.9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(n + 5.3, -0.6)
    ax.axis("off")
    xs = dict(num=2.3, lab=3.6, d=31.5, tR2=38.5, tB=46.0, tM=53.5, tR=61.0, tS=67.0,
              nR2=73.5, nB=79.5, nM=85.5, nR=91.5, nS=97.0)
    fs = 14
    ax.plot([0.8, 99.2], [-0.4, -0.4], color="black", lw=1.4)
    ax.text((xs["tR2"] + xs["tS"]) / 2, 0.15, "VIMS Tidewatch  (36-hr window, 6-min, n=360)", ha="center",
            fontsize=fs, fontweight="bold")
    ax.text((xs["nR2"] + xs["nS"]) / 2, 0.15, "NWS (IEM HML archive)  (same 36-hr window, hourly)", ha="center",
            fontsize=fs, fontweight="bold")
    ax.plot([xs["tR2"] - 3, xs["tS"] + 3], [0.6, 0.6], color="black", lw=1)
    ax.plot([xs["nR2"] - 3, 99.2], [0.6, 0.6], color="black", lw=1)
    heads = [("num", "#"), ("d", "ΔRMSE"), ("tR2", "R²"), ("tB", "Bias"), ("tM", "MAE"), ("tR", "RMSE"),
             ("tS", "Skill"), ("nR2", "R²"), ("nB", "Bias"), ("nM", "MAE"), ("nR", "RMSE"), ("nS", "Skill")]
    for k, h in heads:
        ax.text(xs[k], 1.5, h, ha="center", va="center", fontsize=fs, fontweight="bold")
    ax.text(16.5, 1.5, "Forecast Start (EDT)", ha="center", va="center", fontsize=fs, fontweight="bold")
    ax.plot([0.8, 99.2], [2.2, 2.2], color="black", lw=1.4)

    def fmt(v, nd=3):
        return f"{v:.{nd}f}".replace("-", "−")

    for r, c in enumerate(cyc):
        y = 3.0 + r
        col = COLORS[r % 7]
        ax.add_patch(plt.Rectangle((xs["lab"], y - 0.42), 25.5, 0.84, facecolor=col, edgecolor="#BBBBBB", lw=0.6))
        ax.text(xs["lab"] + 0.8, y, f"{c['label']} 36-hr Forecast", va="center", fontsize=fs - 1,
                color=TEXT_ON.get(col, "black"))
        ax.text(xs["num"], y, str(r + 1), ha="center", va="center", fontsize=fs - 1)
        dr = c["s36"]["rmse"] - c["s_tw"]["rmse"]
        ax.text(xs["d"], y, fmt(dr), ha="center", va="center", fontsize=fs - 1, fontweight="bold",
                color="#C00000" if dr > 0 else "#006100")
        for k, key, s, nd in [("tR2", "r2", "s_tw", 4), ("tB", "bias", "s_tw", 3), ("tM", "mae", "s_tw", 3),
                              ("tR", "rmse", "s_tw", 3), ("tS", "skill", "s_tw", 3), ("nR2", "r2", "s36", 4),
                              ("nB", "bias", "s36", 3), ("nM", "mae", "s36", 3), ("nR", "rmse", "s36", 3),
                              ("nS", "skill", "s36", 3)]:
            ax.text(xs[k], y, fmt(c[s][key], nd), ha="center", va="center", fontsize=fs - 1)
    y = 3.0 + n
    ax.plot([0.8, 99.2], [y - 0.55, y - 0.55], color="black", lw=1.4)
    ax.text(16.5, y, f"Mean Value Across All {n} Forecast Cycles", ha="center", va="center", fontsize=fs - 1,
            fontweight="bold")
    mean = lambda s, k: float(np.mean([c[s][k] for c in cyc]))  # noqa: E731
    dm = mean("s36", "rmse") - mean("s_tw", "rmse")
    ax.text(xs["d"], y, fmt(dm), ha="center", va="center", fontsize=fs - 1, fontweight="bold",
            color="#C00000" if dm > 0 else "#006100")
    for k, key, s, nd in [("tR2", "r2", "s_tw", 4), ("tB", "bias", "s_tw", 3), ("tM", "mae", "s_tw", 3),
                          ("tR", "rmse", "s_tw", 3), ("tS", "skill", "s_tw", 3), ("nR2", "r2", "s36", 4),
                          ("nB", "bias", "s36", 3), ("nM", "mae", "s36", 3), ("nR", "rmse", "s36", 3),
                          ("nS", "skill", "s36", 3)]:
        ax.text(xs[k], y, fmt(mean(s, key), nd), ha="center", va="center", fontsize=fs - 1, fontweight="bold")
    ax.plot([0.8, 99.2], [y + 0.55, y + 0.55], color="black", lw=1.4)
    lid, shift = R.STATIONS[st]
    note = (f"{NAMES[st]} (VIMS Tidewatch {st} / NWS {lid}), October 2025. Bias = mean(forecast − observation), ft above "
            f"MLLW.  ΔRMSE = NWS − Tidewatch.  NWS forecasts are the archived HML products (Iowa Environmental Mesonet) "
            f"with the same issue times as the NWPS hydrographs, "
            + (f"shifted −{shift:.3f} ft onto the verifying datum." if shift else "no datum shift required at this gauge.")
            + "  Skill = 0.25[R² + e^(−|Bias|/σ) + e^(−MAE/σ) + e^(−RMSE/σ)], σ = SD of observations in the window.")
    import textwrap
    for j, line in enumerate(textwrap.wrap(note, 235)):
        ax.text(0.8, y + 1.15 + j * 0.45, line, fontsize=11, style="italic", color="#444444", va="center")
    fig.savefig(path, dpi=150)
    plt.close(fig)


THIN = Side(style="thin", color="000000")
MED = Side(style="medium", color="000000")


def table_sheet(ws, st, d):
    cyc = d["cyc"]
    n = len(cyc)
    lid, shift = R.STATIONS[st]
    ws["A1"] = f"{NAMES[st]} (VIMS Tidewatch {st} / NWS {lid}): Tidewatch vs. NWS, 36-hr window, October 2025"
    ws["A1"].font = Font(name="Calibri", size=12, bold=True)
    ws.merge_cells("D2:I2")
    ws.merge_cells("J2:O2")
    ws["D2"] = "VIMS Tidewatch (36-hr window, 6-min, n=360)"
    ws["J2"] = "NWS, IEM HML archive (same 36-hr window, hourly)"
    heads = ["#", "Forecast Start (EDT)", "ΔRMSE (NWS−TW)", "R²", "Bias", "MAE", "RMSE", "Skill", "SD obs",
             "n", "R²", "Bias", "MAE", "RMSE", "Skill", "NWS issued (UTC)"]
    for j, h in enumerate(heads, 1):
        c = ws.cell(3, j, h)
        c.font = Font(name="Calibri", bold=True)
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        c.border = Border(top=MED, bottom=MED)
    for cell in ("D2", "J2"):
        ws[cell].font = Font(name="Calibri", bold=True)
        ws[cell].alignment = Alignment(horizontal="center")
    for r, c in enumerate(cyc):
        k = r + 4
        col = COLORS[r % 7].lstrip("#")
        vals = [r + 1, f"{c['label']} 36-hr Forecast", f"=N{k}-G{k}", c["s_tw"]["r2"], c["s_tw"]["bias"],
                c["s_tw"]["mae"], c["s_tw"]["rmse"],
                f"=0.25*(D{k}+EXP(-ABS(E{k})/I{k})+EXP(-F{k}/I{k})+EXP(-G{k}/I{k}))",
                float(d["obs"].reindex(c["tw"].index).std(ddof=0)), c["s36"]["n"], c["s36"]["r2"], c["s36"]["bias"],
                c["s36"]["mae"], c["s36"]["rmse"], c["s36"]["skill"], f"{c['issued']:%Y-%m-%d %H:%M}"]
        for j, v in enumerate(vals, 1):
            cell = ws.cell(k, j, round(v, 6) if isinstance(v, float) else v)
            cell.alignment = Alignment(horizontal="left" if j == 2 else "center")
            cell.font = Font(name="Calibri")
            if j in (4, 11):
                cell.number_format = "0.0000"
            elif j in (3, 5, 6, 7, 8, 9, 12, 13, 14, 15):
                cell.number_format = "0.000"
        lab = ws.cell(k, 2)
        lab.fill = PatternFill("solid", fgColor=col)
        lab.font = Font(name="Calibri", color="FFFFFF" if COLORS[r % 7] in TEXT_ON else "000000")
        ws.cell(k, 3).font = Font(name="Calibri", bold=True)
    k = n + 4
    ws.cell(k, 2, f"Mean Value Across All {n} Forecast Cycles").font = Font(name="Calibri", bold=True)
    for j in range(3, 16):
        if j == 10:
            continue
        L = openpyxl.utils.get_column_letter(j)
        c = ws.cell(k, j, f"=AVERAGE({L}4:{L}{k - 1})")
        c.font = Font(name="Calibri", bold=True)
        c.alignment = Alignment(horizontal="center")
        c.number_format = "0.0000" if j in (4, 11) else "0.000"
    for j in range(1, 17):
        ws.cell(k, j).border = Border(top=MED, bottom=MED)
    ws.conditional_formatting.add(
        f"C4:C{k}", openpyxl.formatting.rule.CellIsRule(operator="greaterThan", formula=["0"],
                                                        font=Font(color="C00000", bold=True)))
    ws.conditional_formatting.add(
        f"C4:C{k}", openpyxl.formatting.rule.CellIsRule(operator="lessThanOrEqual", formula=["0"],
                                                        font=Font(color="006100", bold=True)))
    note = (f"Bias = mean(forecast − observation), ft above MLLW. ΔRMSE = NWS − Tidewatch. NWS forecasts: archived HML "
            f"products (Iowa Environmental Mesonet) with the same issue times as the NWPS hydrographs"
            + (f", shifted −{shift:.3f} ft onto the verifying datum." if shift else "; no datum shift at this gauge.")
            + " Tidewatch Skill (column H) is a live formula of D-G and the observation SD in column I; "
              "NWS Skill (column O) uses the SD of the hourly observations in its window.")
    ws.cell(k + 2, 1, note).font = Font(name="Calibri", italic=True, color="444444")
    widths = [5, 38, 11, 9, 9, 9, 9, 9, 9, 6, 9, 9, 9, 9, 9, 17]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = w
    ws.row_dimensions[3].height = 30
    ws.freeze_panes = "C4"


def main():
    src = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    data = load(src, HmlArchive("input/nws_hml_oct2025"))
    allwb = openpyxl.Workbook()
    allwb.remove(allwb.active)
    rows = []
    for st, d in data.items():
        figure(st, d, range(0, 7), OUT / f"Fig_{st}_Tidewatch_vs_NWS_HML_cycles_1-7.png")
        figure(st, d, range(7, 14), OUT / f"Fig_{st}_Tidewatch_vs_NWS_HML_cycles_8-14.png")
        table_png(st, d, OUT / f"{st}_Tidewatch_vs_NWS_HML_36hr_table.png")
        wb = openpyxl.Workbook()
        table_sheet(wb.active, st, d)
        wb.active.title = st
        wb.save(OUT / f"{st}_Tidewatch_vs_NWS_HML_36hr_table.xlsx")
        table_sheet(allwb.create_sheet(st), st, d)
        for c in d["cyc"]:
            rows.append(dict(station=st, cycle=c["i"], start_edt=c["start"].tz_localize(None), label=c["label"],
                             nws_issued_utc=c["issued"],
                             **{f"tw_{k}": v for k, v in c["s_tw"].items()},
                             **{f"nws36_{k}": v for k, v in c["s36"].items()},
                             **{f"nws72_{k}": v for k, v in c["s72"].items()}))
        print(st, "done")
    allwb.save(OUT / "All_stations_Tidewatch_vs_NWS_HML_36hr_tables.xlsx")
    pd.DataFrame(rows).to_csv(OUT / "paper_figure_stats.csv", index=False, float_format="%.4f")


if __name__ == "__main__":
    main()
