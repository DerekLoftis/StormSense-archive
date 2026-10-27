"""High waters: the forecast peaks labelled on the charts (XHW) and the scores that matter in a
flood, at each high tide.

Peaks: local maxima of a 6-minute series at least MIN_SEP_H apart (each tidal high water; a
higher maximum within that span wins), on the series smoothed over 30 minutes so a wiggle at the
crest is not counted twice.

Verification against observed high waters: each observed high water in the window is paired with
the forecast's highest water within +/- MATCH_H hours of it.

    peak-height error  forecast peak - observed peak (ft)
    peak-timing error  forecast peak time - observed peak time (minutes; + = forecast late)
    per NWS flood category (Action, Minor, Moderate, Major), each high water is a
        hit           both reached it
        miss          the water reached it, the forecast did not
        false alarm   the forecast reached it, the water did not
"""
import numpy as np

MIN_SEP_H = 6.0
MATCH_H = 3.0
SMOOTH = 5            # 6-minute steps in the running mean (30 minutes)
CATEGORIES = ("action", "minor", "moderate", "major")
XHW_NEAR_ACTION_FT = 0.5


def _smooth(v, n=SMOOTH):
    if len(v) < n:
        return v
    ok = np.isfinite(v)
    k = np.ones(n)
    num = np.convolve(np.where(ok, v, 0.0), k, mode="same")
    den = np.convolve(ok.astype(float), k, mode="same")
    out = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    out[~ok] = np.nan
    return out


def peaks(hours, v, min_sep_h=MIN_SEP_H):
    """Indices of high waters in v (hours: the series' times in hours, evenly spaced)."""
    s = _smooth(np.asarray(v, float))
    if len(s) < 3:
        return []
    cand = [i for i in range(1, len(s) - 1)
            if np.isfinite(s[i]) and np.isfinite(s[i - 1]) and np.isfinite(s[i + 1])
            and s[i] >= s[i - 1] and s[i] > s[i + 1]]
    cand.sort(key=lambda i: -s[i])
    kept = []
    for i in cand:
        if all(abs(hours[i] - hours[j]) >= min_sep_h for j in kept):
            kept.append(i)
    kept.sort()
    # report the raw maximum near each smoothed crest
    out = []
    for i in kept:
        lo, hi = max(0, i - SMOOTH), min(len(v), i + SMOOTH + 1)
        seg = np.asarray(v[lo:hi], float)
        if np.isfinite(seg).any():
            out.append(lo + int(np.nanargmax(seg)))
    return out


def xhw(hours, fc, threshold):
    """Indices of forecast peaks at or above `threshold` (the charts' XHW labels)."""
    return [i for i in peaks(hours, fc) if np.isfinite(fc[i]) and fc[i] >= threshold]


def verify(hours, obs, fc, levels):
    """Per observed high water in the overlap: dict(obs, fc, dt_min, and per category hit/miss/fa).
    hours: common time axis (hours); obs, fc: series on it; levels: {category: ft} on the same
    datum."""
    out = []
    both = np.isfinite(obs) & np.isfinite(fc)
    if both.sum() < 20:
        return out
    for i in peaks(hours, np.where(both, obs, np.nan)):
        near = both & (np.abs(hours - hours[i]) <= MATCH_H)
        if not near.any():
            continue
        j = np.where(near)[0][int(np.argmax(fc[near]))]
        ev = {"obs": float(obs[i]), "fc": float(fc[j]), "dt_min": float((hours[j] - hours[i]) * 60.0)}
        for c in CATEGORIES:
            lv = levels.get(c)
            if lv is None:
                continue
            o, f = obs[i] >= lv, fc[j] >= lv
            ev[c] = "hit" if (o and f) else "miss" if o else "fa" if f else "none"
        out.append(ev)
    return out


def summarize(events):
    """Mean peak-height error, mean absolute peak-height error, mean absolute timing error and the
    hit/miss/false-alarm counts per category over a list of verify() events."""
    if not events:
        return {}
    e = np.array([x["fc"] - x["obs"] for x in events])
    dt = np.array([x["dt_min"] for x in events])
    out = {"high_waters": len(events), "peak_error_ft": float(e.mean()), "peak_abs_error_ft": float(np.abs(e).mean()),
           "timing_error_min": float(dt.mean()), "timing_abs_error_min": float(np.abs(dt).mean())}
    for c in CATEGORIES:
        for k in ("hit", "miss", "fa"):
            out[f"{c}_{k}"] = sum(1 for x in events if x.get(c) == k)
    return out
