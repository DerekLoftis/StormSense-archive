"""Monthly forecast-validation deck with embedded autoplay animations.

Layout follows Derek's Oct 2025 SWPT deck: for each station, a full table of
the month's 36-hour forecasts, then one animation per forecast (colored in
sequence red, orange, yellow, green, blue, indigo, violet, then red again),
then one composite animation that sweeps the month and labels every forecast
with its issuance time and Tidewatch Skill.
"""
import copy
import logging
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from . import animate, config, data, nws, validation
from .data import station_name
from .stations import STATIONS

log = logging.getLogger("asa.monthly_deck")

# Forecast sequence colors (ROYGBIV). Observations stay light blue underneath.
SEQ = [("#E41A1C", "red"), ("#FF8C00", "orange"), ("#F2C200", "yellow"), ("#2CA02C", "green"),
       ("#0B3FE0", "blue"), ("#4B0082", "indigo"), ("#9B30D9", "violet")]
DARK_FILL = {"#E41A1C", "#2CA02C", "#0B3FE0", "#4B0082", "#9B30D9"}
OBS_LIGHT = "#7FB0DE"
COMPOSITE_S = 16.0


def seq_color(i):
    return SEQ[i % len(SEQ)][0]


def station_order(ids):
    known = [s["tidewatch_id"] for s in STATIONS]
    return [s for s in known if s in ids] + sorted(s for s in ids if s not in known)


def obs_label(st):
    src = data.STATION_INFO.get(st, {}).get("source")
    return {"NOAA": "NOAA Observation", "USGS": "USGS Observation"}.get(src, "Observation")


# --------------------------------------------------------------------------
# Station table
# --------------------------------------------------------------------------
def table_png(station, recs, path, month_label):
    """Month table in the layout of Derek's Oct 2025 SWPT table (NWS columns when available)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": animate._FONT})
    has_nws = any(r["nws"] for r in recs)
    n = len(recs)
    fig = plt.figure(figsize=(27.72, 3.6 + 0.5 * n), dpi=100)
    ax = fig.add_axes([0.005, 0.0, 0.99, 1.0])
    ax.set_axis_off()
    H = 2.6 + n + 2.2
    ax.set_xlim(0, 100)
    ax.set_ylim(H, 0)
    fs = 15
    if has_nws:
        xs = {"#": 2.0, "start": 3.6, "d": 31.5, "tR2": 38.5, "tB": 45, "tM": 51.5, "tR": 58, "tS": 64.5,
              "nR2": 72, "nB": 78.5, "nM": 85, "nR": 91.5, "nS": 97.5}
        groups = [(51.5, 35.5, 67.5, "VIMS Tidewatch  (36-hr window, 6-min)"),
                  (85, 69, 99.8, "NWS NWPS  (same 36-hr window, hourly)")]
    else:
        xs = {"#": 2.0, "start": 3.6, "n": 37, "tR2": 46, "tB": 55, "tM": 64, "tR": 73, "SD": 82, "tS": 92}
        groups = [(64.5, 33, 99.5, "VIMS Tidewatch  (36-hr window, 6-min)")]
    ax.plot([0.2, 99.8], [0.15, 0.15], color="black", lw=1.5)
    for xc, x0, x1, label in groups:
        ax.text(xc, 0.75, label, ha="center", va="center", fontsize=fs, fontweight="bold")
        ax.plot([x0, x1], [1.25, 1.25], color="black", lw=1)
    hdr = {"#": "#", "start": "Forecast Issued (UTC)", "d": "ΔRMSE", "n": "n", "tR2": "R²", "tB": "Bias",
           "tM": "MAE", "tR": "RMSE", "SD": "SD obs", "tS": "TW Skill", "nR2": "R²", "nB": "Bias", "nM": "MAE",
           "nR": "RMSE", "nS": "Skill"}
    for k in xs:
        ax.text(xs[k] + (12.5 if k == "start" else 0), 1.8, hdr[k], ha="center", va="center", fontsize=fs,
                fontweight="bold")
    ax.plot([0.2, 99.8], [2.35, 2.35], color="black", lw=1.5)

    def f3(v, nd=3):
        return "–" if v is None or pd.isna(v) else f"{v:.{nd}f}".replace("-", "−")

    cols = {k: [] for k in xs if k not in ("#", "start", "n")}
    for i, r in enumerate(recs):
        y = 2.6 + i + 0.5
        c = seq_color(i)
        t = r["tw_stats"]
        s = (r["nws"] or {}).get("s36") or {}
        d = s.get("RMSE", np.nan) - t.get("RMSE", np.nan)
        ax.add_patch(matplotlib.patches.Rectangle((xs["start"], y - 0.45), 25, 0.9, facecolor=c,
                                                  edgecolor="#999999", lw=0.8))
        ax.text(xs["#"], y, str(i + 1), ha="center", va="center", fontsize=fs)
        ax.text(xs["start"] + 0.6, y, f"{animate.cycle_label(r['issued'])} 36-hr Forecast", va="center",
                fontsize=fs, color="white" if c in DARK_FILL else "black")
        vals = {"d": d, "n": t.get("N"), "tR2": t.get("R2"), "tB": t.get("Bias"), "tM": t.get("MAE"),
                "tR": t.get("RMSE"), "SD": t.get("SDobs"), "tS": t.get("Skill"), "nR2": s.get("R2"),
                "nB": s.get("Bias"), "nM": s.get("MAE"), "nR": s.get("RMSE"), "nS": s.get("Skill")}
        for k in xs:
            if k in ("#", "start"):
                continue
            v = vals[k]
            txt = str(v) if k == "n" else f3(v, 4 if k.endswith("R2") else 3)
            color = ("#C00000" if (pd.notna(v) and v > 0) else "#006100") if k == "d" else "black"
            ax.text(xs[k], y, txt, ha="center", va="center", fontsize=fs, color=color,
                    fontweight="bold" if k in ("d", "tS") else "normal")
            if k in cols:
                cols[k].append(v)
    y = 2.6 + n + 0.6
    ax.plot([0.2, 99.8], [y - 0.55, y - 0.55], color="black", lw=1.5)
    ax.text(xs["start"] + 12.5, y, f"Mean Value Across All {n} Forecast Cycles", ha="center", va="center",
            fontsize=fs, fontweight="bold")
    for k, v in cols.items():
        mv = float(np.nanmean(np.asarray(v, float))) if len(v) and np.isfinite(np.asarray(v, float)).any() else np.nan
        color = ("#C00000" if (pd.notna(mv) and mv > 0) else "#006100") if k == "d" else "black"
        ax.text(xs[k], y, f3(mv, 4 if k.endswith("R2") else 3), ha="center", va="center", fontsize=fs,
                fontweight="bold", color=color)
    ax.plot([0.2, 99.8], [y + 0.55, y + 0.55], color="black", lw=1.5)
    lid = recs[0]["lid"]
    shifts = sorted({r["nws"]["shift"] for r in recs if r["nws"]})
    nws_txt = ""
    if has_nws:
        nws_txt = (f" NWS {lid} forecasts from the IEM archive of issued HML products; ΔRMSE = NWS − Tidewatch; "
                   + ("no datum shift required at this gauge." if shifts == [0.0] else
                      "NWS datum shift " + ", ".join(f"{-v:+.3f}" for v in shifts) + " ft applied."))
    src = ("NWS NWPS observations for the real gauge" if any(r["obs_source"] == "nwps" for r in recs)
           else "NOAA CO-OPS 6-min data" if any(r["obs_source"] == "noaa" for r in recs)
           else "the Tidewatch 3-day / 30-day hindcast")
    note = (f"{station_name(station)} (VIMS Tidewatch {station}), {month_label}. Bias = mean(forecast − observation), "
            f"ft above MLLW. Observations from {src}. TW Skill = 0.25[R² + e^(−|Bias|/σ) + e^(−MAE/σ) + e^(−RMSE/σ)]."
            + nws_txt)
    import textwrap
    yy = y + 1.0
    for line in textwrap.wrap(note, 260):
        ax.text(0.2, yy, line, fontsize=12.5, style="italic", color="#444444", va="center")
        yy += 0.45
    if station in ALIASED:
        ax.text(0.2, yy, "Caution: " + COPY_OF.get(station, f"Tidewatch publishes the same observation series for "
                f"{station} as for {ALIASED[station]} (output/aliasing/ALIASING.md)."),
                fontsize=12.5, style="italic", color="#C00000", va="center")
    fig.savefig(path, dpi=100, facecolor="white")
    plt.close(fig)


# Stations whose published observations are copies of another station's
# series (output/aliasing/ALIASING.md, 7 Oct 2026).
_GROUPS = [("DAHL", "USNC", "USSC"), ("WADC", "USAR", "USBK"), ("USFM", "USCB"), ("USSF", "USNR"),
           ("USHR", "USSB"), ("GS01", "GS02")]
ALIASED = {s: ", ".join(x for x in g if x != s) for g in _GROUPS for s in g}
# Copies whose source is confirmed against NWS HML observations (exact match).
COPY_OF = {
    "DAHL": "Tidewatch's DAHL tide prediction and observations are New Market Creek's (USNC); "
            "validated here against the real Dahlgren gauge (NWS NCDV2).",
    "USSC": "Tidewatch's USSC observations are New Market Creek's (USNC, NWS NMCV2).",
    "USCB": "Tidewatch's USCB observations are Fort Monroe's (USFM, NWS FTMV2).",
    "USNR": "Tidewatch's USNR observations are Nansemond River at Suffolk's (USSF, NWS NMDV2).",
}


# --------------------------------------------------------------------------
# Composite animation
# --------------------------------------------------------------------------
def _sweep_times(windows, lo, hi, n_frames, gap_weight=0.15):
    """Cursor times that linger on forecast windows and hurry through gaps."""
    grid = pd.date_range(lo, hi, freq="6min")
    w = np.full(len(grid), gap_weight)
    for a, b in windows:
        w[(grid >= a) & (grid <= b)] = 1.0
    cum = np.concatenate([[0], np.cumsum(w[1:])])
    targets = np.linspace(0, cum[-1], n_frames)
    idx = np.searchsorted(cum, targets).clip(0, len(grid) - 1)
    return grid[idx]


def render_composite(spec, path):
    """All forecasts on one axis. Each 36-h forecast gets its own bar in its sequence color: the bar fades in,
    wipes right across that forecast's window, then recedes down to the forecast's label, leaving a faded dashed
    callout from where the forecast ends to its label. The next bar starts 2/3 of the way through the prior wipe."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": animate._FONT, "font.size": 15})
    items = spec["items"]          # list of {series, issued, skill, color}
    obs = spec["obs"]
    lo = min(it["series"].index.min() for it in items) - pd.Timedelta(days=1)
    hi = max(it["series"].index.max() for it in items) + pd.Timedelta(days=1)
    obs = obs.loc[lo:hi]

    def num(idx):
        return mdates.date2num(pd.DatetimeIndex(idx).tz_convert(None).to_pydatetime())

    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor="white")
    fig.text(0.045, 0.945, spec["title"], fontsize=30)
    fig.text(0.045, 0.905, spec["subtitle"], fontsize=17, color="#555555")
    ax = fig.add_axes([0.045, 0.085, 0.935, 0.79])
    ax.plot(num(obs.index), obs.values, color=OBS_LIGHT, lw=1.8, zorder=1)

    # Labels: centered under each forecast, pushed down where they would collide.
    span_days = (hi - lo) / pd.Timedelta(days=1)
    width = span_days * 0.08
    vals = np.concatenate([obs.values] + [it["series"].values for it in items])
    ymin, ymax = np.nanmin(vals), np.nanmax(vals)
    approx_range = (ymax - ymin) * 1.45
    label_h = approx_range * 50 / 853          # two 15-pt lines in data units
    placed, labels = [], []
    for it in items:
        s = it["series"]
        mid = s.index[0] + (s.index[-1] - s.index[0]) / 2
        xm = mdates.date2num(mid.tz_convert(None))
        local = obs.loc[s.index[0]:s.index[-1]]
        top = min(s.min(), local.min() if len(local) else s.min()) - label_h * 0.25
        while any(abs(xm - px) < width and top > pb and top - label_h < pt for px, pt, pb in placed):
            top -= label_h * 0.5
        placed.append((xm, top, top - label_h))
        labels.append((xm, top))
    low = min(p[2] for p in placed) if placed else ymin
    ylo, yhi = min(ymin, low) - label_h * 0.2, ymax + (ymax - ymin) * 0.04
    ax.set_ylim(ylo, yhi)
    ax.set_xlim(num([lo, hi]))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2 if span_days > 12 else 1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.set_ylabel("Water Level (ft. above MLLW)", fontsize=16)
    ax.set_xlabel("Date (UTC)", fontsize=16)
    ax.grid(color="#E3E3E3", lw=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    xt = ax.get_xaxis_transform()              # x in data, y in axes fraction
    lines, texts, bars, vleg, hleg = [], [], [], [], []
    for it, (xm, y) in zip(items, labels):
        c = it["color"]
        ln, = ax.plot([], [], color=c, lw=3.6, zorder=3, solid_capstyle="round")
        # Black text, a 1-px white halo next to it, then a further 2-px halo in the sequence color
        # (stroke widths are full widths in points at 100 dpi: 6 px = 4.32 pt, 2 px = 1.44 pt).
        tx = ax.text(xm, y, f"{it['issued']:%b %d %H:%MZ}\nTWskill {it['skill']:.3f}", ha="center", va="top",
                     fontsize=15, color="black", zorder=7, linespacing=1.1, fontweight="semibold",
                     path_effects=[pe.Stroke(linewidth=4.32, foreground=c), pe.Stroke(linewidth=1.44, foreground="white"),
                                   pe.Normal()])
        bar, = ax.plot([], [], color=c, lw=2.6, zorder=5, transform=xt, solid_capstyle="butt")
        dv, = ax.plot([], [], color=c, lw=1.7, ls=(0, (4, 3)), alpha=0.6, zorder=4)
        dh, = ax.plot([], [], color=c, lw=1.7, ls=(0, (4, 3)), alpha=0.6, zorder=4)
        for a in (ln, tx, bar, dv, dh):
            a.set_animated(True)
        lines.append(ln)
        texts.append(tx)
        bars.append(bar)
        vleg.append(dv)
        hleg.append(dh)

    fig.canvas.draw()
    bg = fig.canvas.copy_from_bbox(fig.bbox)
    inv = ax.transData.inverted()
    boxes = [tx.get_window_extent(renderer=fig.canvas.get_renderer()).transformed(inv) for tx in texts]

    fps = animate.FPS
    n = len(items)
    wipe = int(round(fps * min(1.8, 36.0 / (1 + (n - 1) * 2 / 3))))   # frames per 36-h wipe
    fade, drop, leg = int(0.25 * fps), int(0.6 * fps), int(0.3 * fps)
    step = max(1, int(round(wipe * 2 / 3)))
    hold0, hold1 = int(0.5 * fps), int(2.5 * fps)
    starts = [hold0 + i * step for i in range(n)]
    total = starts[-1] + wipe + drop + leg + hold1
    xs = [num(it["series"].index) for it in items]
    geo = []
    for it, x, b in zip(items, xs, boxes):
        xe, ye = x[-1], float(it["series"].values[-1])
        ymid = (b.y0 + b.y1) / 2
        right_of = xe > b.x1                      # label sits left of the window end: elbow over to its edge
        ystop = ymid if right_of else b.y1
        geo.append((xe, ye, ystop, b.x1 if right_of else xe))

    def frac(y):
        return (y - ylo) / (yhi - ylo)

    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "1920x1080",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for k in range(total):
            fig.canvas.restore_region(bg)
            for i, it in enumerate(items):
                f = k - starts[i]
                if f < 0:
                    continue
                ln, tx, bar, dv, dh = lines[i], texts[i], bars[i], vleg[i], hleg[i]
                x, yv = xs[i], it["series"].values
                xe, ye, ystop, xlab = geo[i]
                if f < wipe:                                   # fade in and wipe right
                    p = (f + 1) / wipe
                    sel = slice(0, max(1, int(np.ceil(p * len(x)))))
                    ln.set_data(x[sel], yv[sel])
                    ax.draw_artist(ln)
                    xb = x[0] + p * (x[-1] - x[0])
                    bar.set_data([xb, xb], [0, 1])
                    bar.set_alpha(min(1.0, (f + 1) / fade))
                    ax.draw_artist(bar)
                    continue
                ln.set_data(x, yv)
                ax.draw_artist(ln)
                g = f - wipe
                if g < drop:                                   # bar recedes down to the label and fades
                    p = (g + 1) / drop
                    ytop = 1 + p * (frac(ystop) - 1)
                    bar.set_data([xe, xe], [0, ytop])
                    bar.set_alpha(1 - 0.85 * p)
                    ax.draw_artist(bar)
                    ycur = (yhi - ylo) * ytop + ylo
                    if ycur < ye:
                        dv.set_data([xe, xe], [ye, max(ycur, ystop)])
                        ax.draw_artist(dv)
                    continue
                dv.set_data([xe, xe], [ye, ystop])
                ax.draw_artist(dv)
                h = g - drop
                p = min(1.0, (h + 1) / leg)
                if xlab != xe:
                    dh.set_data([xe, xe + p * (xlab - xe)], [ystop, ystop])
                    ax.draw_artist(dh)
                tx.set_alpha(p)
                ax.draw_artist(tx)
            proc.stdin.write(bytes(fig.canvas.buffer_rgba()))
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed for {path}")
    finally:
        if proc.poll() is None:
            proc.kill()
        plt.close(fig)
    return path


# --------------------------------------------------------------------------
# PowerPoint
# --------------------------------------------------------------------------
AUTOPLAY = (
    '<p:timing xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:tnLst><p:par>'
    '<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
    '<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
    '<p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
    '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>'
    '<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
    '<p:par><p:cTn id="5" presetID="1" presetClass="mediacall" presetSubtype="0" fill="hold" nodeType="withEffect">'
    '<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst><p:cmd type="call" cmd="playFrom(0.0)">'
    '<p:cBhvr><p:cTn id="6" dur="{dur}" fill="hold"/><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr>'
    '</p:cmd></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
    '</p:childTnLst></p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond>'
    '</p:prevCondLst><p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond>'
    '</p:nextCondLst></p:seq>'
    '<p:seq concurrent="1" nextAc="seek"><p:cTn id="7" restart="whenNotActive" fill="hold" evtFilter="cancelBubble" '
    'nodeType="interactiveSeq"><p:stCondLst><p:cond evt="onClick" delay="0"><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>'
    '</p:cond></p:stCondLst><p:endSync evt="end" delay="0"><p:rtn val="all"/></p:endSync><p:childTnLst>'
    '<p:par><p:cTn id="8" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
    '<p:par><p:cTn id="9" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
    '<p:par><p:cTn id="10" presetID="2" presetClass="mediacall" presetSubtype="0" fill="hold" nodeType="clickEffect">'
    '<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst><p:cmd type="call" cmd="togglePause"><p:cBhvr>'
    '<p:cTn id="11" dur="1" fill="hold"/><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr></p:cmd>'
    '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst>'
    '</p:cTn><p:nextCondLst><p:cond evt="onClick" delay="0"><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cond>'
    '</p:nextCondLst></p:seq>'
    '<p:video><p:cMediaNode vol="80000"><p:cTn id="12" fill="hold" display="0"><p:stCondLst>'
    '<p:cond delay="indefinite"/></p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cMediaNode>'
    '</p:video></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>'
)


def _poster(mp4, jpg):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-sseof", "-0.2", "-i", str(mp4), "-frames:v", "1",
                    "-vf", "scale=1280:-2", "-q:v", "4", str(jpg)], check=True)


def _duration_ms(mp4):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(mp4)], capture_output=True, text=True, check=True)
    return int(float(out.stdout.strip()) * 1000)


def _add_video(slide, mp4, poster, left, top, width, height):
    from lxml import etree

    mv = slide.shapes.add_movie(str(mp4), left, top, width, height, poster_frame_image=str(poster),
                                mime_type="video/mp4")
    spid = mv.shape_id
    sld = slide._element
    for old in sld.findall("{http://schemas.openxmlformats.org/presentationml/2006/main}timing"):
        sld.remove(old)
    timing = etree.fromstring(AUTOPLAY.replace("{spid}", str(spid)).replace("{dur}", str(_duration_ms(mp4))))
    ext = sld.find("{http://schemas.openxmlformats.org/presentationml/2006/main}extLst")
    if ext is not None:
        ext.addprevious(timing)
    else:
        sld.append(timing)
    return mv


def _title(slide, text, sub=None, sub_color=None):
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt

    tb = slide.shapes.add_textbox(Emu(457200), Emu(150000), Emu(11277600), Emu(900000))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = text
    r.font.size, r.font.bold = Pt(28), True
    r.font.color.rgb = RGBColor(0x12, 0x35, 0x5B)
    if sub:
        p2 = tf.add_paragraph()
        r2 = p2.add_run()
        r2.text = sub
        r2.font.size, r2.font.bold, r2.font.italic = Pt(20), True, True
        if sub_color:
            r2.font.color.rgb = RGBColor.from_string(sub_color.lstrip("#"))
    return tb


def build_deck(sections, summary, path, month_label):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    blank = prs.slide_layouts[6]

    s = prs.slides.add_slide(blank)
    _title(s, summary.get("title", f"Tidewatch 36-hour Forecast Validation, {month_label}"),
           summary.get("subtitle", "VIMS Tidewatch 3.0: every forecast at every actively updating station"))
    tb = s.shapes.add_textbox(Inches(0.5), Inches(1.6), Inches(12.3), Inches(5.0)).text_frame
    tb.word_wrap = True
    lines = [
        f"{summary['forecasts']} forecasts from {summary['runs']} Tidewatch runs at {summary['stations']} stations.",
        "Each station: a table of the month's forecasts, then one animation per 36-hour forecast "
        "(colored in sequence red, orange, yellow, green, blue, indigo, violet), then a composite of the month.",
        "Observations (blue) are Tidewatch's own 3-day / 30-day hindcast, ft above MLLW.",
        "Tidewatch Skill = 0.25 [R² + e^(−|Bias|/σ) + e^(−MAE/σ) + e^(−RMSE/σ)], σ = SD of observations in the window; "
        "no-skill ≈ 0.45.",
        summary.get("nws_note", ""),
        "Stations marked ⚠ publish another station's observation series (aliasing check, 7 Oct 2026).",
    ]
    lines = summary.get("intro", lines)
    for i, line in enumerate([x for x in lines if x]):
        p = tb.paragraphs[0] if i == 0 else tb.add_paragraph()
        p.text = line
        p.font.size = Pt(18)
        p.space_after = Pt(10)

    # Station summary, 17 per slide.
    tab = summary["table"]
    per = 17
    for k in range(0, len(tab), per):
        part = tab.iloc[k:k + per]
        s = prs.slides.add_slide(blank)
        _title(s, f"Station summary, {month_label}", f"Mean of each station's forecasts ({k // per + 1} of "
               f"{(len(tab) + per - 1) // per})")
        cols = list(part.columns)
        shp = s.shapes.add_table(len(part) + 1, len(cols), Inches(0.5), Inches(1.45), Inches(12.3),
                                 Inches(0.3 * (len(part) + 1)))
        t = shp.table
        widths = [0.9, 4.6, 0.9, 1.0, 1.0, 1.0, 1.0, 1.9]
        for j, w in enumerate(widths[:len(cols)]):
            t.columns[j].width = Inches(w)
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = c
            cell.text_frame.paragraphs[0].font.size = Pt(12)
            cell.text_frame.paragraphs[0].font.bold = True
        for i, (_, row) in enumerate(part.iterrows(), 1):
            for j, c in enumerate(cols):
                v = row[c]
                txt = (f"{v:.4f}" if c == "R²" else f"{v:+.3f}" if c.startswith("Bias") else f"{v:.3f}") \
                    if isinstance(v, float) else str(v)
                cell = t.cell(i, j)
                cell.text = txt.replace("-", "−") if isinstance(v, float) else txt
                cell.text_frame.paragraphs[0].font.size = Pt(11)
                cell.margin_top = cell.margin_bottom = Pt(1)

    W, H = Emu(10363200), Emu(5829300)          # 16:9 video, 11.33 x 6.375 in
    left = Emu((12192000 - W) // 2)
    top = Emu(990000)
    for sec in sections:
        st, name = sec["station"], station_name(sec["station"])
        flag = " ⚠" if st in ALIASED else ""
        s = prs.slides.add_slide(blank)
        _title(s, f"{name} ({st}){flag} — {month_label}",
               f"All {len(sec['forecasts'])} VIMS 36-hr Tidewatch forecasts")
        from PIL import Image
        im = Image.open(sec["table_png"])
        w_in = 12.3
        h_in = w_in * im.height / im.width
        if h_in > 5.6:
            h_in, w_in = 5.6, 5.6 * im.width / im.height
        s.shapes.add_picture(str(sec["table_png"]), Inches((13.333 - w_in) / 2), Inches(1.45),
                             Inches(w_in), Inches(h_in))
        n = len(sec["forecasts"])
        for i, f in enumerate(sec["forecasts"]):
            s = prs.slides.add_slide(blank)
            vs = " vs. NWS NWPS 72-hr" if f["nws"] else ""
            _title(s, f"{name} ({st}){flag} — {month_label}",
                   f"VIMS 36-hr Tidewatch forecast {i + 1} of {n}{vs}: {animate.cycle_label(f['issued'])} "
                   f"(TWskill {f['skill']:.3f})", sub_color=seq_color(i))
            _add_video(s, f["mp4"], f["poster"], left, top, W, H)
        s = prs.slides.add_slide(blank)
        _title(s, f"{name} ({st}){flag} — {month_label}",
               f"All {n} forecasts in sequence, labeled with issuance (UTC) and Tidewatch Skill")
        _add_video(s, sec["composite"], sec["composite_poster"], left, top, W, H)
    prs.save(path)
    return path


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def _job(args):
    kind, spec, path = args
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".part.mp4")
    (animate.render if kind == "forecast" else render_composite)(spec, tmp)
    tmp.replace(path)
    _poster(path, path.with_suffix(".jpg"))
    return str(path)


def run(roots, out_root, month="2026-09", stations=None, workers=4, force=False):
    out = Path(out_root) / "monthly_decks" / month
    out.mkdir(parents=True, exist_ok=True)
    m0 = pd.Timestamp(f"{month}-01", tz="UTC")
    m1 = m0 + pd.offsets.MonthBegin(1)
    month_label = m0.strftime("%B %Y")
    forecasts_ids = stations or sorted({f.station for f in data.load_forecasts(roots, "MLLW")})
    # NWS forecasts issued that month, downloaded from IEM (input/nws_hml_<mon>*<year>/).
    hml = sorted((Path(config.ASA_ROOT) / "input").glob(f"nws_hml_{m0:%b}*{m0:%Y}".lower()))
    arch = nws.HmlArchive(hml[0]) if hml else None
    records, _ = validation.build_records(roots, stations=forecasts_ids, archive=arch)
    records = [r for r in records if m0 <= r["issued"] < m1]
    by = {}
    for r in records:
        by.setdefault(r["station"], []).append(r)
    order = station_order(set(by))

    jobs, sections = [], []
    for st in order:
        recs = sorted(by[st], key=lambda r: r["issued"])
        sdir = out / "animations" / st
        sec = {"station": st, "forecasts": [], "table_png": out / "tables" / f"{st}_{month}_forecast_table.png"}
        sec["table_png"].parent.mkdir(parents=True, exist_ok=True)
        table_png(st, recs, sec["table_png"], month_label)
        items = []
        for i, r in enumerate(recs):
            color = seq_color(i)
            horizon = 72 if (r["nws"] and r["nws"]["done72"]) else 36
            spec = validation.build_spec(r, horizon, None)
            spec = copy.copy(spec)
            spec["tw_color"] = color
            spec["rows"] = [dict(spec["rows"][0], color=color)] + spec["rows"][1:]
            spec["obs_label"] = "Tidewatch Observation" if r["obs_source"] == "tidewatch-aliased" else (
                "NWPS Observation" if r["obs_source"] == "nwps" else obs_label(st))
            spec["heading"] = f"{animate.cycle_label(r['issued'])} 36-hr Forecast"
            spec["notes"] = [n for n in spec["notes"] if not n.startswith("No NWS NWPS forecast")]
            if st in COPY_OF:
                spec["notes"].insert(0, f"Caution: {COPY_OF[st]}")
            elif st in ALIASED and r["obs_source"] != "tidewatch-aliased":
                spec["notes"].insert(0, f"Caution: Tidewatch publishes the same observation series for {st} as for "
                                        f"{ALIASED[st]} (aliasing check).")
            if not r["nws"]:
                spec["title"] = "VIMS Tidewatch 3.0 — 36-hour Storm Tide Forecast Validation"
            spec["subtitle"] = (f"{station_name(st)}  (Tidewatch {st})   ·   {animate.cycle_label(r['issued'])} run   ·   "
                                f"forecast {i + 1} of {len(recs)}, {month_label}")
            mp4 = sdir / f"{i + 1:02d}_{st}_{animate.file_label(r['issued'])}_{horizon}hr.mp4"
            if force or not mp4.exists():
                jobs.append(("forecast", spec, mp4))
            sec["forecasts"].append({"issued": r["issued"], "skill": r["tw_stats"].get("Skill", np.nan),
                                     "nws": bool(r["nws"]),
                                     "mp4": mp4, "poster": mp4.with_suffix(".jpg")})
            items.append({"series": r["tw"].loc[r["t0"]:r["t0"] + pd.Timedelta(hours=36)], "issued": r["issued"],
                          "skill": r["tw_stats"].get("Skill", np.nan), "color": color})
        obs = recs[0]["obs"]
        comp = sdir / f"{len(recs) + 1:02d}_{st}_{month}_composite.mp4"
        cspec = {"items": items, "obs": obs,
                 "title": f"{station_name(st)} ({st}) — Tidewatch 36-hour forecasts, {month_label}",
                 "subtitle": "Observed water level in blue; each 36-h forecast in sequence color, labeled with its "
                             "issuance (UTC) and Tidewatch Skill"}
        if force or not comp.exists():
            jobs.append(("composite", cspec, comp))
        sec["composite"], sec["composite_poster"] = comp, comp.with_suffix(".jpg")
        sections.append(sec)

    log.info("%d stations, %d forecasts, %d animations to render", len(sections), len(records), len(jobs))
    if jobs:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for p in ex.map(_job, jobs):
                log.info("wrote %s", p)

    rows = []
    for st in order:
        recs = by[st]
        m = lambda k: float(np.nanmean([r["tw_stats"].get(k, np.nan) for r in recs]))  # noqa: E731
        rows.append({"Station": st + (" ⚠" if st in ALIASED else ""), "Name": station_name(st), "n": len(recs),
                     "R²": m("R2"), "Bias (ft)": m("Bias"), "MAE (ft)": m("MAE"), "RMSE (ft)": m("RMSE"),
                     "Tidewatch Skill": m("Skill")})
    summary = {"forecasts": len(records), "runs": len({r["issued"] for r in records}), "stations": len(order),
               "table": pd.DataFrame(rows),
               "nws_note": (f"NWS NWPS forecasts ({sum(1 for r in records if r['nws'])} of {len(records)} forecasts "
                            "have one) come from the Iowa Environmental Mesonet archive of issued NWS HML products: "
                            "the latest issuance before each Tidewatch run, verified over the same 36 h and out to 72 h."
                            if arch else "")}
    summary["table"].to_csv(out / f"station_summary_{month}.csv", index=False)
    deck = out / f"Tidewatch_Forecast_Validation_{m0:%B_%Y}.pptx"
    build_deck(sections, summary, deck, month_label)
    return deck, summary
