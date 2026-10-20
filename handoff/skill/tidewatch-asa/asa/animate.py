"""Forecast validation animations in the format of Derek's October 2025 set.

Top: observed water level over about a week, the Tidewatch 36-hour forecast
drawn in as a red cursor sweeps the window, NWS NWPS hourly forecast points
(black squares inside the 36-hour Tidewatch window, grey diamonds from 36 to
72 hours). Bottom left: forecast vs observation scatter filling in with the
sweep. Bottom right: R2, Bias, MAE, RMSE and Tidewatch Skill per series.

Frames are drawn with matplotlib blitting and piped to ffmpeg (H.264, 30 fps,
1920x1080), so one animation takes a few seconds to render.
"""
import subprocess
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

FPS = 30
SIZE = (1920, 1080)
HOLD_S = 0.5
DURATION_S = {36: 5.0, 72: 6.0}

OBS_COLOR = "#5B9BD5"
TW_COLOR = "#00B0F0"
NWS_COLOR = "black"
NWS72_COLOR = "#8C8C8C"
CURSOR = "#C00000"
MARKERS = {"●": "o", "■": "s", "◆": "D"}

# Tidal datum reference lines (ft above MLLW), drawn when known. NOAA HAT/LAT
# on the 1983-2001 epoch; SWPT from Derek's Oct 2025 workbook. Add stations
# here as the values are confirmed.
TIDAL_DATUMS = {"SWPT": {"HAT83": 3.70, "LAT83": -0.87}}

_FONT = "Carlito" if any(f.name == "Carlito" for f in font_manager.fontManager.ttflist) else "DejaVu Sans"


def cycle_label(t):
    """'Sep. 25 Thursday 5PM' (UTC), as on Derek's figures."""
    h = t.hour % 12 or 12
    return f"{t:%b}. {t.day:02d} {t:%A} {h}{'AM' if t.hour < 12 else 'PM'}"


def file_label(t):
    h = t.hour % 12 or 12
    return f"{t:%Y%b%d}_{t:%A}_{h}{'AM' if t.hour < 12 else 'PM'}"


def _fmt(v):
    if v is None or pd.isna(v):
        return "–"
    return f"{v:.3f}".replace("-", "−")


def render(spec, path):
    """spec: dict built by validation.build_spec(); path: output .mp4."""
    plt.rcParams.update({"font.family": _FONT, "font.size": 15})
    H = spec["horizon"]
    t0, t36, t72 = spec["t0"], spec["t0"] + pd.Timedelta(hours=36), spec["t0"] + pd.Timedelta(hours=72)
    tend = t36 if H == 36 else t72
    xlo, xhi = (t0 - pd.Timedelta(days=2)).normalize(), t0 + pd.Timedelta(days=3.5 if H == 36 else 5)
    # Start at midnight so the day ticks never widen the axis past the observations.

    tw = spec["tw"]                       # Series: px30 at 6-min
    twp = spec["tw_pairs"]                # DataFrame predicted/observed at 6-min
    nws = spec.get("nws")                 # Series: shifted NWS hourly, t0..t72 (or None)
    nwp = spec.get("nws_pairs")           # DataFrame predicted/observed, hourly
    obs = spec["obs"].loc[xlo:xhi]

    def num(idx):
        return matplotlib.dates.date2num(idx.tz_convert(None).to_pydatetime())

    fig = plt.figure(figsize=(SIZE[0] / 100, SIZE[1] / 100), dpi=100, facecolor="white")
    fig.text(0.045, 0.945, spec["title"], fontsize=30)
    fig.text(0.045, 0.905, spec["subtitle"], fontsize=17, color="#555555")

    ax = fig.add_axes([0.045, 0.545, 0.935, 0.33])
    ax.plot(num(obs.index), obs.values, color=OBS_COLOR, lw=3.2, zorder=2)
    tw_color = spec.get("tw_color", TW_COLOR)
    for t in (t0, t36):
        ax.axvline(matplotlib.dates.date2num(t.tz_convert(None)), color="black", ls=(0, (5, 4)), lw=2, zorder=1)
    for name, v in TIDAL_DATUMS.get(spec["station"], {}).items():
        ax.axhline(v, color="#555555", ls=(0, (7, 4)), lw=1.3, zorder=1)
        ax.text(matplotlib.dates.date2num(xlo.tz_convert(None)) + 0.05, v + 0.07, name, fontsize=13, color="#333333")
    ax.set_xlim(num(pd.DatetimeIndex([xlo, xhi])))
    vals = [obs.values, tw.values] + ([nws.values] if nws is not None else [])
    allv = np.concatenate([v[np.isfinite(v)] for v in vals if len(v)])
    lo, hi = np.floor(allv.min() - 0.3), np.ceil(allv.max() + 0.6)
    for v in TIDAL_DATUMS.get(spec["station"], {}).values():
        lo, hi = min(lo, np.floor(v - 0.4)), max(hi, np.ceil(v + 0.4))
    ax.set_ylim(lo, hi)
    days = pd.date_range(xlo.normalize(), xhi, freq="D")
    ax.set_xticks(num(days))
    same_month = xlo.month == xhi.month
    ax.set_xticklabels([str(d.day) if same_month else f"{d.month}/{d.day}" for d in days])
    ax.set_xlabel(f"Day in {t0:%B %Y} (UTC)" if same_month else "Date (UTC)", fontsize=16)
    ax.set_ylabel("Water Level (ft. above MLLW)", fontsize=16)
    ax.grid(color="#DDDDDD", lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    # Scatter: x forecast, y observation, as on Derek's figures.
    sx = fig.add_axes([0.045, 0.07, 0.305, 0.385])
    pts = [twp["predicted"], twp["observed"]] + ([nwp["predicted"], nwp["observed"]] if nwp is not None else [])
    pv = np.concatenate([p.to_numpy(float) for p in pts])
    a, b = np.floor(np.nanmin(pv) - 0.5), np.ceil(np.nanmax(pv) + 0.5)
    sx.plot([a, b], [a, b], color="#BBBBBB", ls="--", lw=1.2)
    sx.set_xlim(a, b)
    sx.set_ylim(a, b)
    sx.set_aspect("equal", adjustable="box")
    sx.set_xlabel("Forecast Prediction (ft. above MLLW)", fontsize=15)
    sx.set_ylabel(f"{spec['obs_label']} (ft. above MLLW)", fontsize=15)
    sx.grid(False)

    # Statistics panel.
    X = 0.395
    fig.text(X, 0.425, spec.get("heading") or f"{cycle_label(t0)} 36-hr Forecast", fontsize=24)
    m, c = spec["fit"]
    fig.text(X, 0.375, f"f(x) = {m:.4f} x {'+' if c >= 0 else '−'} {abs(c):.4f}   (Tidewatch)", fontsize=17, color="#333333")
    cols = [0.635, 0.71, 0.78, 0.85, 0.92]
    for x, h in zip(cols, ["R²", "Bias", "MAE", "RMSE", "Skill"]):
        fig.text(x, 0.315, h, fontsize=17, fontweight="bold", ha="center")
    y = 0.255
    for row in spec["rows"]:
        fig.add_artist(Line2D([X + 0.008], [y], transform=fig.transFigure, ls="", marker=MARKERS[row["glyph"]],
                              ms=11 if row["glyph"] == "●" else 9, color=row["color"]))
        if row.get("glyph2"):
            fig.add_artist(Line2D([X + 0.024], [y], transform=fig.transFigure, ls="", marker=MARKERS[row["glyph2"]],
                                  ms=9, color=NWS72_COLOR))
        fig.text(X + 0.04, y, row["label"], fontsize=17, va="center")
        s = row["stats"]
        for x, v in zip(cols, [s.get("R2"), s.get("Bias"), s.get("MAE"), s.get("RMSE"), s.get("Skill")]):
            fig.text(x, y, (f"{v:.4f}" if x == cols[0] and pd.notna(v) else _fmt(v)), fontsize=17,
                     va="center", ha="center")
        y -= 0.05
    notes = [w for note in spec["notes"] for w in textwrap.wrap(note, 150)]
    ny = 0.105
    for note in notes:
        fig.text(X, ny, note, fontsize=12.5, style="italic", color="#444444")
        ny -= 0.026

    # Animated artists.
    tw_line, = ax.plot([], [], color=tw_color, lw=5, zorder=3, solid_capstyle="round")
    n36, = ax.plot([], [], ls="", marker="s", ms=7, color=NWS_COLOR, zorder=4)
    n72, = ax.plot([], [], ls="", marker="D", ms=7, color=NWS72_COLOR, zorder=4)
    cur = ax.axvline(0, color=CURSOR, lw=1.8, zorder=5)
    ring, = ax.plot([], [], ls="", marker="o", ms=15, mfc="none", mec=CURSOR, mew=2.2, zorder=6)
    s_tw, = sx.plot([], [], ls="", marker="o", ms=7, color=tw_color, zorder=3)
    s36, = sx.plot([], [], ls="", marker="s", ms=7, color=NWS_COLOR, zorder=4)
    s72, = sx.plot([], [], ls="", marker="D", ms=7, color=NWS72_COLOR, zorder=4)
    fitl, = sx.plot([], [], color="black", lw=2.2, zorder=5)
    sring, = sx.plot([], [], ls="", marker="o", ms=15, mfc="none", mec=CURSOR, mew=2.2, zorder=6)
    top = [tw_line, n36, n72, cur, ring]
    bot = [s_tw, s36, s72, fitl, sring]
    for art in top + bot:
        art.set_animated(True)

    fig.canvas.draw()
    bg = fig.canvas.copy_from_bbox(fig.bbox)

    tw_x = num(tw.index)
    tw_pair_t = twp.index
    if nws is not None:
        nws_x = num(nws.index)
        in36 = nws.index <= t36
        nwp_t = nwp.index
        nwp_in36 = nwp_t <= t36

    n_frames = int(round(DURATION_S[H] * FPS))
    hold = int(round(HOLD_S * FPS))
    sweep = n_frames - 2 * hold

    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{SIZE[0]}x{SIZE[1]}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n_frames):
            k = min(max(i - hold, 0), sweep)
            tau = t0 + (tend - t0) * (k / sweep)
            tau_n = matplotlib.dates.date2num(tau.tz_convert(None))
            sel = tw.index <= tau
            tw_line.set_data(tw_x[sel], tw.values[sel])
            if sel.any():
                ring.set_data([tw_x[sel][-1]], [tw.values[sel][-1]])
            cur.set_xdata([tau_n, tau_n])
            ps = tw_pair_t <= tau
            s_tw.set_data(twp["predicted"].values[ps], twp["observed"].values[ps])
            if ps.sum() >= 2:
                xs = twp["predicted"].values[ps]
                fitl.set_data([xs.min(), xs.max()], [m * xs.min() + c, m * xs.max() + c])
                sring.set_data([xs[-1]], [twp["observed"].values[ps][-1]])
            if nws is not None:
                ns = nws.index <= tau
                n36.set_data(nws_x[ns & in36], nws.values[ns & in36])
                n72.set_data(nws_x[ns & ~in36], nws.values[ns & ~in36])
                qs = nwp_t <= tau
                s36.set_data(nwp["predicted"].values[qs & nwp_in36], nwp["observed"].values[qs & nwp_in36])
                s72.set_data(nwp["predicted"].values[qs & ~nwp_in36], nwp["observed"].values[qs & ~nwp_in36])
            fig.canvas.restore_region(bg)
            for art in top:
                ax.draw_artist(art)
            for art in bot:
                sx.draw_artist(art)
            proc.stdin.write(bytes(fig.canvas.buffer_rgba()))
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed for {path}")
    finally:
        if proc.poll() is None:
            proc.kill()
        plt.close(fig)
    return path

