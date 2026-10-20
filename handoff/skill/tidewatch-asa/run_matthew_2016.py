"""Hurricane Matthew (Oct 2016): NWS water level and rainfall forecasts vs observations.

Inputs (input/nws_hml_oct2016/): IEM HML forecasts/obs, AFOS text (CFW tide tables,
PFM point forecasts, PNS storm-total rainfall, TCM advisories), CO-OPS hourly heights
for CBBT (8638863) and Duck (8651370).
Outputs: output/matthew_2016/
"""
import json, re
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.collections import LineCollection

IN = Path(__file__).parent / "input" / "nws_hml_oct2016"
OUT = Path(__file__).parent / "output" / "matthew_2016"
OUT.mkdir(parents=True, exist_ok=True)
NE = Path("/tmp/claude-0/-home-claude-tidewatch-archive/0d1266a8-305e-575f-b964-faff01c18b26/scratchpad/ne")

T0, T1 = pd.Timestamp("2016-10-06"), pd.Timestamp("2016-10-13")
PEAK_WIN = (pd.Timestamp("2016-10-08 12:00"), pd.Timestamp("2016-10-10 12:00"))

# name, lat, lon, flood categories (action, minor, moderate, major; ft MLLW, current NWPS values)
GAUGES = {
    "SWPV2": ("Sewells Point", 36.9467, -76.3300, (4.0, 4.5, 5.5, 6.5)),
    "FTMV2": ("Fort Monroe", 37.0019, -76.3150, (4.0, 4.5, 5.5, 6.5)),
    "YKTV2": ("Yorktown USCG", 37.2267, -76.4783, (4.0, 4.5, 5.0, 5.5)),
    "JSFV2": ("Jamestown/Scotland", 37.2201, -76.7915, (3.5, 4.0, 4.5, 5.0)),
    "KPTV2": ("Kiptopeke", 37.1650, -75.9883, (4.0, 4.5, 5.0, 5.5)),
    "NSWV2": ("Nassawadox Creek (Bayford)", 37.4764, -75.9353, (2.5, 3.0, 3.5, 4.5)),
    "WAHV2": ("Wachapreague", 37.6083, -75.6850, (6.0, 6.5, 7.5, 8.0)),
    "WNDV2": ("Windmill Point", 37.6150, -76.2900, (2.5, 3.0, 3.5, 4.0)),
    "LWTV2": ("Lewisetta", 37.9950, -76.4650, (2.5, 3.0, 3.5, 4.0)),
    "BISM2": ("Bishops Head", 38.2200, -76.0383, (3.0, 3.2, 3.7, 4.2)),
    "SLIM2": ("Solomons Island", 38.3200, -76.4500, (2.5, 3.0, 4.0, 5.0)),
    "NCDV2": ("Upper Machodoc Ck (Dahlgren)", 38.3197, -77.0367, (3.0, 3.5, 5.0, 7.0)),
    "WASD2": ("Washington DC", 38.8733, -77.0211, (4.2, 5.3, 6.5, 7.0)),
    "CBBT": ("Chesapeake Bay Bridge Tunnel", 36.9667, -76.1133, (4.5, 5.0, 5.5, 6.0)),
    "DUCK": ("Duck, NC", 36.1833, -75.7467, (5.0, 5.5, 6.0, 6.5)),
}
OBS_ONLY = {  # HML obs, no forecasts in 2016
    "MNPV2": ("Money Point", 36.78, -76.30), "NMDV2": ("Nansemond R. at Suffolk", 36.7392, -76.5828),
    "ELZV2": ("Elizabeth R. W. Branch", 36.8436, -76.3647), "LHNV2": ("Lynnhaven Inlet", 36.9063, -76.0868),
    "HMNV2": ("Hampton River", 37.0323, -76.3367), "NWRV2": ("Northwest River", 36.5666, -76.1988),
    "BLYV2": ("Bailey Creek (Nansemond)", 36.8057, -76.4087),
}
CFW_NAMES = {  # CFW table heading -> obs key
    "SEWELLS POINT VA": "SWPV2", "YORKTOWN VA": "YKTV2", "FORT MONROE VA": "FTMV2",
    "CHESAPEAKE BAY BRIDGE TUNNEL VA": "CBBT", "JAMES RIVER AT SCOTLAND/JAMESTOWN VA": "JSFV2",
    "DUCK NC": "DUCK", "WACHAPREAGUE VA": "WAHV2", "NASSAWADOX CREEK AT BAYFORD VA": "NSWV2",
    "KIPTOPEKE VA": "KPTV2", "WINDMILL POINT VA": "WNDV2", "LEWISETTA VA": "LWTV2",
    "BISHOPS HEAD MD": "BISM2", "SOLOMONS ISLAND MD": "SLIM2", "DAHLGREN VA": "NCDV2",
}
CFW_ONLY = {  # tide-table locations without archived observations (approximate NOS positions)
    "OYSTER VA": ("Oyster", 37.288, -75.923), "MOBJACK BAY VA": ("Mobjack Bay", 37.36, -76.36),
    "CHINCOTEAGUE INLET VA": ("Chincoteague Inlet", 37.908, -75.405),
    "OCEAN CITY INLET MD": ("Ocean City Inlet", 38.328, -75.091), "CAMBRIDGE MD": ("Cambridge", 38.573, -76.062),
}


# ---------------------------------------------------------------- water level obs
def load_obs():
    obs = {}
    for lid in list(GAUGES) + list(OBS_ONLY):
        f = IN / f"{lid}_obs.csv"
        if f.exists() and f.stat().st_size > 100:
            d = pd.read_csv(f, parse_dates=["valid[UTC]"])
            obs[lid] = d.set_index("valid[UTC]").iloc[:, -1].sort_index()
    for key, fn in (("CBBT", "coops_8638863_CBBT_hourly_MLLW_ft_UTC.csv"), ("DUCK", "coops_8651370_Duck_hourly_MLLW_ft_UTC.csv")):
        d = pd.read_csv(IN / fn)
        obs[key] = pd.Series(d.iloc[:, 1].values, index=pd.to_datetime(d.iloc[:, 0])).sort_index()
    return obs


def load_hml_fc(lid):
    f = IN / f"{lid}_forecasts.csv"
    if not f.exists() or f.stat().st_size < 200:
        return None
    d = pd.read_csv(f, parse_dates=["issued[UTC]", "forecast_valid[UTC]"])
    d = d.rename(columns={"issued[UTC]": "issued", "forecast_valid[UTC]": "valid", "primary_value": "fc"})
    return d[["issued", "valid", "fc"]].drop_duplicates(["issued", "valid"]).sort_values(["issued", "valid"])


def stats(e):
    e = np.asarray(e, float)
    return dict(n=len(e), bias=e.mean(), mae=np.abs(e).mean(), rmse=np.sqrt((e ** 2).mean()))


# ---------------------------------------------------------------- CFW tide tables
ROW = re.compile(r"^\s+(\d{2})/(\d{1,2})\s+(AM|PM)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+(\S+)\s+(\S+)")


def parse_cfw(path):
    text = path.read_text(errors="replace")
    prods = re.split(r"\n(?=\d{3}\s*\nWHUS4\d K\w{3} \d{6})", text)
    rows = []
    for p in prods:
        m = re.search(r"WHUS4\d K\w{3} (\d{2})(\d{2})(\d{2})", p)
        if not m:
            continue
        issued = pd.Timestamp(2016, 10, int(m.group(1)), int(m.group(2)), int(m.group(3)))
        loc = None
        for line in p.splitlines():
            s = line.strip()
            if s in CFW_NAMES or s in CFW_ONLY:
                loc = s
                continue
            r = ROW.match(line)
            if r and loc:
                day, hr, ap = int(r.group(1)), int(r.group(2)), r.group(3)
                hr24 = (hr % 12) + (12 if ap == "PM" else 0)
                # table time is local (EDT); the hour given is the approximate high-tide hour
                t_utc = pd.Timestamp(2016, 10, day, hr24) + pd.Timedelta(hours=4)
                rows.append(dict(issued=issued, location=loc, time_utc=t_utc, total=float(r.group(4)),
                                 astro=float(r.group(5)), surge=float(r.group(6)), category=r.group(8)))
            elif s.startswith("$$"):
                loc = None
    d = pd.DataFrame(rows).drop_duplicates(["issued", "location", "time_utc"])
    return d


# ---------------------------------------------------------------- PFM QPF
def parse_pfm(path):
    text = path.read_text(errors="replace")
    prods = re.split(r"\n(?=\d{3}\s*\nFOUS51 KAKQ \d{6})", text)
    rows, pts = [], {}
    for p in prods:
        m = re.search(r"FOUS51 KAKQ (\d{2})(\d{2})(\d{2})", p)
        if not m:
            continue
        issued = pd.Timestamp(2016, 10, int(m.group(1)), int(m.group(2)), int(m.group(3)))
        for blk in p.split("$$"):
            lines = blk.splitlines()
            ll = [i for i, l in enumerate(lines) if re.match(r"^\s*[\d.]+N\s+[\d.]+W", l)]
            if not ll:
                continue
            i = ll[0]
            name = lines[i - 1].strip()
            lat, lon = re.findall(r"([\d.]+)N\s+([\d.]+)W", lines[i])[0]
            pts[name] = (float(lat), -float(lon))
            dates = [l for l in lines if l.startswith("DATE")]
            utc = [l for l in lines if l.startswith("UTC 3HRLY")]
            qpf = [l for l in lines if l.startswith("QPF 12HR")]
            if not (utc and qpf and dates):
                continue
            uline, qline = utc[0], qpf[0]
            hours = [(mm.start(), mm.end(), int(mm.group())) for mm in re.finditer(r"\d{2}", uline[14:])]
            # build the UTC time of each column from the issue time: columns run forward in 3-h steps
            t = issued.floor("h")
            first = hours[0][2]
            t = t.normalize() + pd.Timedelta(hours=first)
            if t < issued - pd.Timedelta(hours=3):
                t += pd.Timedelta(days=1)
            times = [t + pd.Timedelta(hours=3 * k) for k in range(len(hours))]
            ends = np.array([14 + e for (_, e, _) in hours])
            for tok in re.finditer(r"[\d.]+", qline[14:]):
                k = int(np.argmin(np.abs(ends - (14 + tok.end()))))
                rows.append(dict(issued=issued, point=name, period_end=times[k], qpf12=float(tok.group())))
    return pd.DataFrame(rows).drop_duplicates(["issued", "point", "period_end"]), pts


def parse_pns(path):
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith(":") and ",RAIN," in line:
            f = line[1:].split(",")
            try:
                rows.append(dict(date=f[0], time=f[1], state=f[2], county=f[3], location=f[4],
                                 lat=float(f[7]), lon=float(f[8]), inches=float(f[10]), source=f[12]))
            except (ValueError, IndexError):
                pass
    d = pd.DataFrame(rows)
    return d.sort_values("inches").drop_duplicates(["location", "lat", "lon"], keep="last")


def parse_track(path):
    pts = re.findall(r"CENTER LOCATED NEAR\s+([\d.]+)N\s+([\d.]+)W AT (\d{2})/(\d{2})00Z", path.read_text())
    d = pd.DataFrame([(pd.Timestamp(2016, 10, int(dd), int(hh)), float(a), -float(b)) for a, b, dd, hh in pts],
                     columns=["time", "lat", "lon"]).drop_duplicates("time").sort_values("time")
    return d


# ---------------------------------------------------------------- plotting helpers
CAT_COL = ["#fff3b0", "#ffd166", "#f4845f", "#c77dff"]


def flood_bands(ax, cats, ymax):
    edges = list(cats) + [max(ymax, cats[-1] + 0.5)]
    for k in range(4):
        ax.axhspan(edges[k], edges[k + 1], color=CAT_COL[k], alpha=0.45, lw=0, zorder=0)
    for k, lab in enumerate(["Action", "Minor", "Moderate", "Major"]):
        ax.text(1.002, cats[k], lab, transform=ax.get_yaxis_transform(), fontsize=7, va="bottom", color="#555")


def main():
    obs = load_obs()
    hourly = {k: v[v.index.minute == 0] for k, v in obs.items()}

    # ---- HML gauge forecasts
    lead_rows, iss_rows, fcs = [], [], {}
    for lid in GAUGES:
        fc = load_hml_fc(lid)
        if fc is None or lid not in hourly:
            continue
        fcs[lid] = fc
        o = hourly[lid]
        for iss, g in fc.groupby("issued"):
            g = g.set_index("valid")["fc"]
            j = g.index.intersection(o.index)
            if len(j) < 6:
                continue
            e = g.loc[j] - o.loc[j]
            lead = (j - iss).total_seconds() / 3600
            for t, ee, ld in zip(j, e.values, lead):
                lead_rows.append(dict(lid=lid, issued=iss, valid=t, lead_h=ld, err=ee))
            pk = g[(g.index >= PEAK_WIN[0]) & (g.index <= PEAK_WIN[1])]
            opk = o[(o.index >= PEAK_WIN[0]) & (o.index <= PEAK_WIN[1])]
            s = stats(e.values)
            iss_rows.append(dict(lid=lid, name=GAUGES[lid][0], issued=iss, **s,
                                 fc_peak=pk.max() if len(pk) and pk.index.min() <= opk.idxmax() else np.nan,
                                 fc_peak_time=pk.idxmax() if len(pk) else pd.NaT,
                                 obs_peak=opk.max(), obs_peak_time=opk.idxmax()))
    lead = pd.DataFrame(lead_rows)
    iss = pd.DataFrame(iss_rows)
    lead["bin"] = pd.cut(lead.lead_h, [-1, 12, 24, 36, 48, 60, 73], labels=["0-12", "12-24", "24-36", "36-48", "48-60", "60-72"])
    by_lead = lead.groupby(["lid", "bin"], observed=True).err.agg(
        bias="mean", mae=lambda x: np.abs(x).mean(), rmse=lambda x: np.sqrt((x ** 2).mean()), n="size").reset_index()
    event = lead[(lead.valid >= PEAK_WIN[0]) & (lead.valid <= PEAK_WIN[1])]
    by_lead_event = event.groupby(["lid", "bin"], observed=True).err.agg(
        bias="mean", rmse=lambda x: np.sqrt((x ** 2).mean()), n="size").reset_index()

    # ---- CFW tide tables vs observed high tides
    cfw = pd.concat([parse_cfw(IN / f) for f in ("afos_CFWAKQ.txt", "afos_CFWLWX.txt")], ignore_index=True)
    cfw["obs_key"] = cfw.location.map(CFW_NAMES)
    cfw["lead_h"] = (cfw.time_utc - cfw.issued).dt.total_seconds() / 3600
    obs_hi, obs_hi_t = [], []
    for _, r in cfw.iterrows():
        o = obs.get(r.obs_key)
        if o is None:
            obs_hi.append(np.nan); obs_hi_t.append(pd.NaT); continue
        w = o[(o.index >= r.time_utc - pd.Timedelta(hours=3)) & (o.index <= r.time_utc + pd.Timedelta(hours=3))]
        obs_hi.append(w.max() if len(w) else np.nan)
        obs_hi_t.append(w.idxmax() if len(w) else pd.NaT)
    cfw["obs_high"] = obs_hi
    cfw["obs_high_time"] = obs_hi_t
    cfw["err"] = cfw.total - cfw.obs_high
    cfw_valid = cfw.dropna(subset=["obs_high"])
    cfw_valid = cfw_valid[cfw_valid.lead_h > -2]
    cfw_sum = cfw_valid.groupby("location").err.agg(n="size", bias="mean", mae=lambda x: np.abs(x).mean(),
                                                     rmse=lambda x: np.sqrt((x ** 2).mean())).reset_index()

    # ---- rainfall
    pfm, pfm_pts = parse_pfm(IN / "afos_PFMAKQ.txt")
    pns = parse_pns(IN / "afos_PNSAKQ.txt")
    RW0, RW1 = pd.Timestamp("2016-10-08 00:00"), pd.Timestamp("2016-10-10 00:00")
    ends = [RW0 + pd.Timedelta(hours=12 * k) for k in range(1, 5)]
    tot = (pfm[pfm.period_end.isin(ends)].groupby(["point", "issued"])
           .agg(qpf_total=("qpf12", "sum"), n=("qpf12", "size")).reset_index())
    tot = tot[tot.n == 4]
    tot36 = (pfm[pfm.period_end.isin(ends[1:])].groupby(["point", "issued"])
             .agg(qpf36=("qpf12", "sum"), n=("qpf12", "size")).reset_index())
    tot36 = tot36[tot36.n == 3]
    # observed: nearest report to each PFM point (prefer the airport ASOS within 5 km, else median of reports within 15 km)
    rain_rows = []
    for name, (la, lo) in pfm_pts.items():
        dkm = np.hypot((pns.lat - la) * 111, (pns.lon - lo) * 111 * np.cos(np.radians(la)))
        near = pns[dkm <= 15].assign(dkm=dkm[dkm <= 15])
        asos = near[(near.source.isin(["ASOS", "AWOS"])) & (near.dkm <= 5)]
        if len(asos):
            ob, how = asos.inches.max(), f"ASOS {asos.location.iloc[0]}"
        elif len(near):
            ob, how = near.inches.median(), f"median of {len(near)} reports within 15 km"
        else:
            ob, how = np.nan, "no report within 15 km"
        t = tot[tot.point == name].sort_values("issued")
        last = t.iloc[-1] if len(t) else None
        rain_rows.append(dict(point=name, lat=la, lon=lo, obs_in=ob, obs_source=how,
                              last_full_issue=last.issued if last is not None else pd.NaT,
                              fc_last_in=last.qpf_total if last is not None else np.nan,
                              fc_mean_in=t.qpf_total.mean() if len(t) else np.nan,
                              fc_min_in=t.qpf_total.min() if len(t) else np.nan,
                              fc_max_in=t.qpf_total.max() if len(t) else np.nan, n_issuances=len(t),
                              fc36_last_in=tot36[tot36.point == name].sort_values("issued").qpf36.iloc[-1] if (tot36.point == name).any() else np.nan,
                              fc36_last_issue=tot36[tot36.point == name].issued.max() if (tot36.point == name).any() else pd.NaT))
    rain = pd.DataFrame(rain_rows)
    rain["err_last"] = rain.fc_last_in - rain.obs_in
    track = parse_track(IN / "afos_TCMAT4.txt")

    # ---- save tables
    iss.to_csv(OUT / "gauge_forecast_issuance_stats.csv", index=False, float_format="%.3f")
    by_lead.to_csv(OUT / "gauge_forecast_error_by_lead.csv", index=False, float_format="%.3f")
    cfw.to_csv(OUT / "cfw_tide_table_forecasts_vs_obs.csv", index=False, float_format="%.2f")
    rain.to_csv(OUT / "rainfall_pfm_vs_observed.csv", index=False, float_format="%.2f")
    pns.to_csv(OUT / "observed_storm_total_rainfall_PNS.csv", index=False)
    tot.to_csv(OUT / "pfm_48h_qpf_totals_by_issuance.csv", index=False, float_format="%.2f")
    with pd.ExcelWriter(OUT / "Matthew_2016_NWS_forecast_verification.xlsx") as xw:
        summ = []
        for lid in fcs:
            a = iss[iss.lid == lid]
            ev = event[event.lid == lid]
            summ.append(dict(LID=lid, Gauge=GAUGES[lid][0], issuances=len(a),
                             obs_peak_ft=a.obs_peak.iloc[0], obs_peak_utc=a.obs_peak_time.iloc[0],
                             rmse_all=np.sqrt((lead[lead.lid == lid].err ** 2).mean()),
                             bias_all=lead[lead.lid == lid].err.mean(),
                             rmse_event=np.sqrt((ev.err ** 2).mean()), bias_event=ev.err.mean(),
                             peak_err_mean=(a.fc_peak - a.obs_peak).mean(),
                             peak_err_last_before=(a[a.issued < a.obs_peak_time].sort_values("issued").fc_peak.iloc[-1]
                                                   - a.obs_peak.iloc[0]) if (a.issued < a.obs_peak_time).any() else np.nan))
        summary = pd.DataFrame(summ)
        summary.to_excel(xw, sheet_name="Gauge summary", index=False)
        by_lead.pivot(index="lid", columns="bin", values="rmse").to_excel(xw, sheet_name="RMSE by lead (all)")
        by_lead_event.pivot(index="lid", columns="bin", values="rmse").to_excel(xw, sheet_name="RMSE by lead (8-10 Oct)")
        by_lead_event.pivot(index="lid", columns="bin", values="bias").to_excel(xw, sheet_name="Bias by lead (8-10 Oct)")
        iss.to_excel(xw, sheet_name="Per issuance", index=False)
        cfw_sum.to_excel(xw, sheet_name="Tide tables summary", index=False)
        cfw.to_excel(xw, sheet_name="Tide tables all", index=False)
        rain.to_excel(xw, sheet_name="Rainfall PFM vs obs", index=False)
        tot.to_excel(xw, sheet_name="PFM 48h QPF by issue", index=False)
        tot36.to_excel(xw, sheet_name="PFM 36h QPF by issue", index=False)
        pns.to_excel(xw, sheet_name="PNS rainfall reports", index=False)
    summary.to_csv(OUT / "gauge_summary.csv", index=False, float_format="%.3f")

    # ---- figures: one per gauge
    fig_dir = OUT / "figures"; fig_dir.mkdir(exist_ok=True)
    cmap = plt.get_cmap("plasma")
    for lid, fc in fcs.items():
        name, la, lo, cats = GAUGES[lid]
        o = obs[lid]
        a = iss[iss.lid == lid].sort_values("issued")
        issued = fc.issued.unique()
        tn = (pd.to_datetime(issued) - pd.Timestamp("2016-10-04")) / (pd.Timestamp("2016-10-13 12:00") - pd.Timestamp("2016-10-04"))
        fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw=dict(height_ratios=[2.2, 1]))
        ymax = max(o[(o.index >= T0) & (o.index <= T1)].max(), fc[(fc.valid >= T0) & (fc.valid <= T1)].fc.max()) + 0.6
        flood_bands(ax, cats, ymax)
        for k, (i, g) in enumerate(fc.groupby("issued")):
            ax.plot(g.valid, g.fc, color=cmap(np.clip(tn[k], 0, 0.95)), lw=1.0, alpha=0.75, zorder=2)
        ax.plot(o.index, o.values, color="#0b3d91", lw=2.4, zorder=3, label="Observed (NWS/NOS gauge)")
        ax.set_xlim(T0, T1); ax.set_ylim(min(-0.5, o[(o.index >= T0)].min() - 0.3), ymax)
        ax.set_ylabel("Water level (ft above MLLW)")
        ax.set_title(f"{name} ({lid}): NWS hourly water level forecasts vs observations, Hurricane Matthew 2016", loc="left", fontsize=12)
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=matplotlib.colors.Normalize(mdates.date2num(pd.Timestamp("2016-10-04")), mdates.date2num(pd.Timestamp("2016-10-13 12:00"))))
        cb = fig.colorbar(sm, ax=ax, pad=0.06, fraction=0.03)
        cb.set_label("Forecast issued (UTC)")
        cb.ax.yaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.legend(loc="upper left", fontsize=9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H UTC")); ax.grid(alpha=0.3)
        # panel b: forecast peak (8-10 Oct) by issue time
        aa = a.dropna(subset=["fc_peak"])
        ax2.axhline(a.obs_peak.iloc[0], color="#0b3d91", lw=2, label=f"Observed peak {a.obs_peak.iloc[0]:.2f} ft ({a.obs_peak_time.iloc[0]:%d %b %H:%M} UTC)")
        ax2.axvline(a.obs_peak_time.iloc[0], color="#0b3d91", ls=":", lw=1)
        ax2.plot(aa.issued, aa.fc_peak, "o-", color="#d1495b", ms=5, label="Forecast peak for 8-10 Oct, by issuance")
        for k, labl in enumerate(["Action", "Minor", "Moderate", "Major"]):
            ax2.axhline(cats[k], color=CAT_COL[k], lw=1.5)
        ax2.set_xlim(pd.Timestamp("2016-10-04"), T1)
        ax2.set_ylabel("Peak water level (ft MLLW)"); ax2.grid(alpha=0.3)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d")); ax2.set_xlabel("Forecast issue time (UTC)")
        ax2.legend(fontsize=8, loc="upper left")
        ev = event[event.lid == lid]
        ax2.text(0.99, 0.05, f"8-10 Oct hourly errors, all issuances: bias {ev.err.mean():+.2f} ft, RMSE {np.sqrt((ev.err**2).mean()):.2f} ft",
                 transform=ax2.transAxes, ha="right", fontsize=9, bbox=dict(fc="white", ec="#ccc"))
        fig.tight_layout()
        fig.savefig(fig_dir / f"Matthew2016_{lid}_waterlevel_forecasts.png", dpi=150)
        plt.close(fig)

    # CFW tide-table figure for every location with observations (incl. CBBT and Duck)
    locs = [l for l in CFW_NAMES if l in set(cfw_valid.location)]
    n = len(locs); ncol = 3; nrow = int(np.ceil(n / ncol))
    fig, axs = plt.subplots(nrow, ncol, figsize=(16, 4.1 * nrow), squeeze=False)
    for ax, loc in zip(axs.flat, locs):
        key = CFW_NAMES[loc]
        o = obs[key]; c = cfw_valid[cfw_valid.location == loc]
        cats = GAUGES[key][3]
        flood_bands(ax, cats, max(o[(o.index >= T0) & (o.index <= T1)].max(), c.total.max()) + 0.5)
        ax.plot(o.index, o.values, color="#0b3d91", lw=1.6)
        sc = ax.scatter(c.time_utc, c.total, c=c.lead_h.clip(0, 72), cmap="viridis_r", s=26, edgecolor="k", lw=0.4, zorder=4)
        ax.set_xlim(T0, T1); ax.set_ylim(bottom=max(-1, o[(o.index >= T0)].min() - 0.3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d")); ax.grid(alpha=0.3)
        s = cfw_sum[cfw_sum.location == loc].iloc[0]
        short = GAUGES[key][0]
        ax.set_title(f"{short}\nn={s.n} high tides, bias {s.bias:+.2f} ft, RMSE {s.rmse:.2f} ft", fontsize=9.5, loc="left")
    for ax in axs.flat[n:]:
        ax.axis("off")
    fig.subplots_adjust(right=0.9)
    cb = fig.colorbar(sc, cax=fig.add_axes([0.93, 0.35, 0.012, 0.3])); cb.set_label("Lead time (h)")
    fig.suptitle("NWS coastal flood tide-table forecasts (dots, high tides) vs observed water level, October 2016 (UTC, ft MLLW)", x=0.01, y=0.995, ha="left", fontsize=13)
    fig.subplots_adjust(hspace=0.45, top=0.95)
    fig.savefig(fig_dir / "Matthew2016_tide_table_forecasts_all_locations.png", dpi=140, bbox_inches="tight")
    plt.close(fig)

    # RMSE by lead time, all gauges
    fig, axs = plt.subplots(1, 2, figsize=(14, 5.2))
    for lid in fcs:
        b = by_lead[by_lead.lid == lid]; be = by_lead_event[by_lead_event.lid == lid]
        axs[0].plot(b.bin.astype(str), b.rmse, "o-", label=f"{lid} {GAUGES[lid][0]}")
        axs[1].plot(be.bin.astype(str), be.bias, "o-")
    axs[0].set_title("RMSE vs lead time, all hours 4-13 Oct", loc="left"); axs[0].set_ylabel("ft")
    axs[1].set_title("Bias vs lead time, valid 8 Oct 12Z - 10 Oct 12Z (storm)", loc="left"); axs[1].axhline(0, color="k", lw=0.8)
    for ax in axs:
        ax.set_xlabel("Lead time (h)"); ax.grid(alpha=0.3)
    axs[0].legend(fontsize=7, ncol=2)
    fig.tight_layout(); fig.savefig(fig_dir / "Matthew2016_error_by_lead_time.png", dpi=150); plt.close(fig)

    # rainfall: forecast vs observed scatter + lead-time evolution
    fig, axs = plt.subplots(1, 2, figsize=(15, 6.3))
    r = rain.dropna(subset=["obs_in", "fc_last_in"])
    axs[0].errorbar(r.obs_in, r.fc_mean_in, yerr=[r.fc_mean_in - r.fc_min_in, r.fc_max_in - r.fc_mean_in], fmt="none", ecolor="#bbb", lw=1)
    axs[0].scatter(r.obs_in, r.fc_last_in, color="#d1495b", s=40, zorder=3, label="Last forecast issued before 8 Oct 00Z")
    axs[0].scatter(r.obs_in, r.fc_mean_in, color="#2e86ab", s=18, zorder=3, label="Mean of all issuances (bars: range)")
    for _, q in r.iterrows():
        axs[0].annotate(q.point.split("-")[0].split("/")[0].title()[:22], (q.obs_in, q.fc_last_in), fontsize=7, xytext=(3, 3), textcoords="offset points")
    lim = max(r.obs_in.max(), r.fc_max_in.max()) + 1
    axs[0].plot([0, lim], [0, lim], "k--", lw=0.8); axs[0].set_xlim(0, lim); axs[0].set_ylim(0, lim)
    axs[0].set_xlabel("Observed storm total (in), PNS reports"); axs[0].set_ylabel("Forecast QPF 8 Oct 00Z - 10 Oct 00Z (in)")
    axs[0].set_title("NWS Wakefield point rainfall forecasts vs observed", loc="left"); axs[0].legend(fontsize=8); axs[0].grid(alpha=0.3)
    for name in r.sort_values("obs_in", ascending=False).point.head(8):
        t = tot36[tot36.point == name].sort_values("issued")
        ob = r[r.point == name].obs_in.iloc[0]
        l, = axs[1].plot(t.issued, t.qpf36 - ob, "o-", ms=3, label=f"{name.split('-')[0].title()[:28]} (obs {ob:.1f} in)")
    axs[1].axhline(0, color="k", lw=0.8)
    axs[1].set_title("QPF for 8 Oct 12Z - 10 Oct 00Z minus observed storm total, by issue time", loc="left")
    axs[1].set_ylabel("in"); axs[1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H UTC")); axs[1].grid(alpha=0.3)
    axs[1].legend(fontsize=7)
    fig.tight_layout(); fig.savefig(fig_dir / "Matthew2016_rainfall_forecast_vs_observed.png", dpi=150); plt.close(fig)

    # ---- map
    make_map(rain, pns, pfm_pts, track, cfw, fig_dir)
    return summary, cfw_sum, rain, by_lead_event


def _geo(name):
    return json.loads((NE / f"{name}.geojson").read_text())["features"]


def _draw(ax, feats, kind, **kw):
    for f in feats:
        g = f["geometry"]
        if g is None:
            continue
        polys = g["coordinates"] if g["type"] in ("Polygon", "MultiLineString") else (
            [c for p in g["coordinates"] for c in p] if g["type"] == "MultiPolygon" else [g["coordinates"]])
        for ring in polys:
            a = np.asarray(ring)
            if a.ndim != 2 or (a[:, 0].max() < -79.5 or a[:, 0].min() > -72) or (a[:, 1].max() < 33 or a[:, 1].min() > 40):
                continue
            if kind == "fill":
                ax.fill(a[:, 0], a[:, 1], **kw)
            else:
                ax.plot(a[:, 0], a[:, 1], **kw)


LABEL_OFF = {"NCDV2": (-10, -14), "SLIM2": (8, 4), "YKTV2": (6, 6), "JSFV2": (-60, -14), "SWPV2": (-80, -10),
             "FTMV2": (-74, 2), "CBBT": (8, -2), "NSWV2": (8, -4)}


def make_map(rain, pns, pfm_pts, track, cfw, fig_dir):
    fig, ax = plt.subplots(figsize=(13, 12.5))
    ax.set_facecolor("#dbe9f6")
    _draw(ax, _geo("ne_10m_land"), "fill", color="#f2efe9", lw=0)
    _draw(ax, _geo("ne_10m_minor_islands"), "fill", color="#f2efe9", lw=0)
    _draw(ax, _geo("ne_10m_coastline"), "line", color="#7a8b99", lw=0.6)
    _draw(ax, _geo("ne_10m_admin_1_states_provinces_lines"), "line", color="#999", lw=0.8, ls="--")
    # observed rainfall
    sc = ax.scatter(pns.lon, pns.lat, c=pns.inches, cmap="YlGnBu", vmin=0, vmax=12, s=14, alpha=0.8, lw=0, zorder=2)
    cb = fig.colorbar(sc, ax=ax, fraction=0.03, pad=0.01, shrink=0.6); cb.set_label("Observed storm-total rainfall, 9 Oct 2016 PNS reports (in)")
    # PFM points
    for name, (la, lo) in pfm_pts.items():
        ax.plot(lo, la, "s", ms=7, mfc="none", mec="#6a4c93", mew=1.4, zorder=4)
    # track
    tr = track[(track.lon > -80) & (track.lat > 32)]
    ax.plot(tr.lon, tr.lat, "-", color="#c1121f", lw=2, zorder=3)
    for _, q in tr.iterrows():
        ax.plot(q.lon, q.lat, "o", color="#c1121f", ms=4, zorder=3)
        if True:
            ax.annotate(f"{q.time:%d/%H}Z", (q.lon, q.lat), fontsize=7, color="#c1121f", xytext=(4, -8), textcoords="offset points")
    # gauges
    for lid, (name, la, lo, _) in GAUGES.items():
        hml = lid not in ("CBBT", "DUCK")
        ax.plot(lo, la, "^" if hml else "D", ms=10 if hml else 9, mfc="#ef8a17" if hml else "#2a9d8f", mec="k", zorder=6)
        ax.annotate(name, (lo, la), fontsize=8, xytext=LABEL_OFF.get(lid, (6, 2)), textcoords="offset points", zorder=7,
                    bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.6))
    for lid, (name, la, lo) in OBS_ONLY.items():
        ax.plot(lo, la, "o", ms=6, mfc="white", mec="k", zorder=5)
    for key, (name, la, lo) in CFW_ONLY.items():
        ax.plot(lo, la, "D", ms=7, mfc="#90e0ef", mec="k", zorder=5)
        ax.annotate(name, (lo, la), fontsize=7, xytext=(6, -8), textcoords="offset points", color="#035")
    from matplotlib.lines import Line2D
    h = [Line2D([], [], marker="^", ls="", mfc="#ef8a17", mec="k", ms=10, label="Hourly NWS water level forecasts (HML) + tide tables"),
         Line2D([], [], marker="D", ls="", mfc="#2a9d8f", mec="k", ms=9, label="Tide-table forecasts only; NOS observations (CBBT, Duck)"),
         Line2D([], [], marker="D", ls="", mfc="#90e0ef", mec="k", ms=7, label="Tide-table forecasts only; no archived observations"),
         Line2D([], [], marker="o", ls="", mfc="white", mec="k", ms=6, label="Observations only (no forecasts archived)"),
         Line2D([], [], marker="s", ls="", mfc="none", mec="#6a4c93", mew=1.4, ms=7, label="NWS Wakefield point forecast (rainfall QPF)"),
         Line2D([], [], color="#c1121f", marker="o", ms=4, lw=2, label="Matthew center (NHC advisories)")]
    ax.legend(handles=h, loc="lower right", fontsize=8, framealpha=0.95)
    ax.set_xlim(-79.0, -72.8); ax.set_ylim(33.6, 39.05); ax.set_aspect(1 / np.cos(np.radians(37.3)))
    ax.set_title("Hurricane Matthew, 4-14 Oct 2016: NWS forecast locations in coastal Virginia, Maryland and NE North Carolina", loc="left", fontsize=11)
    ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(fig_dir / "Matthew2016_station_map.png", dpi=170); plt.close(fig)


if __name__ == "__main__":
    s, c, r, b = main()
    pd.set_option("display.width", 250)
    print(s.round(3).to_string()); print(c.round(3).to_string())
    print(r[["point", "obs_in", "obs_source", "fc_last_in", "fc_mean_in", "fc_min_in", "fc_max_in", "n_issuances", "last_full_issue"]].round(2).to_string())
    print(b.round(3).to_string())
