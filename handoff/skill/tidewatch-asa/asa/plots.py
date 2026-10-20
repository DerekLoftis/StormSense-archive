"""Figures for the ASA: single-forecast plots and overlapping validation plots."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

OBS_COLOR = "#1a1a1a"
FCST_COLOR = "#1f6fd1"
ASTRO_COLOR = "#8c8c8c"
FIT_COLOR = "#d62728"
ORIGIN_FIT_COLOR = "#ff7f0e"

plt.rcParams.update({
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": "#e3e3e3",
    "grid.linewidth": 0.6,
})


def _fmt_time_axis(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H:%MZ"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))


def _stat_line(s, skill=None, site_skill=None):
    parts = [f"R² = {s['R2']:.3f}", f"Bias = {s['Bias']:+.3f} ft",
             f"MAE = {s['MAE']:.3f} ft", f"RMSE = {s['RMSE']:.3f} ft"]
    if skill is not None and pd.notna(skill):
        parts.append(f"Tidewatch Skill = {skill:.3f}")
    if site_skill is not None and pd.notna(site_skill):
        parts.append(f"Site Skill = {site_skill:.3f}")
    return parts


def timeseries(paired, forecast, observed, title, path, datum="MLLW"):
    """Product 1: Tidewatch px30 vs observed water level for one 36-h forecast."""
    fig, ax = plt.subplots(figsize=(8, 4.4), dpi=150)
    f = forecast.data
    pad = pd.Timedelta(hours=6)
    if observed is not None and not observed.empty:
        o = observed.loc[forecast.start - pad:forecast.end]
        ax.plot(o.index, o.values, color=OBS_COLOR, lw=2, label="Observed (Tidewatch 3d archive)")
    if "pa30" in f:
        ax.plot(f["time"], f["pa30"], color=ASTRO_COLOR, lw=1.2, ls="--", label="Astronomical tide (pa30)")
    ax.plot(f["time"], f["px30"], color=FCST_COLOR, lw=2, label="Tidewatch forecast (px30)")
    ax.axvline(forecast.start, color="#555555", lw=0.8, ls=":")
    ax.set_ylabel(f"Water level (ft {datum})")
    ax.set_title(title, fontsize=11, loc="left")
    _fmt_time_axis(ax)
    ax.legend(loc="upper left", fontsize=8, frameon=True, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def regression(paired, stats, skill, site_skill, title, path, datum="MLLW"):
    """Product 2: predicted vs observed with both best-fit lines and stats legend."""
    fig, ax = plt.subplots(figsize=(5.6, 5.2), dpi=150)
    x = paired["observed"].to_numpy(float)
    y = paired["predicted"].to_numpy(float)
    lo = min(x.min(), y.min())
    hi = max(x.max(), y.max())
    span = hi - lo
    lo, hi = lo - 0.05 * span, hi + 0.05 * span
    line_x = np.array([lo, hi])
    ax.scatter(x, y, s=10, color=FCST_COLOR, alpha=0.55, edgecolors="none")
    ax.plot(line_x, line_x, color="#9a9a9a", lw=1, ls=":")
    ax.plot(line_x, stats["Slope"] * line_x + stats["Intercept"], color=FIT_COLOR, lw=1.8)
    ax.plot(line_x, stats["SlopeOrigin"] * line_x, color=ORIGIN_FIT_COLOR, lw=1.6, ls="--")
    handles = [
        Line2D([], [], marker="o", ls="", color=FCST_COLOR, alpha=0.7,
               label=f"6-min points (n = {stats['N']})"),
        Line2D([], [], color=FIT_COLOR, lw=1.8,
               label=f"Best fit: y = {stats['Slope']:.3f}x {stats['Intercept']:+.3f}"),
        Line2D([], [], color=ORIGIN_FIT_COLOR, lw=1.6, ls="--",
               label=f"Fit through origin: y = {stats['SlopeOrigin']:.3f}x"),
        Line2D([], [], color="#9a9a9a", lw=1, ls=":", label="1:1"),
    ] + [Line2D([], [], ls="", label=t) for t in _stat_line(stats, skill, site_skill)]
    ax.legend(handles=handles, loc="upper left", fontsize=7.5, frameon=True,
              framealpha=0.92, handlelength=1.6)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal")
    ax.set_xlabel(f"Observed (ft {datum})")
    ax.set_ylabel(f"Tidewatch px30 (ft {datum})")
    ax.set_title(title, fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _issue_colors(items, window_start, window_end):
    """Rainbow color for each forecast from its issuance time in the window.

    Derek asked for the 6-hourly forecasts in a rainbow sequence; turbo is the
    perceptually ordered rainbow (oldest violet, newest red). Color follows
    the issuance time, so a missed cycle leaves a visible gap in the sequence.
    """
    cmap = plt.get_cmap("turbo")
    span = (window_end - window_start).total_seconds()
    return [cmap(0.05 + 0.9 * max(0.0, min(1.0, (it["forecast"].issued - window_start).total_seconds() / span)))
            for it in items]


def _issue_colorbar(fig, cax_or_ax, window_start, window_end, **kw):
    sm = plt.cm.ScalarMappable(cmap="turbo", norm=plt.Normalize(0, 1))
    cb = fig.colorbar(sm, **({"cax": cax_or_ax} if kw.pop("is_cax", False) else {"ax": cax_or_ax}), **kw)
    span = window_end - window_start
    ticks = [0.05 + 0.9 * f for f in (0, 0.25, 0.5, 0.75, 1)]
    cb.set_ticks(ticks)
    cb.set_ticklabels([(window_start + span * f).strftime("%m/%d %H:%MZ") for f in (0, 0.25, 0.5, 0.75, 1)])
    cb.set_label("Forecast issuance")
    return cb


def _table_rows(rows):
    return [[r["Issued"], r["Period"], f"{r['Coverage']:.0%}", f"{r['R2']:.3f}",
             f"{r['Bias']:+.3f}", f"{r['MAE']:.3f}", f"{r['RMSE']:.3f}",
             f"{r['Skill']:.3f}", f"{r['SiteSkill']:.3f}" if pd.notna(r["SiteSkill"]) else "–"]
            for r in rows]


TABLE_COLS = ["Issued (UTC)", "Forecast period (UTC)", "Cover", "R²", "Bias (ft)",
              "MAE (ft)", "RMSE (ft)", "TW Skill", "Site Skill"]


def overlap_timeseries(items, observed, window_start, window_end, title, path, datum="MLLW"):
    """Goal 3: every 36-h forecast in the window overlaid on the observations.

    items: list of dicts with keys forecast, stats, row (table row dict).
    """
    n = len(items)
    issue_end = window_end - pd.Timedelta(hours=36)
    colors = _issue_colors(items, window_start, issue_end)
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=150)
    if observed is not None and not observed.empty:
        o = observed.loc[window_start:window_end]
        ax.plot(o.index, o.values, color=OBS_COLOR, lw=2.4, label="Observed", zorder=5)
    for it, c in zip(items, colors):
        f = it["forecast"].data
        s = it["row"]
        label = (f"{s['Issued']}  R² {s['R2']:.2f}  Bias {s['Bias']:+.2f}  MAE {s['MAE']:.2f}  "
                 f"RMSE {s['RMSE']:.2f}  Skill {s['Skill']:.3f}")
        ax.plot(f["time"], f["px30"], color=c, lw=1.3, alpha=0.9, label=label if n <= 16 else None)
    if n > 16:
        _issue_colorbar(fig, ax, window_start, issue_end, pad=0.01, fraction=0.03)
        agg = pd.DataFrame([it["row"] for it in items])
        ax.text(0.01, 0.98,
                f"{n} forecasts   mean R² {agg['R2'].mean():.3f}   mean Bias {agg['Bias'].mean():+.3f} ft   "
                f"mean MAE {agg['MAE'].mean():.3f} ft   mean RMSE {agg['RMSE'].mean():.3f} ft   "
                f"mean Skill {agg['Skill'].mean():.3f}",
                transform=ax.transAxes, va="top", fontsize=8,
                bbox=dict(facecolor="white", edgecolor="#cccccc", alpha=0.9))
        ax.legend(loc="lower left", fontsize=8)
    else:
        ax.legend(loc="upper left", fontsize=6.8, frameon=True, framealpha=0.9,
                  bbox_to_anchor=(1.005, 1.0), borderaxespad=0)
    ax.set_xlim(window_start, window_end)
    ax.set_ylabel(f"Water level (ft {datum})")
    ax.set_title(title, fontsize=11, loc="left")
    _fmt_time_axis(ax)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def overlap_regression(items, window_start, window_end, title, path, datum="MLLW"):
    """Goal 3: regression of every forecast in the window, with a stats table.

    window_start / window_end bound the issuance times (for the colors).
    """
    n = len(items)
    colors = _issue_colors(items, window_start, window_end)
    rows = [it["row"] for it in items]
    row_h = 0.24
    table_h = row_h * (n + 1)
    plot_h = 6.0
    fig = plt.figure(figsize=(10.5, plot_h + table_h + 1.0), dpi=150)
    gs = fig.add_gridspec(2, 2, height_ratios=[plot_h, table_h], width_ratios=[30, 1],
                          hspace=0.75 / plot_h * 1.6, wspace=0.03, left=0.07, right=0.88, top=0.97, bottom=0.01)
    ax = fig.add_subplot(gs[0, 0])
    cax = fig.add_subplot(gs[0, 1])
    tax = fig.add_subplot(gs[1, :])
    allx, ally = [], []
    for it, c in zip(items, colors):
        p = it["paired"]
        ax.scatter(p["observed"], p["predicted"], s=6, color=c, alpha=0.5, edgecolors="none")
        allx.append(p["observed"].to_numpy(float))
        ally.append(p["predicted"].to_numpy(float))
    x = np.concatenate(allx)
    y = np.concatenate(ally)
    lo, hi = min(x.min(), y.min()), max(x.max(), y.max())
    span = hi - lo
    lo, hi = lo - 0.05 * span, hi + 0.05 * span
    lx = np.array([lo, hi])
    m, b = np.polyfit(x, y, 1)
    m0 = np.sum(x * y) / np.sum(x * x)
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    err = y - x
    ax.plot(lx, lx, color="#9a9a9a", lw=1, ls=":")
    ax.plot(lx, m * lx + b, color=FIT_COLOR, lw=1.8)
    ax.plot(lx, m0 * lx, color=ORIGIN_FIT_COLOR, lw=1.6, ls="--")
    handles = [
        Line2D([], [], marker="o", ls="", color="#777777", label=f"{n} forecasts, {len(x)} points"),
        Line2D([], [], color=FIT_COLOR, lw=1.8, label=f"Best fit: y = {m:.3f}x {b:+.3f}"),
        Line2D([], [], color=ORIGIN_FIT_COLOR, lw=1.6, ls="--", label=f"Fit through origin: y = {m0:.3f}x"),
        Line2D([], [], color="#9a9a9a", lw=1, ls=":", label="1:1"),
        Line2D([], [], ls="", label=f"Pooled R² = {r2:.3f}"),
        Line2D([], [], ls="", label=f"Pooled Bias = {err.mean():+.3f} ft"),
        Line2D([], [], ls="", label=f"Pooled MAE = {np.abs(err).mean():.3f} ft"),
        Line2D([], [], ls="", label=f"Pooled RMSE = {np.sqrt((err**2).mean()):.3f} ft"),
        Line2D([], [], ls="", label=f"Mean Tidewatch Skill = {np.nanmean([r['Skill'] for r in rows]):.3f}"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=7.5, framealpha=0.92)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel(f"Observed (ft {datum})")
    ax.set_ylabel(f"Tidewatch px30 (ft {datum})")
    ax.set_title(title, fontsize=11, loc="left")
    _issue_colorbar(fig, cax, window_start, window_end, is_cax=True)
    tax.axis("off")
    widths = [0.12, 0.22, 0.07, 0.08, 0.09, 0.09, 0.09, 0.08, 0.08]
    tbl = tax.table(cellText=_table_rows(rows), colLabels=TABLE_COLS, loc="upper center",
                    cellLoc="center", colLoc="center", colWidths=widths,
                    bbox=[0, 0, 1, 1])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(7.5)
    for (r, ccol), cell in tbl.get_celld().items():
        cell.set_edgecolor("#d0d0d0")
        if r == 0:
            cell.set_facecolor("#eef2f7")
            cell.set_text_props(weight="bold")
        elif ccol == 0:
            cell.set_facecolor(colors[r - 1])
            rgb = np.array(colors[r - 1][:3])
            cell.get_text().set_color("white" if rgb @ [0.299, 0.587, 0.114] < 0.55 else "black")
    fig.savefig(path)
    plt.close(fig)
