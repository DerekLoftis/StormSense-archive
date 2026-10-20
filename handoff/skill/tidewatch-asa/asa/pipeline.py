"""One ASA run: score archived forecasts, then write every product."""
import logging
from pathlib import Path

import pandas as pd

from . import config, data, deck, metrics, plots, runfiles, tables
from .data import station_name

log = logging.getLogger("asa")


def _fill_from_services(forecasts, obs, archive_root, datum):
    """Optional NOAA/USGS fill for windows the archive does not cover."""
    by_station = {}
    for f in forecasts:
        by_station.setdefault(f.station, []).append(f)
    for station, fs in by_station.items():
        have = obs.get(station, pd.Series(dtype=float))
        missing = [f for f in fs if data.coverage(f, data.pair(f, have)) < 1]
        if not missing:
            continue
        start = min(f.start for f in missing)
        end = min(max(f.end for f in missing), pd.Timestamp.now(tz="UTC"))
        try:
            fetched = data.fetch_observations(station, start, end)
        except Exception as e:  # network or service errors leave the archive as is
            log.warning("fetch %s failed: %s", station, e)
            continue
        if fetched.empty:
            continue
        info = data.STATION_INFO.get(station, {})
        if info.get("source") == "USGS" and datum == "MLLW":
            run = missing[-1].cycle_path
            ref = runfiles.glob(run, f"{station}_*_f36_*.csv")
            m = [n for n in ref if n.endswith("_MLLW.csv")]
            nv = [n for n in ref if n.endswith("_NAVD.csv")]
            if not (m and nv):
                continue
            fetched = fetched + data.datum_offset(runfiles.open_text(run, m[0]), runfiles.open_text(run, nv[0]))
        fetched = fetched[fetched.index.minute % 6 == 0]
        obs[station] = have.combine_first(fetched).sort_index()
    return obs


def score_all(forecasts, obs, previous):
    """History of statistics for every forecast with enough observations.

    Rows already in `previous` with full coverage are kept as they are; the
    rest are recomputed, since later 3d snapshots can fill them in.
    """
    keep = pd.DataFrame()
    done = set()
    # Rows scored before the 0-1 Tidewatch Skill formula (no SDobs) are redone.
    if not previous.empty and "SDobs" in previous.columns:
        keep = previous[previous["Coverage"] >= 0.999]
        done = set(zip(keep["station"], keep["issued"]))
    rows = []
    for f in forecasts:
        if (f.station, f.issued) in done:
            continue
        o = obs.get(f.station)
        p = data.pair(f, o)
        cov = data.coverage(f, p)
        if cov < config.MIN_COVERAGE:
            continue
        s = metrics.statistics(p)
        if pd.isna(s["R2"]):
            continue
        scale = metrics.site_scale(o, f.start)
        comp_g = metrics.components(s, s["SDobs"])
        comp_s = metrics.components(s, scale)
        rows.append({
            "station": f.station, "issued": f.issued, "start": f.start, "end": f.end,
            "archive_cycle": f.archive_cycle, "Coverage": round(cov, 4), **s,
            "SiteScale": scale,
            **{f"g_{k}": v for k, v in comp_g.items()},
            **{f"s_{k}": v for k, v in comp_s.items()},
        })
    new = pd.DataFrame(rows)
    hist = pd.concat([keep, new], ignore_index=True) if not keep.empty else new
    if hist.empty:
        return hist
    for c in ("start", "end"):
        hist[c] = pd.to_datetime(hist[c], utc=True)
    hist = hist.sort_values(["station", "issued"]).drop_duplicates(["station", "issued"], keep="last")
    return _apply_skill(hist)


def _apply_skill(hist):
    """Tidewatch Skill (Derek's formula: equal weights, sigma = window SD of
    observations) and Site Skill (same exp components with the station's
    30-day SD as sigma, CRITIC weights calibrated on its earlier forecasts)."""
    hist = hist.copy()
    w = config.EQUAL_WEIGHTS
    hist["Skill"] = sum(hist[f"g_{k}"] * w[k] for k in metrics.METRICS) / sum(w.values())
    site, wcols = [], {k: [] for k in metrics.METRICS}
    for _, g in hist.groupby("station", sort=False):
        g = g.sort_values("issued")
        comp = g[[f"s_{k}" for k in metrics.METRICS]].copy()
        comp.columns = metrics.METRICS
        for i in range(len(g)):
            weights, _ = metrics.critic_weights(comp.iloc[: i + 1])
            row = comp.iloc[i]
            site.append((g.index[i], sum(row[k] * weights[k] for k in metrics.METRICS)))
            for k in metrics.METRICS:
                wcols[k].append((g.index[i], weights[k]))
    hist["SiteSkill"] = pd.Series(dict(site))
    for k in metrics.METRICS:
        hist[f"w_{k}"] = pd.Series(dict(wcols[k]))
    return hist


def pick_target(hist):
    """Most recent issuance scored at at least half of the stations."""
    if hist.empty:
        return None
    counts = hist.groupby("issued")["station"].nunique()
    best = counts.max()
    ok = counts[counts >= max(1, best // 2)]
    return ok.index.max()


def _row(r):
    return {
        "Issued": r["issued"].strftime("%m/%d %H:%MZ"),
        "Period": f"{r['start']:%m/%d %H:%M}–{r['end']:%m/%d %H:%M}",
        "Coverage": r["Coverage"], "R2": r["R2"], "Bias": r["Bias"], "MAE": r["MAE"],
        "RMSE": r["RMSE"], "Skill": r["Skill"], "SiteSkill": r["SiteSkill"],
    }


def run(archive_root=None, output_root=None, target=None, datum=None, stations=None,
        overlap_windows=(3, 30), make_deck=True):
    archive_root = data.archive_roots(archive_root)
    output_root = Path(output_root or config.OUTPUT_ROOT)
    datum = datum or config.DATUM
    forecasts = data.load_forecasts(archive_root, datum, stations)
    obs = data.load_observations(archive_root, datum)
    if config.FETCH_MISSING_OBSERVATIONS:
        obs = _fill_from_services(forecasts, obs, archive_root, datum)
    log.info("%d forecasts, observations for %d stations", len(forecasts), len(obs))

    history_path = output_root / "history" / tables.HISTORY_FILE
    hist = score_all(forecasts, obs, tables.load_history(history_path))
    if hist.empty:
        log.warning("No forecast has %.0f%% observation coverage yet", config.MIN_COVERAGE * 100)
        return None
    tables.save_history(hist, history_path)

    if target is None:
        target = pick_target(hist)
    target = pd.Timestamp(target)
    target = target.tz_localize("UTC") if target.tzinfo is None else target.tz_convert("UTC")
    cyc = hist[hist["issued"] == target]
    if cyc.empty:
        raise SystemExit(f"No scored forecasts issued at {target}")
    stamp = target.strftime("%Y-%m-%d_%H%MZ")
    out = output_root / "cycles" / stamp
    for sub in ("plots", "tables", "validation"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    fc_index = {(f.station, f.issued): f for f in forecasts}

    station_items = []
    for _, r in cyc.sort_values("station").iterrows():
        st = r["station"]
        f = fc_index[(st, target)]
        o = obs.get(st)
        p = data.pair(f, o)
        s = r.to_dict()
        ts_png = out / "plots" / f"{st}_{stamp}_timeseries.png"
        rg_png = out / "plots" / f"{st}_{stamp}_regression.png"
        title = f"{st} · {station_name(st)} · issued {target:%Y-%m-%d %H:%MZ}"
        plots.timeseries(p, f, o, title, ts_png, datum)
        plots.regression(p, s, r["Skill"], r["SiteSkill"], f"{st} · issued {target:%m/%d %H:%MZ}", rg_png, datum)
        tables.write_station_table(hist, st, target, out / "tables")
        recent = tables.to_table(hist[(hist["station"] == st)
                                      & (hist["issued"] > target - pd.Timedelta(days=config.SLIDE_TABLE_DAYS))
                                      & (hist["issued"] <= target)])
        station_items.append({"station": st, "timeseries_png": ts_png, "regression_png": rg_png,
                              "recent": recent})

        for days in overlap_windows:
            w0 = target - pd.Timedelta(days=days)
            sel = hist[(hist["station"] == st) & (hist["issued"] > w0) & (hist["issued"] <= target)]
            items = []
            for _, rr in sel.sort_values("issued").iterrows():
                ff = fc_index.get((st, rr["issued"]))
                if ff is None:
                    continue
                items.append({"forecast": ff, "paired": data.pair(ff, o), "row": _row(rr)})
            if not items:
                continue
            label = f"past {days} days"
            plots.overlap_timeseries(
                items, o, w0, target + pd.Timedelta(hours=config.FORECAST_HOURS),
                f"{st} · {station_name(st)} · 36-h forecasts issued in the {label} (to {target:%m/%d %H:%MZ})",
                out / "validation" / f"{st}_{stamp}_{days}d_overlap.png", datum)
            plots.overlap_regression(
                items, w0, target, f"{st} · 36-h forecast validation, {label}",
                out / "validation" / f"{st}_{stamp}_{days}d_regression.png", datum)
            pd.DataFrame([i["row"] for i in items]).to_csv(
                out / "validation" / f"{st}_{stamp}_{days}d_stats.csv", index=False)

    agg = tables.write_aggregate(hist, target, out)
    if make_deck:
        deck.build(target, station_items, agg, out / f"Tidewatch_ASA_{stamp}.pptx")

    # Month-end station archives for every finished month not yet written.
    months = sorted({(t.year, t.month) for t in hist["issued"]})
    now = pd.Timestamp.now(tz="UTC")
    monthly_dir = output_root / "monthly"
    for y, m in months:
        if (y, m) >= (now.year, now.month):
            continue
        marker = monthly_dir / f".done_{y}_{m:02d}"
        if not marker.exists():
            tables.write_monthly(hist, y, m, monthly_dir)
            marker.touch()
    log.info("Wrote %s", out)
    return out
