"""Hampton Roads report figures for Hurricane Matthew (Oct 2016), no flood-category bands.

Reuses the parsers in run_matthew_2016.py and adds HRSD pump-station rain gauges
(Derek's HRSD workbook) and NOAA astronomical tide predictions for surge.
Outputs: output/matthew_2016/report/
"""
import re
from pathlib import Path
import numpy as np
import pandas as pd
import openpyxl
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D

import run_matthew_2016 as m

OUT = m.OUT / "report"
OUT.mkdir(parents=True, exist_ok=True)
HRSD_XLSX = Path("/mnt/project-files/tidewatch-asa/input/hrsd_rain_oct2016/HRSD_Rainfall_Data_100616-101316_withLocations.xlsx")
INVALID = {"120", "130", "149", "252", "256", "261", "281"}      # HRSD notes: invalid 10/8 07:30 - 10/9 07:30
CALIB = {"014", "148", "184", "231", "260", "262"}               # HRSD notes: calibrated during 10/6 - 10/13
EDT = pd.Timedelta(hours=4)
plt.rcParams.update({"font.size": 9, "axes.titlesize": 9.5, "axes.titleweight": "bold", "font.family": "DejaVu Sans"})

HR_PFM = {  # PFM points in Hampton Roads -> short label
    "NORFOLK INTL AIRPORT-NORFOLK/PORTSMOUTH VA": "Norfolk Intl (ORF)",
    "OCEANA NAS-VIRGINIA BEACH VA": "NAS Oceana (NTU)",
    "NEWPORT NEWS/WILLIAMSBURG INTL AIRPORT-NEWPORT NEWS VA": "Newport News (PHF)",
    "LANGLEY AFB-HAMPTON VA": "Langley AFB (LFI)",
    "WILLIAMSBURG/JAMESTOWN AIRPORT-JAMES CITY VA": "Williamsburg (JGG)",
}


def load_hrsd():
    wb = openpyxl.load_workbook(HRSD_XLSX, data_only=True, read_only=True)
    rows = list(wb["Data"].iter_rows(values_only=True))
    head, lat, lon = rows[1], rows[2], rows[3]
    data = [r for r in rows[4:] if isinstance(r[0], str) and re.match(r"\d{2}/\d{2}/\d{2} ", r[0])]
    t = pd.to_datetime([r[0] for r in data], format="%m/%d/%y %H:%M:%S")
    gauges, series = [], {}
    for j in range(1, len(head)):
        if head[j] is None:
            continue
        mm = re.match(r"MMPS-(\d{3})", head[j])
        gid = mm.group(1) if mm else head[j]
        name = re.sub(r"\.Rain Gauge.*$", "", head[j].split(":", 1)[1] if ":" in head[j] else "").strip() or gid
        name = re.sub(r"^[A-Z ]+ ?[A-Z]* ?- ?", "", name).strip() or name
        vals = pd.Series([r[j] if isinstance(r[j], (int, float)) else np.nan for r in data], index=t)
        series[gid] = vals
        gauges.append(dict(id=gid, label=head[j], name=name, lat=lat[j], lon=lon[j],
                           status="invalid" if gid in INVALID else ("calibrated" if gid in CALIB else "valid")))
    g = pd.DataFrame(gauges)
    s = pd.DataFrame(series)          # 15-min increments, local time (EDT assumed)
    w0, w1 = pd.Timestamp("2016-10-08 07:30"), pd.Timestamp("2016-10-09 07:30")
    g["total_in"] = [s[i].sum() for i in g.id]
    g["event24_in"] = [s[i][(s.index > w0) & (s.index <= w1)].sum() for i in g.id]
    g["max_1h_in"] = [s[i].rolling(4).sum().max() for i in g.id]
    g["max_15min_in"] = [s[i].max() for i in g.id]
    g["time_max_1h_edt"] = [s[i].rolling(4).sum().idxmax() for i in g.id]
    return g, s


def main():
    obs = m.load_obs()
    pred = pd.read_csv(m.IN / "coops_predictions_SWPT_8638610_CBBT_8638863_hourly_MLLW_ft_UTC.csv", parse_dates=["time_utc"]).set_index("time_utc")
    cfw = pd.concat([m.parse_cfw(m.IN / f) for f in ("afos_CFWAKQ.txt", "afos_CFWLWX.txt")], ignore_index=True)
    pfm, pfm_pts = m.parse_pfm(m.IN / "afos_PFMAKQ.txt")
    pns = m.parse_pns(m.IN / "afos_PNSAKQ.txt")
    track = m.parse_track(m.IN / "afos_TCMAT4.txt")
    g, s = load_hrsd()
    g.to_csv(OUT / "hrsd_gauge_totals.csv", index=False, float_format="%.2f")
    gv = g[g.status != "invalid"]

    numbers = {}
    # ---------------- surge
    sw = obs["SWPV2"]; sw_h = sw[sw.index.minute == 0]
    surge_sw = (sw_h - pred.swpt_8638610_pred_ft).dropna()
    surge_cb = (obs["CBBT"] - pred.cbbt_8638863_pred_ft).dropna()
    numbers["swpt_max_surge"] = (surge_sw.max(), surge_sw.idxmax())
    numbers["cbbt_max_surge"] = (surge_cb.max(), surge_cb.idxmax())
    numbers["swpt_peak"] = (sw.max(), sw.idxmax())
    numbers["cbbt_peak"] = (obs["CBBT"].max(), obs["CBBT"].idxmax())
    numbers["swpt_pred_at_peak"] = pred.swpt_8638610_pred_ft.loc[sw_h.idxmax()]
    fc_sw = m.load_hml_fc("SWPV2")

    # ---------------- Figure 1: map
    fig = plt.figure(figsize=(7.2, 4.9))
    ax = fig.add_axes([0.07, 0.07, 0.66, 0.9])
    ax.set_facecolor("#dbe9f6")
    m._draw(ax, m._geo("ne_10m_land"), "fill", color="#f2efe9", lw=0)
    m._draw(ax, m._geo("ne_10m_minor_islands"), "fill", color="#f2efe9", lw=0)
    m._draw(ax, m._geo("ne_10m_coastline"), "line", color="#7a8b99", lw=0.6)
    m._draw(ax, m._geo("ne_10m_admin_1_states_provinces_lines"), "line", color="#999", lw=0.7, ls="--")
    sc = ax.scatter(gv.lon, gv.lat, c=gv.total_in, cmap="YlGnBu", vmin=0, vmax=14, s=30, edgecolor="#333", lw=0.4, zorder=4)
    gi = g[g.status == "invalid"]
    ax.scatter(gi.lon, gi.lat, marker="x", c="#999", s=18, lw=0.8, zorder=4)
    for name, lab in HR_PFM.items():
        la, lo = pfm_pts[name]
        ax.plot(lo, la, "s", ms=8, mfc="none", mec="#6a4c93", mew=1.5, zorder=5)
        short = lab.split(" (")[1].rstrip(")")
        ax.annotate(short, (lo, la), fontsize=7, color="#6a4c93", xytext={"PHF": (-24, 2), "JGG": (7, 2), "LFI": (7, -3)}.get(short, (6, -9)), textcoords="offset points", zorder=6, fontweight="bold")
    wl = {"SWPV2": "Sewells Point", "FTMV2": "Fort Monroe", "YKTV2": "Yorktown", "JSFV2": "Jamestown", "KPTV2": "Kiptopeke",
          "CBBT": "CBBT"}
    off = {"SWPV2": (-62, -10), "FTMV2": (-58, 4), "YKTV2": (5, 4), "JSFV2": (5, 4), "KPTV2": (5, 2), "CBBT": (6, 2), "DUCK": (6, 2)}
    for lid, lab in wl.items():
        _, la, lo, _ = m.GAUGES[lid]
        hml = lid not in ("CBBT", "DUCK")
        ax.plot(lo, la, "^" if hml else "D", ms=9 if hml else 8, mfc="#ef8a17" if hml else "#2a9d8f", mec="k", zorder=7)
        ax.annotate(lab, (lo, la), fontsize=7.5, xytext=off[lid], textcoords="offset points", zorder=8,
                    bbox=dict(fc="white", ec="none", alpha=0.75, pad=0.5))
    for lid in ("MNPV2", "ELZV2", "LHNV2", "HMNV2", "BLYV2", "NMDV2", "NWRV2"):
        _, la, lo = m.OBS_ONLY[lid]
        ax.plot(lo, la, "o", ms=5, mfc="white", mec="k", zorder=6)
    ax.set_xlim(-76.95, -75.75); ax.set_ylim(36.55, 37.45); ax.set_aspect(1 / np.cos(np.radians(36.8)))
    ax.tick_params(labelsize=7.5); ax.grid(alpha=0.25)
    cax = fig.add_axes([0.765, 0.2, 0.2, 0.025])
    cb = fig.colorbar(sc, cax=cax, orientation="horizontal"); cb.set_label("HRSD gauge total, 6-12 Oct (in)", fontsize=7.5); cb.ax.tick_params(labelsize=7)
    h = [Line2D([], [], marker="^", ls="", mfc="#ef8a17", mec="k", ms=8, label="NWS hourly water level forecast (HML)"),
         Line2D([], [], marker="D", ls="", mfc="#2a9d8f", mec="k", ms=7, label="NWS tide-table forecast + NOS gauge"),
         Line2D([], [], marker="o", ls="", mfc="white", mec="k", ms=5, label="Water level observations only"),
         Line2D([], [], marker="s", ls="", mfc="none", mec="#6a4c93", mew=1.4, ms=7, label="NWS point forecast (QPF)"),
         Line2D([], [], marker="o", ls="", mfc="#41b6c4", mec="#333", ms=6, label="HRSD rain gauge (valid)"),
         Line2D([], [], marker="x", ls="", c="#999", ms=5, label="HRSD gauge flagged invalid")]
    ax.legend(handles=h, loc="upper right", fontsize=6.6, framealpha=0.95)
    # inset: track
    ia = fig.add_axes([0.755, 0.42, 0.235, 0.52])
    ia.set_facecolor("#dbe9f6")
    m._draw(ia, m._geo("ne_10m_land"), "fill", color="#f2efe9", lw=0)
    m._draw(ia, m._geo("ne_10m_coastline"), "line", color="#7a8b99", lw=0.4)
    tr = track[(track.time >= "2016-10-07 03:00")]
    ia.plot(tr.lon, tr.lat, "-o", color="#c1121f", ms=2.5, lw=1.3)
    for _, q in tr.iterrows():
        if q.time.hour == 9:
            ia.annotate(f"{q.time:%d/%H}Z", (q.lon, q.lat), fontsize=5.5, color="#c1121f", xytext=(3, -6), textcoords="offset points")
    ia.add_patch(plt.Rectangle((-76.95, 36.55), 1.2, 0.9, fill=False, ec="k", lw=0.8))
    ia.set_xlim(-81.5, -71.5); ia.set_ylim(27.5, 38.2); ia.set_xticks([]); ia.set_yticks([])
    ia.set_title("Matthew center (NHC)", fontsize=7)
    fig.savefig(OUT / "Fig1_HamptonRoads_map.png", dpi=220); plt.close(fig)

    # ---------------- Figure 2: water levels (no flood bands)
    t0, t1 = pd.Timestamp("2016-10-07 12:00"), pd.Timestamp("2016-10-11 12:00")
    fig, axs = plt.subplots(2, 2, figsize=(7.2, 5.2), sharex=True)
    cmap = plt.get_cmap("plasma"); norm = matplotlib.colors.Normalize(mdates.date2num(pd.Timestamp("2016-10-05")), mdates.date2num(pd.Timestamp("2016-10-11")))
    for ax, lid, title in zip(axs.flat[:3], ("SWPV2", "FTMV2", "YKTV2"), ("(a) Sewells Point (SWPV2 / 8638610)", "(b) Fort Monroe (FTMV2)", "(c) Yorktown USCG (YKTV2 / 8637689)")):
        fc = m.load_hml_fc(lid)
        fc = fc[fc.issued >= "2016-10-05"]
        for i, gg in fc.groupby("issued"):
            ax.plot(gg.valid, gg.fc, color=cmap(norm(mdates.date2num(i))), lw=0.8, alpha=0.8)
        o = obs[lid]
        ax.plot(o.index, o.values, color="#0b3d91", lw=1.8)
        if lid == "SWPV2":
            ax.plot(pred.index, pred.swpt_8638610_pred_ft, color="#555", lw=0.9, ls="--")
        ax.set_title(title, loc="left"); ax.grid(alpha=0.3); ax.set_xlim(t0, t1)
    ax = axs.flat[3]
    c = cfw[(cfw.location == "CHESAPEAKE BAY BRIDGE TUNNEL VA") & (cfw.issued >= "2016-10-05")]
    ax.plot(obs["CBBT"].index, obs["CBBT"].values, color="#0b3d91", lw=1.8)
    ax.plot(pred.index, pred.cbbt_8638863_pred_ft, color="#555", lw=0.9, ls="--")
    ax.scatter(c.time_utc, c.total, c=[cmap(norm(mdates.date2num(i))) for i in c.issued], s=16, edgecolor="k", lw=0.3, zorder=4)
    ax.set_title("(d) CBBT (8638863), tide tables", loc="left"); ax.grid(alpha=0.3); ax.set_xlim(t0, t1)
    for ax in axs[:, 0]:
        ax.set_ylabel("Water level (ft MLLW)")
    for ax in axs[1]:
        ax.xaxis.set_major_locator(mdates.DayLocator()); ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[12]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d Oct"))
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    fig.subplots_adjust(left=0.08, right=0.86, top=0.89, bottom=0.11, hspace=0.3, wspace=0.18)
    cb = fig.colorbar(sm, cax=fig.add_axes([0.885, 0.25, 0.018, 0.55])); cb.set_label("Forecast issue time (UTC)", fontsize=8)
    cb.ax.yaxis.set_major_formatter(mdates.DateFormatter("%d Oct")); cb.ax.tick_params(labelsize=7)
    fig.legend(handles=[Line2D([], [], color="#0b3d91", lw=1.8, label="Observed"),
                        Line2D([], [], color="#555", ls="--", lw=0.9, label="Astronomical tide (NOS)"),
                        Line2D([], [], color=cmap(0.6), lw=1, label="NWS forecasts, coloured by issue time")],
               loc="upper center", ncol=3, fontsize=7.5, frameon=False, bbox_to_anchor=(0.47, 1.0))
    fig.savefig(OUT / "Fig2_water_levels.png", dpi=220); plt.close(fig)

    # ---------------- Figure 3: surge + forecast peak convergence
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.0), gridspec_kw=dict(width_ratios=[1.35, 1]))
    sel = fc_sw[fc_sw.issued >= "2016-10-06"]
    for i, gg in sel.groupby("issued"):
        p = pred.swpt_8638610_pred_ft.reindex(gg.valid.values)
        a1.plot(gg.valid, gg.fc.values - p.values, color=cmap(norm(mdates.date2num(i))), lw=0.8, alpha=0.8)
    a1.plot(surge_sw.index, surge_sw.values, color="#0b3d91", lw=1.8, label="Observed surge (obs - tide)")
    cs = cfw[(cfw.location == "SEWELLS POINT VA") & (cfw.issued >= "2016-10-06")]
    a1.scatter(cs.time_utc, cs.surge, c=[cmap(norm(mdates.date2num(i))) for i in cs.issued], marker="D", s=12, edgecolor="k", lw=0.3, zorder=4, label="Tide-table surge (CFW)")
    a1.axhline(0, color="k", lw=0.6)
    a1.set_xlim(pd.Timestamp("2016-10-07 00:00"), pd.Timestamp("2016-10-11 00:00"))
    a1.xaxis.set_major_locator(mdates.DayLocator()); a1.xaxis.set_major_formatter(mdates.DateFormatter("%d Oct")); a1.grid(alpha=0.3); a1.set_xlabel("Valid time (UTC)")
    a1.set_ylabel("Surge / anomaly (ft)"); a1.set_title("(a) Sewells Point surge", loc="left")
    a1.legend(handles=a1.get_legend_handles_labels()[0] + [Line2D([], [], color=cmap(0.6), lw=1)], labels=a1.get_legend_handles_labels()[1] + ["Hourly forecast surge (HML - tide)"], fontsize=6.3, loc="upper left")
    # convergence of forecast peak total water at SWPT (HML) and CBBT (CFW)
    pk = []
    for i, gg in fc_sw.groupby("issued"):
        w = gg[(gg.valid >= "2016-10-08 12:00") & (gg.valid <= "2016-10-09 12:00")]
        if len(w) and w.valid.min() <= numbers["swpt_peak"][1]:
            pk.append((i, w.fc.max()))
    pk = pd.DataFrame(pk, columns=["issued", "peak"])
    a2.plot(pk.issued, pk.peak, "o-", color="#d1495b", ms=3.5, lw=1, label="Sewells Point, hourly forecast")
    cb_ = cfw[(cfw.location == "CHESAPEAKE BAY BRIDGE TUNNEL VA") & (cfw.time_utc >= "2016-10-08 22:00") & (cfw.time_utc <= "2016-10-09 14:00")]
    cbp = cb_.groupby("issued").total.max().reset_index()
    a2.plot(cbp.issued, cbp.total, "D-", color="#2a9d8f", ms=3.5, lw=1, label="CBBT, tide table")
    a2.axhline(numbers["swpt_peak"][0], color="#d1495b", ls=":", lw=1.2)
    a2.axhline(numbers["cbbt_peak"][0], color="#2a9d8f", ls=":", lw=1.2)
    a2.axvline(numbers["swpt_peak"][1], color="#0b3d91", lw=0.8, ls="--")
    a2.text(numbers["swpt_peak"][1], 5.0, "observed peak ", fontsize=6.3, color="#0b3d91", ha="right", rotation=90, va="bottom")
    a2.set_title("(b) Forecast of the 9 Oct crest", loc="left"); a2.set_ylabel("Peak water level (ft MLLW)")
    a2.xaxis.set_major_locator(mdates.DayLocator()); a2.xaxis.set_major_formatter(mdates.DateFormatter("%d")); a2.set_xlabel("Issue time (UTC, October)"); a2.grid(alpha=0.3)
    a2.legend(fontsize=6.3, loc="upper left"); a2.set_xlim(pd.Timestamp("2016-10-05 12:00"), pd.Timestamp("2016-10-09 12:00"))
    fig.tight_layout(); fig.savefig(OUT / "Fig3_surge.png", dpi=220); plt.close(fig)
    numbers["swpt_peak_fc"] = pk
    numbers["cbbt_peak_fc"] = cbp

    # ---------------- Figure 4: rainfall
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.1), gridspec_kw=dict(width_ratios=[1.3, 1]))
    cum = s[gv.id].cumsum()
    for gid in gv.id:
        a1.plot(cum.index, cum[gid], color="#9ecae1", lw=0.6, alpha=0.8)
    a1.plot(cum.index, cum.median(axis=1), color="#08519c", lw=2, label=f"HRSD median of {len(gv)} gauges")
    a1.fill_between(cum.index, cum.quantile(0.1, axis=1), cum.quantile(0.9, axis=1), color="#6baed6", alpha=0.3, label="HRSD 10-90th percentile")
    # ORF PFM cumulative forecasts for a few issuances (12-h steps), plotted in EDT
    orf = pfm[pfm.point == "NORFOLK INTL AIRPORT-NORFOLK/PORTSMOUTH VA"]
    picks = [pd.Timestamp(x) for x in ("2016-10-06 20:20", "2016-10-07 20:20", "2016-10-08 08:20", "2016-10-08 20:06")]
    cols = ["#fdae61", "#f46d43", "#d73027", "#7f0000"]
    for iss, col in zip(picks, cols):
        q = orf[(orf.issued == iss)].sort_values("period_end")
        q = q[q.period_end <= "2016-10-10 12:00"]
        if not len(q):
            continue
        tt = [q.period_end.iloc[0] - pd.Timedelta(hours=12) - EDT] + list(q.period_end - EDT)
        cc = [0] + list(q.qpf12.cumsum())
        base = cum.median(axis=1).asof(tt[0]) if tt[0] > cum.index[0] else 0
        a1.plot(tt, np.array(cc) + base, color=col, lw=1.3, label=f"NWS ORF QPF, issued {iss:%d Oct %H%MZ}")
    a1.set_xlim(pd.Timestamp("2016-10-07 12:00"), pd.Timestamp("2016-10-10 00:00"))
    a1.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12])); a1.xaxis.set_major_formatter(mdates.DateFormatter("%d Oct\n%H:%M")); a1.set_xlabel("Local time (EDT)")
    a1.set_ylabel("Cumulative rainfall (in)"); a1.grid(alpha=0.3); a1.legend(fontsize=6, loc="upper left")
    a1.set_title("(a) HRSD gauges vs Norfolk point forecasts", loc="left")
    # panel b: per PFM point, HRSD gauges within 10 km vs forecasts
    tot48 = (pfm[pfm.period_end.isin([pd.Timestamp("2016-10-08 12:00"), pd.Timestamp("2016-10-09 00:00"),
                                      pd.Timestamp("2016-10-09 12:00"), pd.Timestamp("2016-10-10 00:00")])]
             .groupby(["point", "issued"]).qpf12.agg(["sum", "size"]).reset_index())
    tot48 = tot48[tot48["size"] == 4]
    tot36 = (pfm[pfm.period_end.isin([pd.Timestamp("2016-10-09 00:00"), pd.Timestamp("2016-10-09 12:00"), pd.Timestamp("2016-10-10 00:00")])]
             .groupby(["point", "issued"]).qpf12.agg(["sum", "size"]).reset_index())
    tot36 = tot36[tot36["size"] == 3]
    rows = []
    for k, (name, lab) in enumerate(HR_PFM.items()):
        la, lo = pfm_pts[name]
        d = np.hypot((gv.lat - la) * 111, (gv.lon - lo) * 111 * np.cos(np.radians(la)))
        near = gv[d <= 10]
        a2.scatter(np.full(len(near), k) + np.random.default_rng(k).uniform(-0.15, 0.15, len(near)), near.total_in, s=12, color="#6baed6", edgecolor="#08519c", lw=0.3, zorder=3)
        f48 = tot48[tot48.point == name].sort_values("issued").iloc[-1]
        f36 = tot36[tot36.point == name].sort_values("issued").iloc[-1]
        a2.plot([k - 0.3, k + 0.3], [f48["sum"]] * 2, color="#f46d43", lw=2.2)
        a2.plot([k - 0.3, k + 0.3], [f36["sum"]] * 2, color="#7f0000", lw=2.2)
        pn = pns[np.hypot((pns.lat - la) * 111, (pns.lon - lo) * 111 * np.cos(np.radians(la))) <= 5]
        rows.append(dict(point=lab, n_hrsd=len(near), hrsd_median=near.total_in.median(), hrsd_max=near.total_in.max(),
                         qpf48_last=f48["sum"], qpf48_issued=f48.issued, qpf36_last=f36["sum"], qpf36_issued=f36.issued,
                         pns_max=pn.inches.max() if len(pn) else np.nan))
    a2.set_xticks(range(len(HR_PFM))); a2.set_xticklabels([v.split(" (")[1].rstrip(")") for v in HR_PFM.values()])
    a2.set_ylabel("Storm total (in)"); a2.grid(alpha=0.3, axis="y")
    a2.legend(handles=[Line2D([], [], marker="o", ls="", mfc="#6baed6", mec="#08519c", ms=4, label="HRSD gauges within 10 km (6-12 Oct)"),
                       Line2D([], [], color="#f46d43", lw=2, label="QPF 8 Oct 00Z-10 Oct 00Z, issued 7 Oct 20Z"),
                       Line2D([], [], color="#7f0000", lw=2, label="QPF 8 Oct 12Z-10 Oct 00Z, issued 8 Oct 08Z")], fontsize=6, loc="upper left")
    a2.set_ylim(0, 18.5); a2.set_title("(b) Gauges vs point QPF", loc="left")
    fig.tight_layout(); fig.savefig(OUT / "Fig4_rainfall.png", dpi=220); plt.close(fig)
    hr = pd.DataFrame(rows)
    hr.to_csv(OUT / "hampton_roads_rain_vs_qpf.csv", index=False, float_format="%.2f")
    numbers["rain"] = hr
    # HRSD intensity summary
    numbers["hrsd"] = dict(n_all=len(g), n_valid=len(gv), n_invalid=int((g.status == "invalid").sum()),
                           n_calib=int((g.status == "calibrated").sum()),
                           median_total=gv.total_in.median(), max_total=gv.total_in.max(),
                           max_gauge=gv.loc[gv.total_in.idxmax(), "label"],
                           n_over_10=int((gv.total_in > 10).sum()),
                           median_event24=gv.event24_in.median(), event24_frac=(gv.event24_in / gv.total_in).median(),
                           max_1h=gv.max_1h_in.max(), max_1h_gauge=gv.loc[gv.max_1h_in.idxmax(), "label"],
                           max_1h_time=gv.loc[gv.max_1h_in.idxmax(), "time_max_1h_edt"],
                           median_max1h=gv.max_1h_in.median(),
                           peak_hour_median=pd.Series(gv.time_max_1h_edt).median())
    return numbers, g


if __name__ == "__main__":
    n, g = main()
    for k, v in n.items():
        print(k, "=>"); print(v if not isinstance(v, pd.DataFrame) else v.round(2).to_string())
