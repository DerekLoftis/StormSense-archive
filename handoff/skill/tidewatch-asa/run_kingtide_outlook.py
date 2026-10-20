"""Today's outlook (Norfolk / Hampton Roads) and the event's observed peaks, for the king tide sitrep deck.

Uses output/event_decks/2026-10_kingtide/models/series.pkl from run_kingtide_models.py.
"""
import pickle
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asa import event_models as E  # noqa: E402
from asa.data import station_name  # noqa: E402
from run_kingtide_models import FLOOD, ORDER, _num  # noqa: E402

EV = Path(__file__).resolve().parent / "output" / "event_decks" / "2026-10_kingtide_v2"
OUT = EV / "outlook"
EDT = pd.Timedelta(hours=4)
ASOF = pd.Timestamp("2026-10-12 11:36", tz="UTC")   # when the first sitrep went out (Derek flood mapping in Norfolk)
PEAK_HI = pd.Timestamp("2026-10-14 06:00", tz="UTC")  # end of the last run's 36-h window
HR = ["SWPT", "USPT", "USPA", "USLI", "USLB", "USLN", "USLR", "USBC", "USFM", "USHR", "KIPT", "YRCG"]


def latest(series, src, st, now):
    cyc = series.get(src, {}).get(st, [])
    cyc = [(ct, s) for ct, s in cyc if ct <= now + pd.Timedelta(hours=1)]
    return cyc[-1] if cyc else (None, None)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d = pickle.load(open(EV / "models" / "series.pkl", "rb"))
    series, obs = d["series"], d["obs"]
    now = ASOF
    # The newest Tidewatch run (12Z today) has no verifying observations yet but belongs in the outlook.
    lo, hi = now - pd.Timedelta(hours=30), now + pd.Timedelta(hours=48)

    # ---- Figure: Sewells Point, latest cycle from each source (EDT)
    fig, ax = plt.subplots(figsize=(19.2, 8.6))
    o = obs["SWPT"].loc[lo:hi]
    ob, oa = o.loc[:now], o.loc[now:]
    ax.plot(_num(ob.index - EDT), ob.values, color="#9ecae1", lw=5, label=f"Observed (to {now - EDT:%a %H:%M} EDT)", zorder=2)
    ax.plot(_num(oa.index - EDT), oa.values, color="#9ecae1", lw=5, ls=(0, (1.2, 0.8)), label="Observed afterwards", zorder=2)
    for src in ORDER:
        ct, s = latest(series, src, "SWPT", now)
        if s is None:
            continue
        s = s.loc[max(ct, lo):hi]
        ax.plot(_num(s.index - EDT), s.values, color=E.COLORS[src], lw=2.4 if src in ("Tidewatch", "NWS") else 1.6,
                label=f"{src}, cycle {ct - EDT:%a %d %H:%M} EDT", zorder=3)
    for v, c, n in zip(FLOOD["SWPT"], ("#ffd400", "#ff8c00", "#e41a1c", "#9b30d9"), ("Action", "Minor", "Moderate", "Major")):
        if v > 4.9:
            continue
        ax.axhline(v, color=c, lw=1.4, ls="--")
        ax.text(_num([hi - EDT])[0], v + 0.04, f"NWS {n} {v:.1f} ft ", ha="right", va="bottom", color=c, fontsize=11)
    ax.axvline(_num([now - EDT])[0], color="k", lw=1)
    ax.text(_num([now - EDT])[0], 0.1, "  outlook issued", fontsize=12)
    ax.set_xlim(_num([lo - EDT, hi - EDT]))
    ax.set_ylim(-0.3, 5.0)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d\n%H:%M"))
    ax.set_ylabel("Water level (ft above MLLW)", fontsize=13)
    ax.set_xlabel("Local time (EDT)", fontsize=13)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=10.5, loc="upper left", ncol=2)
    ax.set_title("Sewells Point, Norfolk (SWPT): latest forecast from every archived source in the Monday-morning sitrep (12 Oct, observations then to 07:36 EDT), and what was observed",
                 loc="left", fontsize=16)
    fig.tight_layout()
    fig.savefig(OUT / "SWPT_today_all_sources.png", dpi=100)
    plt.close(fig)

    # ---- Table: forecast high tides in the next ~36 h at Hampton Roads stations
    rows = []
    for st in HR:
        if st not in obs:
            continue
        for src in ORDER:
            ct, s = latest(series, src, st, now)
            if s is None:
                continue
            seg = s.loc[now:now + pd.Timedelta(hours=36)]
            if len(seg) < 6:
                continue
            # Pad with the last 6 h of observations so a high tide shortly after "now" is found.
            base = obs[st].loc[now - pd.Timedelta(hours=6):now - pd.Timedelta(minutes=1)]
            for tp, hp in E.high_tides(pd.concat([base, seg[seg.index > base.index.max()] if len(base) else seg]).sort_index(),
                                       now, now + pd.Timedelta(hours=36), 8):
                rows.append({"station": st, "name": station_name(st), "source": src, "cycle_utc": ct,
                             "high_tide_edt": (tp - EDT).tz_localize(None), "height_ft_mllw": round(hp, 2)})
    ht = pd.DataFrame(rows)
    for st in HR:
        if st in obs:
            for tp, hp in E.high_tides(obs[st], now, now + pd.Timedelta(hours=36), 8):
                ht = pd.concat([ht, pd.DataFrame([{"station": st, "name": station_name(st), "source": "Observed", "cycle_utc": pd.NaT,
                                                     "high_tide_edt": (tp - EDT).tz_localize(None), "height_ft_mllw": round(hp, 2)}])])
    ht = ht.sort_values(["station", "source", "high_tide_edt"]).reset_index(drop=True)
    ht["k"] = ht.groupby(["station", "source"]).cumcount()
    ht.drop(columns="k").to_csv(OUT / "hampton_roads_next_high_tides.csv", index=False)
    srcs = [s for s in ORDER if s in set(ht.source)] + ["Observed"]
    sts = [s for s in HR if s in set(ht.station)]
    fig, ax = plt.subplots(figsize=(23.5, 0.62 * len(sts) + 2.4))
    ax.set_axis_off()
    colw = 1.0 / (len(srcs) + 1.6)
    ax.text(0, 1.0, "Station", fontweight="bold", fontsize=12, va="top", transform=ax.transAxes)
    for j, s in enumerate(srcs):
        ax.text(colw * (j + 1.6), 1.0, s.replace(" coastal ", "\n"), fontweight="bold", fontsize=11.5, va="top",
                color=E.COLORS.get(s, "#1f77b4"), transform=ax.transAxes)
    for i, st in enumerate(sts):
        y = 1.0 - (i + 1.3) / (len(sts) + 1.6)
        ax.text(0, y, f"{st}\n{station_name(st)[:30]}", fontsize=10.5, va="top", transform=ax.transAxes)
        for j, s in enumerate(srcs):
            g = ht[(ht.station == st) & (ht.source == s) & (ht.k < 2)]
            fl = FLOOD.get(st)
            if not len(g):
                ax.text(colw * (j + 1.6), y, "–", fontsize=10.5, va="top", transform=ax.transAxes)
            for k, r in enumerate(g.itertuples()):
                h = r.height_ft_mllw
                col = "#C00000" if (fl and h >= fl[1]) else ("#E07B00" if (fl and h >= fl[0]) else "black")
                ax.text(colw * (j + 1.6), y - k * 0.32 / (len(sts) + 1.6), f"{r.high_tide_edt:%a %H:%M}  {h:.2f} ft",
                        fontsize=10, va="top", color=col, family="monospace", fontweight="bold" if col != "black" else "normal",
                        transform=ax.transAxes)
    ax.text(0, -0.02, "Next two forecast high tides in local time (EDT), ft above MLLW, from each source's latest cycle in the Monday-morning sitrep (12 Oct); last column: what was observed. "
                      "Orange = at or above the NWS action level, red = at or above minor flooding (where NWS sets thresholds). "
                      "NWM heights converted from NAVD88; NWM ran 0.5-1.0 ft low all weekend, so read it as a lower bound.",
            fontsize=10, style="italic", color="#444", transform=ax.transAxes, va="top", wrap=True)
    fig.savefig(OUT / "hampton_roads_next_high_tides.png", dpi=100, bbox_inches="tight")
    plt.close(fig)

    # ---- Observed event peaks
    rows = []
    for st, o in obs.items():
        s = o.loc[pd.Timestamp("2026-10-08", tz="UTC"):PEAK_HI].dropna()
        if s.empty:
            continue
        fl = FLOOD.get(st)
        cat = "no flood thresholds set" if not fl else (
            "major" if s.max() >= fl[3] else "moderate" if s.max() >= fl[2] else "minor" if s.max() >= fl[1]
            else "action" if s.max() >= fl[0] else "below action")
        rows.append({"station": st, "name": station_name(st), "peak_ft_mllw": round(float(s.max()), 2),
                     "peak_utc": s.idxmax(), "peak_edt": (s.idxmax() - EDT).tz_localize(None), "category": cat})
    pk = pd.DataFrame(rows).sort_values("peak_ft_mllw", ascending=False)
    pk.to_csv(OUT / "observed_event_peaks.csv", index=False)
    print(pk.head(16).to_string())
    print(ht[ht.k < 2].to_string())


if __name__ == "__main__":
    main()
