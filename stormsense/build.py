"""One StormSense run: read every gauge and every forecast, make StormSense's own forecast, score
the archive, and write both public sites and the run's archive.

    python -m stormsense.build [--latest site/latest] [--ensemble site/ensemble] [--work work]
                               [--rescore-all] [--stations SWPT,USJT]

  1. NWS flood categories (NWPS), every station's own observations (observe.py: CO-OPS, USGS,
     NWPS, the VIMS THREDDS gauge, FloodVISION, GreenStream; 40 days, no web4)
  2. StormSense's harmonic tide per gauge locked to m30 (tide.py; stored fit or fitted now)
  3. members: P-ETSS, ETSS, STOFS-2D/3D, CBOFS, NWM, NWS official, NHC P-Surge, Tidewatch's 36-hour
     forecast (the one thing read from web4), tide + persistence, and the StormSense model
  4. skill.py scores the archived runs and picks each station's guidance per lead band
  5. the StormSense Latest forecast: m30 tide + the picked guidance's residual (station.primary),
     its cone and flood-stage chances from its own archived errors (uncertainty.py), XHW labels
  6. sites: latest/ (fore72, 7day, 30day) and ensemble/ (ens72, skill/), html + csv + json
  7. archive: work/archive/ensemble_<cycle>.tar.gz (ensemble-YYYY-MM release), the fore72 history
     (stormsense-YYYY-MM release), the cumulative skill scores; work/archive/assets.txt lists them
"""
import argparse
import json
import os
import shutil
import tarfile
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import (archive as ARC, charts as C, corrector, members_run as MR, observe, products as P, releases,
               skill as SK, station as STN, stations as S, surge as SURGE, thresholds as THR, tide as TIDE,
               uncertainty as U)

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = "00:20, 06:20, 12:20 and 18:20 UTC"  # keep in step with the cron-job.org schedule that starts stormsense.yml
LATEST_URL = "https://derekloftis.github.io/StormSense-latest/"
ENSEMBLE_URL = "https://derekloftis.github.io/StormSense-ensemble/"
MAX_OBS_AGE = timedelta(hours=12)
HISTORY = "fore72_history.csv.gz"


def guidance_setting():
    """data/guidance.json: {"mode": "adaptive"} follows the skill pick; {"mode": "override",
    "override": "petss"} forces one guidance everywhere."""
    p = os.path.join(HERE, "data", "guidance.json")
    return json.load(open(p)) if os.path.exists(p) else {"mode": "adaptive"}


def cycle_of(now):
    c = now.replace(minute=0, second=0, microsecond=0)
    return c - timedelta(hours=c.hour % 6)


# --- tide ------------------------------------------------------------------------------------------
def tide_for(st, info, ot, ov, t0):
    """(tide function on times, m30 ft MLLW or None, description) for one station."""
    coef, how = None, None
    try:
        coef, how = TIDE.load(st), "stored harmonic fit"
    except FileNotFoundError:
        try:
            coef, how = TIDE.fit_from_obs(st, ot, ov, info["lat_f"] or 37.0), "fitted to this run's 40 days"
        except Exception as e:
            print(f"  {st}: tide fit failed ({e})")
    if coef is None:
        return None, None, None
    level, used = TIDE.m30(ot, ov, t0)
    shift = None
    if not np.isnan(level) and len(used) >= 10 * 24 * TIDE.M30_DAYS / 2:
        shift = level - float(np.mean(TIDE.predict(coef, list(used))))
    add = shift or 0.0
    return (lambda times: TIDE.predict(coef, list(times)) + add), (level if shift is not None else None), \
        f"StormSense harmonic tide ({how}) locked to m30" if shift is not None else f"StormSense harmonic tide ({how})"


# --- the StormSense model member --------------------------------------------------------------------
def stormsense_member(st, grid, t0, cols, surges, notes, rmse, past, truth, tide_fn):
    try:
        out, note = SURGE.forecast(grid, t0, STN.model_cols(cols, surges), cols["astro_tide"], rmse or None)
        if np.isnan(out["stormsense"]).all():
            return
        raw = out["stormsense"].copy()
        info = {"applied": False, "runs": 0}
        if past:
            def predict(g, t, c):
                td = tide_fn(g)
                c2 = dict(c)
                for k in ("petss", "etss"):
                    if f"{k}_surge" in c:
                        c2[k] = td + c[f"{k}_surge"]
                return SURGE.forecast(g, t, c2, td, rmse or None)[0]["stormsense"], td
            coefs, info = corrector.correction_for(st, past, {st: truth}, t0, predict)
            corrector.apply(grid, t0, out, cols["astro_tide"], coefs)
        cols.update(out)
        cols["stormsense_raw"] = raw
        notes["stormsense"] = dict(note, tide="StormSense m30 tide", corrector=info,
                                   run=t0.strftime("%Y-%m-%dT%H:%MZ"), aligned_ft=None)
    except Exception as e:
        print(f"  {st}: stormsense model unavailable ({e})")


# --- uncertainty history ---------------------------------------------------------------------------
def load_history(work, table, now):
    """The fore72 history: the newest fore72_history.csv.gz on the stormsense-YYYY-MM releases, or one
    rebuilt from the archived StormSense Latest builds of the last WINDOW_DAYS."""
    path = os.path.join(work, HISTORY)
    try:
        url = releases.newest_asset("stormsense-", HISTORY, (releases.PRIVATE,))
        if url:
            releases.download(url, path)
            h = U.read(path)
            print(f"fore72 history: {h['cycle'].nunique()} runs")
            return h
    except Exception as e:
        print(f"fore72 history unavailable ({e})")
    rows = []
    try:
        since = now - timedelta(days=U.WINDOW_DAYS + 3)
        for tag, assets in releases.assets("stormsense-", (releases.PRIVATE,)).items():
            for name, url in assets.items():
                if name.startswith("stormsense_") and name.endswith(".tar.xz"):
                    try:
                        t = datetime.strptime(name[11:-7], "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                    if t < since:
                        continue
                    p = releases.download(url, os.path.join(work, "latest_runs", name))
                    rows += U.bootstrap_rows(p, table)
    except Exception as e:
        print(f"fore72 history bootstrap failed ({e})")
    h = U.append(U.read(None), rows)
    print(f"fore72 history rebuilt from the archived builds: {h['cycle'].nunique() if len(h) else 0} runs")
    return h


def cone_and_chances(model, lead, prim, cols, levels):
    fc = prim["SSforecast"]
    if model is not None and model.ok:
        lo, hi = model.cone(lead, fc)
        lo, hi = np.where(np.abs(lead) < 0.01, fc, lo), np.where(np.abs(lead) < 0.01, fc, hi)
        ch = {k: model.chance(lead, fc, v) for k, v in levels.items()}
        d = model.describe()
        label = (f"10%–90% of StormSense's own past errors at this lead time ({d['trajectories']} forecasts, "
                 f"{d['own']} at this station, last {d['window_days']} days)")
        return lo, hi, label, {k: v for k, v in ch.items() if v is not None}
    fut = lead >= 0
    if "petss_p10" in cols and "petss" in cols:  # P-ETSS's spread around our forecast until there is a record
        lo = np.where(fut, fc - np.nan_to_num(cols["petss"] - cols["petss_p10"]), np.nan)
        hi = np.where(fut, fc + np.nan_to_num(cols["petss_p90"] - cols["petss"]), np.nan)
        return lo, hi, "P-ETSS 10%–90% spread (too few verified StormSense forecasts yet)", {}
    s = SURGE.Z90 * (SURGE.SIGMA0 + SURGE.SIGMA_SLOPE * np.clip(lead, 0, None))
    return (np.where(fut, fc - s, np.nan), np.where(fut, fc + s, np.nan),
            "an error growing with lead time (too few verified StormSense forecasts yet)", {})


def guidance_line(used, pick_doc, setting):
    parts = ", ".join(f"{STN.NAMES.get(used.get(b), used.get(b) or 'persistence')} {b} h" for b, _, _ in STN.BANDS)
    if setting.get("mode") == "override":
        why = f"fixed {STN.NAMES.get(setting.get('override'), setting.get('override'))} override"
    elif pick_doc and pick_doc.get("ready_stations"):
        why = "adaptive pick by recent skill"
    else:
        why = "adaptive pick: P-ETSS until a week of skill history exists"
    return f"Forecast guidance: {parts} ({why}) on the StormSense m30 tide"


# --- index pages -------------------------------------------------------------------------------------
STYLE = """body{font-family:Arial,'Liberation Sans',sans-serif;margin:0 auto;padding:16px;max-width:1100px;color:#1b2533;background:#fff}
header img{width:min(520px,100%);height:auto;display:block}
h1{font-size:1.5rem;margin:12px 0 4px;color:#14285a}
p,li{margin:6px 0;line-height:1.45}
.disc{color:#555;font-size:.92rem}
.wrap{overflow-x:auto;margin-top:14px}
table{border-collapse:collapse;font-size:.92rem}
th,td{border:1px solid #d5dbe3;padding:4px 8px;text-align:left;white-space:nowrap}
thead th{background:#eef6fa;text-align:center}
.code{font-family:ui-monospace,monospace;font-weight:normal;color:#555}
a{color:#0d6e8a}
td.small{font-size:5pt;line-height:1.25;white-space:normal;width:18em;min-width:18em;max-width:18em}
td.small.src{overflow-wrap:anywhere}"""
DISC = ('<p class="disc">Disclaimer: StormSense&trade; Predictive Plots are computer-generated operational storm tide '
        'forecasts; the user assumes any and all risk.</p>')


def links(site, prod, st, datum):
    out = []
    for ext, lab in (("html", "chart"), ("csv", "csv"), ("json", "json")):
        if os.path.exists(os.path.join(site, prod, f"{st}_{prod}_{datum}.{ext}")):
            out.append(f'<a href="{prod}/{st}_{prod}_{datum}.{ext}">{lab}</a>')
    return " ".join(out)


def latest_index(site, table, built, missing, thr, updated):
    import html as H
    prods = (("fore72", "72-hour forecast"), ("7day", "7-day observations"), ("30day", "30-day observations"))
    head1 = "".join(f'<th colspan="2">{lab}<br><span class="code">{p}</span></th>' for p, lab in prods)
    head2 = "".join("<th>MLLW</th><th>NAVD88</th>" for _ in prods)
    rows = []
    for st in sorted(built):
        cells = "".join(f"<td>{links(site, p, st, d)}</td>" for p, _ in prods for d in ("MLLW", "NAVD"))
        t = thr.get(st)
        nws = f'<a href="https://water.noaa.gov/gauges/{t["lid"]}">{t["lid"]}</a>' if t else ""
        src = H.escape(built[st])
        src = f'<td class="small src">{src}</td>' if len(built[st]) > 20 else f"<td>{src}</td>"
        rows.append(f"<tr><th>{st}</th><td class=\"small\">{H.escape(table[st]['name'])}</td>{cells}<td>{nws}</td>{src}</tr>")
    miss = "".join(f"<li>{st} ({H.escape(table[st]['name'])}): {H.escape(why)}</li>" for st, why in sorted(missing.items()))
    meta = "; ".join(f'{p}: MLLW <a href="metadata/stormsense-metadata-for-{p}-csv-data.pdf">PDF</a> '
                     f'<a href="metadata/stormsense-metadata-for-{p}-csv-data.json">JSON</a>, NAVD88 '
                     f'<a href="metadata/stormsense-metadata-for-{p}-csv-data-NAVD.pdf">PDF</a> '
                     f'<a href="metadata/stormsense-metadata-for-{p}-csv-data-NAVD.json">JSON</a>' for p, _ in prods)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>StormSense Latest</title><style>{STYLE}</style></head>
<body><header><img src="stormsense_logo.png" alt="StormSense: Forecasting Flooding from Storm Surge, Rain, &amp; Tides"></header>
<h1>StormSense Latest Forecasts and Sensor Observations</h1>
<p>Updated every 6 hours; updated {updated}. Links maintain the same address and are overwritten upon each update at about {SCHEDULE}.</p>
{DISC}
<p class="disc">Observations come straight from each gauge's operator: NOAA CO-OPS, USGS, the NWS National Water Prediction
Service, the VIMS THREDDS server, FloodVISION and GreenStream. The astronomical tide is StormSense's own harmonic tide for
each gauge, locked to the 30-day mean water level (m30, the teal line on the 72-hour forecasts). The forecast adds to that
tide the storm surge of the guidance with the best recent skill at the station and lead time (see the
<a href="{ENSEMBLE_URL}skill/">skill page</a>), starting from the residual the gauge shows now; each chart names its guidance
below the disclaimer. The shaded cone is the 10%&ndash;90% range of StormSense's own past errors at each lead time.</p>
<p class="disc">NAVD88 charts show the NWS flood categories (Action, Minor, Moderate, Major) with the chance of reaching each
within 72 hours in parentheses, and the MLLW line; MLLW charts show the tidal datums. Forecast high waters within 0.5 ft of
Action stage (NAVD88 charts) or above HAT (MLLW charts) are labelled XHW. Click a line's label to hide or show it.</p>
<p class="disc">Field descriptions for the CSV and JSON downloads: {meta}.</p>
<div class="wrap"><table><thead><tr><th rowspan="2">Station</th><th rowspan="2">Name</th>{head1}<th rowspan="2">NWS gauge</th><th rowspan="2">Observations</th></tr>
<tr>{head2}</tr></thead><tbody>
{chr(10).join(rows)}
</tbody></table></div>
<p class="disc">Stations without a chart this update:</p><ul class="disc">{miss or '<li>none</li>'}</ul>
</body></html>
"""
    open(os.path.join(site, "index.html"), "w").write(page)


def ensemble_index(site, table, summary, runs, updated):
    import html as H
    rows = []
    for st in sorted(summary):
        cells = "".join(f"<td>{links(site, 'ens72', st, d)}</td>" for d in ("MLLW", "NAVD"))
        rows.append(f"<tr><th>{st}</th><td>{H.escape(table[st]['name'])}</td>{cells}<td>{len(summary[st]['members'])}</td></tr>")
    run_list = "".join(f"<li>{H.escape(STN.MEMBERS[k][0])}: {v or 'unavailable'}</li>" for k, v in runs.items() if k in STN.MEMBERS)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>StormSense Ensemble</title><style>{STYLE}</style></head>
<body><header><img src="stormsense_logo.png" alt="StormSense"></header>
<h1>StormSense Ensemble Forecasts</h1>
<p>Every available 72-hour storm tide forecast for each sensor on one chart, updated every 6 hours (updated {updated};
about {SCHEDULE}), with each forecast's skill tables under the chart. Links keep the same address and are overwritten on
each update; each run is also archived.</p>
{DISC}
<p class="disc">Model guidance (P-ETSS, STOFS-2D, STOFS-3D, CBOFS, NWM, ETSS, P-Surge) is lined up with each gauge: the median
difference from the observations over the last 24 hours is added to the whole run, removing datum offsets and standing
bias. The official NWS and VIMS Tidewatch forecasts are shown as issued. Tide + persistence is StormSense's m30 tide plus the
last 3 hours' residual, fading over a day: the baseline a forecast should beat. The StormSense model blends the members'
surges by their recent skill and corrects the blend by its own recent errors at the gauge; the dashed red line is the
published StormSense Latest forecast. Click a legend entry to hide or show it.</p>
<p class="disc"><a href="skill/">Skill of each forecast</a> against the observations that came in after it, over all stations,
and which guidance drives the StormSense Latest forecast at each station.</p>
<p class="disc">Newest runs used (UTC):</p><ul class="disc">{run_list}</ul>
<div class="wrap"><table><thead><tr><th>Station</th><th>Name</th><th>MLLW</th><th>NAVD88</th><th>Forecasts</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table></div></body></html>
"""
    open(os.path.join(site, "index.html"), "w").write(page)


# --- main --------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", default="site/latest")
    ap.add_argument("--ensemble", default="site/ensemble")
    ap.add_argument("--work", default="work")
    ap.add_argument("--stations", default="")
    ap.add_argument("--rescore-all", action="store_true")
    ap.add_argument("--no-skill", action="store_true", help="skip scoring the archive (testing)")
    ap.add_argument("--now", default="", help="run as of this UTC time, e.g. 2026-10-27T00:20 (testing)")
    a = ap.parse_args(argv)
    now = (datetime.strptime(a.now, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc) if a.now
           else datetime.now(timezone.utc))
    updated = now.strftime("%Y-%m-%d %H:%M UTC")
    cyc = cycle_of(now)
    for d in (a.latest, a.ensemble):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    os.makedirs(a.work, exist_ok=True)
    table = S.load()
    if a.stations:
        keep = set(a.stations.split(","))
        table = {st: r for st, r in table.items() if st in keep or st in {table[x]["alias_of"] for x in keep if x in table}}
    navd = {st: r["navd88"] for st, r in table.items()}

    # 1 thresholds and observations
    thr = THR.build(table)
    json.dump(thr, open(os.path.join(a.latest, "thresholds.json"), "w"), indent=1, sort_keys=True)
    levels = {st: e.get("mllw", {}) for st, e in thr.items()}
    obs, used_src, obs_err = observe.gather(table, now)
    print(f"observations: {len(obs)} stations; none at {len(obs_err)}")
    for st, e in sorted(obs_err.items()):
        print(f"  {st}: {e}")

    # 2 members
    nws_off = {}
    for st, e in thr.items():
        d, r = e.get("datum"), table[st]
        nws_off[st] = 0.0 if d == "MLLW" else (r["navd88"] if d == "NAVD88" else r["stage_to_mllw"])
    nws_off = {st: v for st, v in nws_off.items() if v is not None}
    pet_cycle, pet = MR.petss_points(now, table)
    etss_cycle, etss = MR.etss_surge(now, table)
    raw = MR.raw_members(now, table, nws_off)
    tw = MR.tidewatch(table)

    # 3 skill and the pick
    truth = {st: pd.Series(v, index=pd.DatetimeIndex(t)) for st, (t, v) in obs.items()}
    pick_doc, rec = None, pd.DataFrame(columns=SK.REC_COLS)
    frec = pd.DataFrame()
    if not a.no_skill:
        try:
            pick_doc, rec, frec = SK.run(os.path.join(a.ensemble, "skill"), os.path.join(a.work, "skill"),
                                         pd.Timestamp(now), truth, levels, navd, rescore_all=a.rescore_all)
        except Exception:
            traceback.print_exc()
    setting = guidance_setting()
    rmse = {}
    for _, r in rec[rec["band"].isin(SK.PICK_BANDS)].iterrows():
        rmse.setdefault(r["station"], {}).setdefault(r["member"], {})[r["band"]] = r["rmse_ew"]

    # 4 past runs for the corrector, the fore72 history for the cone
    past = None
    try:
        paths = ARC.download_recent(os.path.join(a.work, "past_runs"), now)
        past = ARC.read_runs(paths, navd)
        print(f"corrector: {len(past)} archived runs")
    except Exception as e:
        print(f"corrector: no archived runs ({e})")
    hist = load_history(a.work, table, now)
    trajs = U.trajectories(hist, obs, now) if len(hist) else []
    print(f"uncertainty: {len(trajs)} verified forecasts")

    # 5 stations
    built, missing, summary, new_hist, raw_dump = {}, {}, {}, [], {}
    latest_meta = {}
    for st, info in sorted(table.items()):
        if st not in obs:
            missing[st] = obs_err.get(st, "no observations")
            continue
        ot, ov = obs[st]
        if now - ot[-1] > MAX_OBS_AGE:
            missing[st] = f"no observations since {ot[-1]:%Y-%m-%d %H:%MZ}"
            continue
        try:
            t0 = ot[-1]
            tide_fn, m30, tide_note = tide_for(st, info, ot, ov, t0)
            if tide_fn is None:
                missing[st] = "under 29 days of observations for a tide fit"
                continue
            key = info["alias_of"] or st
            raw_st = {k: (run, by[key]) for k, (run, by) in raw.items() if key in by}
            es = (etss_cycle, etss[key]) if key in etss else None
            t0, grid, cols, surges, notes = STN.build((ot, ov), tide_fn, raw_st, pet.get(key), pet_cycle, es, tw.get(st))
            truth_st = {t: v for t, v in zip(ot, ov)}
            stormsense_member(st, grid, t0, cols, surges, notes, rmse.get(st), past, truth_st, tide_fn)
            picks = {}
            if setting.get("mode") == "override":
                picks = {b: setting.get("override", "petss") for b, _, _ in STN.BANDS}
            elif pick_doc:
                picks = {b: v["member"] for b, v in pick_doc["stations"].get(st, {}).items()}
            prim, used, r0 = STN.primary(grid, t0, cols, surges, picks)
            lead = STN.lead_h(grid, t0)
            model = U.Model(trajs, st, now) if trajs else None
            lo, hi, cone_label, chances = cone_and_chances(model, lead, prim, cols, levels.get(st, {}))
            cols["primary"], cols["primary_p10"], cols["primary_p90"] = prim["SSforecast"], lo, hi
            for k in ("petss", "etss"):
                if k in surges:
                    cols[f"{k}_surge"] = surges[k]
            guide = guidance_line(used, pick_doc, setting)
            src = (f"StormSense Latest: {tide_note} plus the residual of "
                   + ", ".join(f"{STN.NAMES.get(used.get(b), used.get(b) or 'persistence')} for {b} h" for b, _, _ in STN.BANDS)
                   + ", handed over from the gauge's current residual")
            d = S.datums(st)
            obs_src = used_src.get(st, "")
            xhw = P.fore72(a.latest, st, info, d, grid, t0, cols, prim, (lo, hi, cone_label), chances, m30,
                           levels.get(st, {}), guide, src, updated,
                           extra={"observations": obs_src, "tide": tide_note, "guidance_by_band_h": used})
            for prod, days in (("7day", 7), ("30day", 30)):
                P.hindcast(a.latest, prod, days, st, info, d, (ot, ov), tide_fn, levels.get(st, {}), updated,
                           f"{obs_src}; astronomical tide: {tide_note}")
            tabs = SK.tables(rec, frec, st) if len(rec) else None
            P.ens72(a.ensemble, st, info, d, grid, t0, cols, notes, levels.get(st, {}), tabs, updated)
            new_hist += U.rows_for(f"{cyc:%Y-%m-%dT%HZ}", st, grid, t0, prim["SSforecast"])
            built[st] = obs_src
            summary[st] = {"t0": P.iso(t0), "members": notes, "observations": obs_src, "tide": tide_note,
                           "m30_ft": None if m30 is None else round(m30, 3), "guidance": used,
                           "chances": {k: round(v, 3) for k, v in chances.items()}, "xhw": xhw}
            latest_meta[st] = {"guidance": guide, "observations": obs_src, "chances": summary[st]["chances"]}
            raw_dump[st] = {k: {"t": [P.iso(t) for t in v[1][0]], "v": [round(float(x), 3) for x in v[1][1]],
                                "datum": v[1][2]} for k, v in raw_st.items()}
            print(f"  {st}: {len(notes)} members; guidance {used}; cone {'own errors' if cone_label.startswith('10%') else 'fallback'}")
        except Exception:
            traceback.print_exc()
            missing[st] = "build failed (see build_log.txt)"

    # 6 sites
    runs = {k: (r.strftime("%Y-%m-%dT%H:%MZ") if r else None) for k, (r, _) in raw.items()}
    runs["petss"] = pet_cycle.strftime("%Y-%m-%dT%HZ") if pet_cycle else None
    runs["etss"] = etss_cycle.strftime("%Y-%m-%dT%HZ") if etss_cycle else None
    for site in (a.latest, a.ensemble):
        C.copy_jslib(site)
        open(os.path.join(site, ".nojekyll"), "w").close()
    shutil.copytree(os.path.join(HERE, "data", "metadata"), os.path.join(a.latest, "metadata"), dirs_exist_ok=True)
    latest_index(a.latest, table, built, missing, thr, updated)
    ensemble_index(a.ensemble, table, summary, runs, updated)
    json.dump({"updated": updated, "cycle": f"{cyc:%Y-%m-%dT%HZ}", "obs_basis": "stormsense", "runs": runs,
               "guidance_setting": setting, "stations": latest_meta, "missing": missing},
              open(os.path.join(a.latest, "latest.json"), "w"), indent=1)
    json.dump({"updated": updated, "cycle": f"{cyc:%Y-%m-%dT%HZ}", "obs_basis": "stormsense", "runs": runs,
               "stations": summary, "missing": missing},
              open(os.path.join(a.ensemble, "latest.json"), "w"), indent=1, default=str)

    # 7 archive
    arc = os.path.join(a.work, "archive")
    os.makedirs(arc, exist_ok=True)
    assets = []
    json.dump({"updated": updated, "runs": runs, "series": raw_dump},
              open(os.path.join(a.work, "members_native.json"), "w"), separators=(",", ":"))
    tar = os.path.join(arc, f"ensemble_{cyc:%Y-%m-%dT%HZ}.tar.gz")
    with tarfile.open(tar, "w:gz") as t:
        t.add(os.path.join(a.ensemble, "latest.json"), "latest.json")
        t.add(os.path.join(a.work, "members_native.json"), "members_native.json")
        for n in sorted(os.listdir(os.path.join(a.ensemble, "ens72"))):
            if n.endswith((".csv", ".json")):
                t.add(os.path.join(a.ensemble, "ens72", n), f"ens72/{n}")
        sk = os.path.join(a.ensemble, "skill")
        if os.path.isdir(sk):
            t.add(sk, "skill")
    assets.append((f"ensemble-{cyc:%Y-%m}", tar))
    for n in ("skill_scores.csv.gz", "flood_scores.csv.gz", "skill_runs.csv"):
        p = os.path.join(a.work, "skill", n)
        if os.path.exists(p):
            assets.append((f"ensemble-{cyc:%Y-%m}", p))
    hist = U.append(hist, new_hist)
    hp = os.path.join(arc, HISTORY)
    U.write(hist, hp)
    assets.append((f"stormsense-{cyc:%Y-%m}", hp))
    with open(os.path.join(arc, "assets.txt"), "w") as f:
        for tag, p in assets:
            f.write(f"{tag} {os.path.abspath(p)}\n")
    print(f"built {len(built)} stations; missing {len(missing)}; archive {os.path.basename(tar)} "
          f"({os.path.getsize(tar) // 1024} KB)")
    return 0 if built else 1


if __name__ == "__main__":
    raise SystemExit(main())
