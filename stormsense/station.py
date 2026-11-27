"""One station's forecasts for one run, on a 6-minute grid from 24 hours before the last
observation (t0) to 72 hours after it, every series in ft above the station's MLLW.

Members are lined up with the gauge as in the StormSense Ensemble: model guidance gets the
median difference from the observations over the last 24 hours added to the whole run (datum
offsets and standing bias); the NWS official forecast and Tidewatch are shown as issued.

The tide is StormSense's own harmonic tide locked to m30 (tide.py). P-ETSS and ETSS also give
their surge on its own (P-ETSS's storm tide minus its tide; ETSS is surge only), kept in `surges`
so the StormSense forecast can put that surge on our tide rather than inherit NOAA's tide.

primary() is the StormSense Latest 72-hour forecast: the m30 tide plus the residual of the
guidance chosen for each lead band (the adaptive pick, P-ETSS by default), handed over from the
gauge's current residual (Tidewatch's offset of the surge to the latest anomaly) and eased across
band edges.
"""
from datetime import timedelta

import numpy as np

from . import petss, surge as SURGE
from .obs import MAX_GAP

HOURS = 72
STEP = timedelta(minutes=6)
# key: (label, color, dash, aligned to the gauge)
MEMBERS = {
    "petss": ("P-ETSS (mean)", "#14285a", "Solid", True),
    "stofs2d": ("STOFS-2D-Global", "#ff7f0e", "Solid", True),
    "stofs3d": ("STOFS-3D-Atlantic", "#bcbd22", "Solid", True),
    "cbofs": ("CBOFS", "#8c564b", "Solid", True),
    "nwm_short": ("NWM coastal (short range)", "#9467bd", "Solid", True),
    "nwm_medium": ("NWM coastal (medium range)", "#e377c2", "Solid", True),
    "etss": ("NWS ETSS", "#17becf", "Solid", True),
    "psurge": ("NHC P-Surge (50%)", "#d62728", "Solid", True),
    "nws": ("NWS official", "#000000", "Solid", False),
    "tidewatch": ("VIMS Tidewatch", "#2ca02c", "Solid", False),
    "baseline": ("Tide + persistence", "#7f7f7f", "ShortDash", False),
    "stormsense": ("StormSense model", "#e6007e", "Solid", False),
}
NAMES = {"petss": "NWS P-ETSS", "stofs2d": "NOAA STOFS-2D-Global", "stofs3d": "NOAA STOFS-3D-Atlantic",
         "cbofs": "NOAA CBOFS", "nwm_short": "NWM coastal (short range)", "nwm_medium": "NWM coastal (medium range)",
         "etss": "NWS ETSS", "psurge": "NHC P-Surge (50%)", "nws": "NWS official forecast",
         "tidewatch": "VIMS Tidewatch", "baseline": "Tide + persistence", "tide": "Astronomical tide only",
         "stormsense": "StormSense model", "stormsense_raw": "StormSense model (uncorrected)",
         "primary": "StormSense Latest (published forecast)"}
BANDS = (("0-12", 0, 12), ("12-36", 12, 36), ("36-72", 36, 72))
TAU_H = SURGE.TAU_H     # hand-over from the gauge's residual to the guidance's surge
EASE_H = 1.0            # guidance changes between bands are eased over this many hours
FALLBACK = ("petss", "stormsense", "etss", "baseline")


def sec(times):
    return np.array([t.timestamp() for t in times])


def on_grid(series, grid, max_gap=None):
    """Linear interpolation onto grid, NaN outside the series' own span. With max_gap (a timedelta), also NaN
    between two values further apart than that: a gauge's outage is a gap, not a straight line across it."""
    t, v = series
    s, g = sec(t), sec(grid)
    out = np.interp(g, s, v)
    out[(g < s[0]) | (g > s[-1])] = np.nan
    if max_gap is not None and len(s) > 1:
        j = np.clip(np.searchsorted(s, g), 1, len(s) - 1)
        out[~np.isin(g, s) & (s[j] - s[j - 1] > max_gap.total_seconds())] = np.nan
    return out


def obs_gap(obs, t_end):
    """The longest silence still drawn through: obs.MAX_GAP, or twice the gauge's own reporting step over the
    last day when that is longer (a gauge that reports hourly is not out between its readings)."""
    s = sec(obs[0][obs[0] > t_end - timedelta(hours=24)])
    step = float(np.median(np.diff(s))) if len(s) > 2 else 0.0
    return max(MAX_GAP, timedelta(seconds=2 * step))


def alignment(obs, series, t_end):
    """Median of observation minus member over their overlap in the last 24 h (None if under 3 h)."""
    ot, ov = obs
    sel = (ot > t_end - timedelta(hours=24)) & (ot <= t_end)
    t = ot[sel]
    if not len(t):
        return None
    m = on_grid(series, t)
    ok = ~np.isnan(m)
    if ok.sum() < 30:  # 30 six-minute observations = 3 hours
        return None
    return float(np.median(ov[sel][ok] - m[ok]))


def grid_for(t0):
    return np.array([t0 - timedelta(hours=24) + k * STEP for k in range((HOURS + 24) * 10 + 1)])


def build(obs, tide_fn, raw, pet, pet_cycle, etss_surge=None, tw=None):
    """(t0, grid, cols, surges, notes) for one station. obs: (times, ft); tide_fn(times) -> m30 tide;
    raw: {key: (run, (t, v, datum, (stitched t, v)))} of this station's members; pet: its P-ETSS
    station dict; etss_surge: (times, surge ft); tw: Tidewatch (times, ft)."""
    t0 = obs[0][-1]
    grid = grid_for(t0)
    cols = {"observed": on_grid(obs, grid, obs_gap(obs, t0))}
    cols["observed"][grid > t0] = np.nan
    tide = tide_fn(grid)
    cols["astro_tide"] = tide
    surges, notes = {}, {}
    if pet is not None:
        try:
            f = petss.forecast(pet, t0, pet_cycle, hours=HOURS)
            hourly = (pet["time"], pet["TWL"])
            ok = ~np.isnan(hourly[1])
            adj = alignment(obs, (hourly[0][ok], hourly[1][ok]), t0) or 0.0
            for k, name in (("SSforecast", "petss"), ("low", "petss_p10"), ("high", "petss_p90")):
                cols[name] = on_grid((f["time"], f[k] + adj), grid)
            surges["petss"] = on_grid((f["time"], f["residual"]), grid)
            # its own hindcast over the last 24 h too, so the surge can be lined up with the gauge
            # on our tide (surge.offset): the station CSV starts days before the cycle
            ptide = petss.hindcast_tide(pet, pet["time"])
            past = (grid < t0) & (grid >= t0 - timedelta(hours=24))
            ok = ~np.isnan(pet["TWL"]) & ~np.isnan(ptide)
            if ok.sum() > 1:
                hc = on_grid((pet["time"][ok], pet["TWL"][ok] - ptide[ok]), grid)
                surges["petss"][past] = hc[past]
            notes["petss"] = {"run": pet_cycle.strftime("%Y-%m-%dT%HZ"), "aligned_ft": round(adj, 3),
                              "point": pet.get("id")}
        except ValueError as e:
            print(f"  P-ETSS {e}")
    if etss_surge is not None:
        run, (t, s) = etss_surge
        es = on_grid((t, s), grid)
        if not np.isnan(es[grid >= t0]).all():
            total = (t, s + tide_fn(t))
            adj = alignment(obs, total, t0)
            adj = 0.0 if adj is None else adj
            cols["etss"] = on_grid((total[0], total[1] + adj), grid)
            cols["etss"][grid < t0 - timedelta(hours=24)] = np.nan
            surges["etss"] = es
            notes["etss"] = {"run": run.strftime("%Y-%m-%dT%HZ") if run else None, "aligned_ft": round(adj, 3),
                             "tide": "StormSense m30 tide"}
    for key, (run, (t, v, datum, (st_t, st_v))) in raw.items():
        aligned = MEMBERS[key][3]
        adj = 0.0
        if aligned:  # the newest run, preceded by the run before, against the last 24 h of observations
            adj = alignment(obs, (st_t, st_v), t0)
            if adj is None:
                if datum == "MSL":  # no fixed tie to MLLW without an overlap
                    continue
                adj = 0.0
        series = on_grid((t, v + adj), grid)
        series[grid < t0 - timedelta(hours=24)] = np.nan
        if np.isnan(series[grid >= t0]).all():
            continue
        cols[key] = series
        notes[key] = {"run": run.strftime("%Y-%m-%dT%H:%MZ") if run else None,
                      "aligned_ft": round(adj, 3) if aligned else None, "native_datum": datum}
    if tw is not None:
        cols["tidewatch"] = on_grid(tw, grid)
        notes["tidewatch"] = {"run": tw[0][0].strftime("%Y-%m-%dT%H:%MZ"), "aligned_ft": None}
    r0 = residual_now(grid, t0, cols)
    if not np.isnan(r0):  # tide plus the last 3 hours' residual, decaying over a day
        lead = lead_h(grid, t0)
        base = tide + r0 * np.exp(-np.clip(lead, 0, None) / 24.0)
        base[lead < 0] = np.nan
        cols["baseline"] = base
        notes["baseline"] = {"residual_ft": round(float(r0), 3), "e_folding_h": 24, "tide": "StormSense m30 tide"}
    return t0, grid, cols, surges, notes


def lead_h(grid, t0):
    return (sec(grid) - t0.timestamp()) / 3600.0


def residual_now(grid, t0, cols, hours=3.0):
    lead = lead_h(grid, t0)
    sel = (lead > -hours) & (lead <= 0)
    d = cols["observed"][sel] - cols["astro_tide"][sel]
    return float(np.nanmedian(d)) if np.isfinite(d).any() else np.nan


def model_cols(cols, surges):
    """Members as the StormSense model sees them: P-ETSS and ETSS as their own surge on our tide."""
    out = dict(cols)
    for k, s in surges.items():
        if k in out:
            out[k] = cols["astro_tide"] + s
    return out


def guidance_residual(key, grid, t0, cols, surges, r0):
    """(residual handed over from the gauge, the guidance's own surge) for one guidance, or None."""
    tide = cols["astro_tide"]
    lead = lead_h(grid, t0)
    if key in ("stormsense", "baseline"):
        if key not in cols:
            return None
        s = cols[key] - tide
        return s, s  # already starts from the gauge's residual
    if key in surges:
        s = surges[key]
    elif key in cols:
        s = cols[key] - tide
    else:
        return None
    if np.isnan(s[lead > 0]).all():
        return None
    b = SURGE.offset(lead, s, cols["observed"] - tide)  # lined up with the gauge on our tide
    return SURGE.anchor(lead, s, r0, TAU_H, b), s


def primary(grid, t0, cols, surges, picks):
    """The StormSense Latest forecast: {astro_tide, surge, bias, residual, SSforecast} on grid
    (NaN before t0) and the guidance used per band. picks: {band: member key}."""
    lead = lead_h(grid, t0)
    r0 = residual_now(grid, t0, cols, hours=1.0)
    if np.isnan(r0):
        r0 = residual_now(grid, t0, cols)
    used, res, own = {}, np.full(len(grid), np.nan), np.full(len(grid), np.nan)
    for name, lo, hi in BANDS:
        sel = (lead > lo) & (lead <= hi) if lo else (lead >= 0) & (lead <= hi)
        for key in (picks.get(name),) + FALLBACK:  # the pick, then the fallbacks where it has no value
            todo = sel & np.isnan(res)
            if not key or not todo.any():
                continue
            g = guidance_residual(key, grid, t0, cols, surges, r0)
            if g is None or np.isnan(g[0][todo]).all():
                continue
            fill = todo & ~np.isnan(g[0])
            res[fill], own[fill] = g[0][fill], g[1][fill]
            used.setdefault(name, key)
    # ease each change of guidance: a running mean over EASE_H hours around the band edges
    w = int(round(EASE_H * 10)) | 1
    for _, _, hi in BANDS[:-1]:
        near = np.abs(lead - hi) <= EASE_H
        if near.any() and not np.isnan(res[near]).any():
            k = np.ones(w) / w
            pad = np.pad(res, w // 2, mode="edge")
            smooth = np.convolve(np.nan_to_num(pad), k, mode="valid")
            res[near] = smooth[near]
    res = np.where(np.isnan(res), 0.0 if np.isnan(r0) else r0 * np.exp(-np.clip(lead, 0, None) / 24.0), res)
    own = np.where(np.isnan(own) & (np.abs(lead) < 0.01), res, own)  # at t0 the surge is the residual now
    tide = cols["astro_tide"]
    fut = lead >= 0
    out = {"astro_tide": np.where(fut, tide, np.nan), "residual": np.where(fut, res, np.nan),
           "SSforecast": np.where(fut, tide + res, np.nan), "surge": np.where(fut, own, np.nan)}
    out["bias"] = out["residual"] - out["surge"]
    return out, used, r0
