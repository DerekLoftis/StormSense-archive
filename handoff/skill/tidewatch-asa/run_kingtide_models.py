"""Other archived guidance over the Oct 2026 king tide weekend: scoring and figures.

Reads output/event_decks/2026-10_kingtide_v2/records.pkl (written by
run_event_deck_kingtide_oct2026.py render) for the observations and the
Tidewatch forecasts, the NWPS captures for NWS, and data/models/ for STOFS-2D,
STOFS-3D, NWM coastal and CBOFS. Writes output/event_decks/2026-10_kingtide_v2/models/.
"""
import json
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
from asa import event_models as E, nws, validation as V  # noqa: E402
from asa.data import STATION_INFO, station_name  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "tidewatch-archive" / "data"
EV = ROOT / "output" / "event_decks" / "2026-10_kingtide_v2"
OUT = EV / "models"
T_LO = pd.Timestamp("2026-10-08 00:00", tz="UTC")
T_HI = pd.Timestamp("2026-10-12 18:00", tz="UTC")   # last cycle in the event window
COV = pd.read_csv(ROOT.parent / "surge_guidance" / "station_coverage.csv")
FLOOD = {"SWPT": (4.0, 4.5, 5.5, 6.5), "KIPT": (4.0, 4.5, 5.0, 5.5), "YRCG": (4.0, 4.5, 5.0, 5.5),
         "WACH": (6.0, 6.5, 7.5, 8.0), "WMPT": (2.5, 3.0, 3.5, 4.0), "LEWI": (2.5, 3.0, 3.5, 4.0),
         "BISH": (3.0, 3.2, 3.7, 4.2), "SLIM": (2.5, 3.0, 4.0, 5.0), "DAHL": (3.0, 3.5, 5.0, 7.0),
         "WADC": (4.2, 5.3, 6.5, 7.0), "USFM": (4.0, 4.5, 5.5, 6.5), "USJT": (3.5, 4.0, 4.5, 5.0)}
ORDER = ["Tidewatch", "NWS", "STOFS-2D-Global", "STOFS-3D-Atlantic", "NWM coastal medium range",
         "NWM coastal short range", "CBOFS"]


def offsets(st_ids):
    """NAVD88->MLLW (Tidewatch's own) and MSL->MLLW (NOAA) per station, ft."""
    navd, msl = {}, {}
    cyc = sorted((DATA / "tidewatch" / "f36" / "2026" / "10").glob("2026-10-1*"))[-1]
    for st in st_ids:
        a, b = cyc / f"{st}_2018_f36_MLLW.csv", cyc / f"{st}_2018_f36_NAVD.csv"
        if a.exists() and b.exists():
            try:
                x = pd.read_csv(a)["pa30"].to_numpy(float) - pd.read_csv(b)["pa30"].to_numpy(float)
                navd[st] = float(np.nanmedian(x))
            except Exception:
                pass
        no = STATION_INFO.get(st, {}).get("station_no")
        p = ROOT.parent / "tidewatch-datum-charts" / "noaa_datums" / f"{no}.json"
        if p.exists():
            d = {x["name"]: x["value"] for x in json.load(open(p))["datums"]}
            if d.get("MSL") is not None and d.get("MLLW") is not None:
                msl[st] = d["MSL"] - d["MLLW"]
            if d.get("NAVD88") is not None and d.get("MLLW") is not None:
                navd[st] = d["NAVD88"] - d["MLLW"]      # NOAA's published value wins for NOAA gauges
    return navd, msl


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    R = pickle.load(open(EV / "records.pkl", "rb"))
    obs, recs = R["obs"], R["recs"] + R["pending"]
    now = max(s.dropna().index.max() for s in obs.values() if len(s))
    lid_of = {r.tidewatch_id: r.nws_lid for r in COV.itertuples() if isinstance(r.nws_lid, str) and r.nws_lid.endswith("2")}
    meta = E.lidmeta(sorted((DATA / "nwps").rglob("gauges.json*"))[-1])
    latlon = {st: meta[l][:2] for st, l in lid_of.items() if l in meta}
    navd, msl = offsets(sorted(lid_of))
    sources = E.load_sources(DATA / "models", lid_of, latlon, navd, msl, T_LO - pd.Timedelta(hours=1), T_HI)
    # STOFS-2D points at USGS gauges sit 0.7-1.3 ft low against the MLLW observations (datum or node not
    # comparable), so only NOAA gauges are kept for STOFS-2D.
    usgs = {r.tidewatch_id for r in COV.itertuples() if r.agency == "USGS"}
    sources["STOFS-2D-Global"] = {k: v for k, v in sources["STOFS-2D-Global"].items() if k not in usgs}
    # Tidewatch (each run's px30) and NWS (each distinct NWPS issuance in the window).
    tw = {}
    for r in recs:
        tw.setdefault(r["station"], []).append((r["issued"], r["tw"]))
    arch = nws.NwpsArchive(DATA / "nwps")
    nw = {}
    for st, lid in nws.NWPS_LID.items():
        if st not in obs:
            continue
        med, sd, _ = nws.datum_shift(arch.observed(lid), obs[st])
        shift = nws.applied_shift(med, sd)
        seen = {}
        for c in arch._captures(lid):
            if c["forecast"].empty or pd.isna(c["issued"]) or c["issued"] < T_LO - pd.Timedelta(hours=6) or c["issued"] > T_HI:
                continue
            seen.setdefault(c["issued"], c["forecast"] - shift)
        if seen:
            nw[st] = sorted(seen.items())
    # DAHL and WADC: Tidewatch publishes another gauge's observations, so verify against NWPS obs of the real gauge.
    for st in V.USE_NWPS_OBS:
        lid = nws.NWPS_LID.get(st)
        alt = arch.observed(lid) if lid else None
        if alt is not None and len(alt):
            obs[st] = alt
    allsrc = {"Tidewatch": tw, "NWS": nw, **sources}
    pickle.dump({"series": allsrc, "now": now, "navd": navd, "msl": msl, "obs": obs}, open(OUT / "series.pkl", "wb"))

    sc = E.score(allsrc, obs, now)
    sc.to_csv(OUT / "all_sources_cycle_stats.csv", index=False)
    pk = E.peak_errors(allsrc, obs, T_LO, min(now, T_HI + pd.Timedelta(hours=36)))
    pk.to_csv(OUT / "all_sources_high_tide_errors.csv", index=False)
    print(sc.groupby(["source", "horizon"])[["RMSE", "Bias", "Skill"]].mean().round(3))
    print(sc.groupby(["source"]).station.nunique())
    print(pk.groupby("source")[["err_ft", "timing_min"]].agg(["mean", "count"]).round(2))
    figures(allsrc, obs, now, sc, pk)


def _num(t):
    return mdates.date2num(pd.DatetimeIndex(t).tz_convert(None).to_pydatetime())


def figures(allsrc, obs, now, sc, pk):
    plt.rcParams.update({"font.size": 11})
    lo, hi = T_LO - pd.Timedelta(hours=6), T_HI + pd.Timedelta(days=3)
    cmap = plt.get_cmap("plasma")
    norm = matplotlib.colors.Normalize(_num([T_LO])[0], _num([T_HI])[0])
    stations = [s for s in ["SWPT", "KIPT", "YRCG", "LEWI", "WMPT", "BISH", "SLIM", "DAHL", "WADC", "WACH",
                            "USFM", "USJT", "USSF", "USPT", "USLI"] if s in obs]
    for st in stations:
        avail = [s for s in ORDER if st in allsrc.get(s, {})]
        if len(avail) < 3:
            continue
        fig, axs = plt.subplots(2, 4, figsize=(19.2, 9.6), sharex=True, sharey=True)
        o = obs[st].loc[lo:hi]
        for ax, src in zip(axs.flat, ORDER):
            ax.plot(_num(o.index), o.values, color="#1f77b4", lw=2.2, zorder=3)
            if src in avail:
                for ct, s in allsrc[src][st]:
                    s = s.loc[max(ct, lo) if False else ct:ct + pd.Timedelta(hours=72)].loc[lo:hi]
                    if len(s):
                        ax.plot(_num(s.index), s.values, color=cmap(norm(_num([ct])[0])), lw=0.9, alpha=0.85, zorder=2)
                g = sc[(sc.source == src) & (sc.station == st) & (sc.horizon == 36)]
                txt = f"36-h RMSE {g.RMSE.mean():.2f} ft, bias {g.Bias.mean():+.2f}, skill {g.Skill.mean():.3f} (n={len(g)})" if len(g) else "no verified cycles"
                ax.set_title(f"{src}\n{txt}", fontsize=10.5, loc="left")
            else:
                ax.set_title(f"{src}\nno point output here", fontsize=10.5, loc="left", color="#999")
            for v, c in zip(FLOOD.get(st, ()), ("#ffd400", "#ff8c00", "#e41a1c", "#9b30d9")):
                ax.axhline(v, color=c, lw=0.8, ls=":")
            ax.grid(alpha=0.3)
            ax.xaxis.set_major_locator(mdates.DayLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
        # last panel: latest cycle from each source
        ax = axs.flat[7]
        ax.cla()
        ax.plot(_num(o.index), o.values, color="#9ecae1", lw=4, label="Observed")
        for src in avail:
            ct, s = allsrc[src][st][-1]
            s = s.loc[ct:ct + pd.Timedelta(hours=72)].loc[lo:hi]
            ax.plot(_num(s.index), s.values, color=E.COLORS[src], lw=1.4, label=f"{src} {ct:%d/%H}Z")
        for v, c in zip(FLOOD.get(st, ()), ("#ffd400", "#ff8c00", "#e41a1c", "#9b30d9")):
            ax.axhline(v, color=c, lw=0.8, ls=":")
        ax.set_title("Latest cycle of each source", fontsize=10.5, loc="left")
        ax.legend(fontsize=7.5, loc="upper left")
        ax.grid(alpha=0.3)
        ax.set_xlim(_num([lo, hi]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
        for a in axs[:, 0]:
            a.set_ylabel("ft above MLLW")
        for a in axs[1]:
            a.set_xlabel("October 2026 (UTC)")
        fig.suptitle(f"{station_name(st)} ({st}): every archived forecast issued 8 Oct 00Z to 12 Oct 18Z (colored by issue "
                     f"time, purple = 8 Oct, yellow = 12 Oct); observations blue"
                     + ("; NWS flood thresholds dotted" if st in FLOOD else ""), fontsize=13, x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        fig.savefig(OUT / f"forecasts_{st}.png", dpi=100)
        plt.close(fig)

    # Summary: mean 36-h RMSE / skill per source per station (heat table) and bars.
    g = sc[sc.horizon == 36].groupby(["station", "source"])[["RMSE", "Skill", "Bias"]].mean().reset_index()
    g.to_csv(OUT / "station_source_36h_means.csv", index=False)
    sts = [s for s in stations if s in set(g.station)]
    srcs = [s for s in ORDER if s in set(g.source)]
    for metric, fmt, cm, rev in (("RMSE", "{:.2f}", "RdYlGn_r", False), ("Skill", "{:.3f}", "RdYlGn", False),
                                 ("Bias", "{:+.2f}", "RdBu_r", False)):
        M = g.pivot(index="station", columns="source", values=metric).reindex(index=sts, columns=srcs)
        fig, ax = plt.subplots(figsize=(15, 0.48 * len(sts) + 1.8))
        vals = M.to_numpy(float)
        if metric == "Bias":
            vmax = np.nanmax(np.abs(vals))
            im = ax.imshow(vals, cmap=cm, vmin=-vmax, vmax=vmax, aspect="auto")
        else:
            im = ax.imshow(vals, cmap=cm, aspect="auto")
        for i in range(vals.shape[0]):
            for j in range(vals.shape[1]):
                if np.isfinite(vals[i, j]):
                    ax.text(j, i, fmt.format(vals[i, j]), ha="center", va="center", fontsize=11)
        ax.set_xticks(range(len(srcs)))
        ax.set_xticklabels([s.replace(" coastal ", "\n").replace("-Atlantic", "-Atl").replace("-Global", "-Glo") for s in srcs])
        ax.set_yticks(range(len(sts)))
        ax.set_yticklabels([f"{s}  {station_name(s)[:28]}" for s in sts])
        ax.xaxis.tick_top()
        fig.colorbar(im, ax=ax, fraction=0.02)
        fig.tight_layout()
        fig.savefig(OUT / f"heat_{metric}.png", dpi=100)
        plt.close(fig)

    # Bars: mean over the stations every source shares with Tidewatch (fair comparison) + peak errors.
    # Each source against Tidewatch at the same stations and over cycles issued in the same window.
    fair = [s for s in stations if s not in ("DAHL", "WADC")]   # Tidewatch fits another gauge's record there
    s36 = sc[(sc.horizon == 36) & sc.station.isin(fair)]
    pk36 = pk[(pk.lead_h <= 36) & pk.station.isin(fair)]
    rows = []
    for src in srcs:
        if src == "Tidewatch":
            continue
        sts_ = sorted(set(s36[s36.source == src].station))
        a_ = s36[(s36.source == src)]
        b_ = s36[(s36.source == "Tidewatch") & s36.station.isin(sts_)]
        pa = pk36[pk36.source == src]
        pb = pk36[(pk36.source == "Tidewatch") & pk36.station.isin(sts_)]
        rows.append({"source": src, "stations": len(sts_), "cycles": len(a_), "RMSE": a_.RMSE.mean(),
                     "TW_RMSE": b_.RMSE.mean(), "Bias": a_.Bias.mean(), "TW_Bias": b_.Bias.mean(),
                     "Skill": a_.Skill.mean(), "TW_Skill": b_.Skill.mean(), "peak_err": pa.err_ft.mean(),
                     "TW_peak_err": pb.err_ft.mean(), "peak_abs": pa.err_ft.abs().mean(),
                     "TW_peak_abs": pb.err_ft.abs().mean(), "peak_timing_min": pa.timing_min.mean(),
                     "peaks": len(pa)})
    pr = pd.DataFrame(rows)
    pr.to_csv(OUT / "paired_vs_tidewatch.csv", index=False)
    print(pr.round(3).to_string())
    fig, axs = plt.subplots(1, 4, figsize=(19.2, 6.0))
    x = np.arange(len(pr))
    lab = [s.replace(" coastal ", "\n").replace("-Atlantic", "\n3D-Atl").replace("STOFS-3D", "STOFS").replace("-Global", "")
           + f"\n({n} stn)" for s, n in zip(pr.source, pr.stations)]
    for a, (k, kt, title) in zip(axs, (("RMSE", "TW_RMSE", "Mean 36-h RMSE (ft)"), ("Bias", "TW_Bias", "Mean 36-h bias (ft)"),
                                     ("Skill", "TW_Skill", "Mean Tidewatch Skill (36 h)"),
                                     ("peak_abs", "TW_peak_abs", "Mean |high-tide height error| (ft)"))):
        a.bar(x - 0.2, pr[k], 0.4, color=[E.COLORS[s] for s in pr.source], label="source")
        a.bar(x + 0.2, pr[kt], 0.4, color="#9ecae1", edgecolor="#0B3FE0", label="Tidewatch, same stations")
        a.set_xticks(x)
        a.set_xticklabels(lab, fontsize=8.5)
        a.set_title(title, loc="left")
        a.axhline(0, color="k", lw=0.7)
        a.grid(alpha=0.3, axis="y")
        if k == "Skill":
            a.set_ylim(0.4, 1)
    axs[0].legend(fontsize=9, loc="upper left")
    fig.suptitle("Each source (left bar) against Tidewatch at the same stations (right bar), cycles issued 8 Oct 00Z to 12 Oct 18Z, completed windows (DAHL and WADC left out: Tidewatch's forecasts there are fitted to another gauge)",
                 x=0.01, ha="left", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(OUT / "bars_all_sources.png", dpi=100)
    plt.close(fig)


if __name__ == "__main__":
    main()
