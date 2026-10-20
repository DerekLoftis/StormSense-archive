"""Verification of the other archived surge guidance over an event window.

Reads the model point files the archiver saves under data/models/:
STOFS-2D-Global and STOFS-3D-Atlantic (SHEF, ft MLLW), NWM v3 coastal short and
medium range (SHEF, ft NAVD88, put on MLLW with the offset Tidewatch itself
uses between its MLLW and NAVD files) and CBOFS (station zeta, m MSL, put on
MLLW with NOAA's published MSL-MLLW). Each cycle's first 36 h (and 72 h) is
scored against the same Tidewatch observations used for the Tidewatch
forecasts, with the same statistics and Tidewatch Skill.
"""
import gzip
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import runfiles
from . import validation as V

H36, H72 = pd.Timedelta(hours=36), pd.Timedelta(hours=72)
SOURCES = {
    "stofs2d": ("STOFS-2D-Global", "stofs_2d_glo.t*z.points.cwl.shef.gz"),
    "stofs3d": ("STOFS-3D-Atlantic", "stofs_3d_atl.t*z.points.cwl.shef.gz"),
    "nwm-medium-coastal": ("NWM coastal medium range", "*.shef.gz"),
    "nwm-short-coastal": ("NWM coastal short range", "*.shef.gz"),
    "cbofs": ("CBOFS", "cbofs.t*z.*.stations.forecast.zeta.csv.gz"),
}
COLORS = {"Tidewatch": "#0B3FE0", "NWS": "#000000", "STOFS-2D-Global": "#E41A1C", "STOFS-3D-Atlantic": "#FF8C00",
          "NWM coastal medium range": "#2CA02C", "NWM coastal short range": "#9B30D9", "CBOFS": "#8C564B"}


# --------------------------------------------------------------------------- SHEF
def _text(path):
    """A text stream from a .gz path, a plain path, or an already open stream (runfiles.open_text)."""
    if hasattr(path, "read"):
        return path
    return gzip.open(path, "rt", errors="replace") if str(path).endswith(".gz") else open(path, errors="replace")


def parse_shef(path, lids=None):
    """{lid: Series} from a SHEF .E file (values in ft; -9999 dropped)."""
    out, cur = {}, None
    with _text(path) as fh:
        for line in fh:
            if line.startswith(".E "):
                parts = line[3:].strip().split("/")
                head = parts[0].split()
                lid, date = head[0], head[1]
                cur = None
                if lids is not None and lid not in lids:
                    continue
                dh = di = pe = None
                vals = []
                for tok in [t.strip() for t in parts[1:]] + [t for t in head[3:]]:
                    if not tok:
                        continue
                    if tok.startswith("DH"):
                        dh = tok[2:]
                    elif tok.startswith("DI"):
                        di = tok[2:]
                    elif tok.startswith("DC"):
                        pass
                    elif re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3,5}", tok):
                        pe = tok
                    else:
                        vals.append(tok)
                if pe and not pe.startswith("HM"):      # NWM also carries HG (stage) blocks
                    continue
                t0 = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:8]} {dh[:2]}:{dh[2:4] or '00'}", tz="UTC")
                step = pd.Timedelta(minutes=int(di[1:])) if di[0] == "N" else pd.Timedelta(hours=int(di[1:]))
                cur = {"lid": lid, "t0": t0, "step": step, "vals": vals}
                out.setdefault(lid, []).append(cur)
            elif line.startswith(".E") and cur is not None:
                cur["vals"] += [t.strip() for t in line.split(None, 1)[1].split("/") if t.strip()]
    series = {}
    for lid, blocks in out.items():
        b = blocks[0]
        v = pd.to_numeric(pd.Series(b["vals"]), errors="coerce").to_numpy(float)
        idx = pd.date_range(b["t0"], periods=len(v), freq=b["step"])
        s = pd.Series(v, idx)
        series[lid] = s[(s > -900) & s.notna()]
    return series


def parse_cbofs(path):
    """(DataFrame of zeta in m MSL, [(lon, lat)] per column)."""
    coords = []
    fh = _text(path)
    for line in fh:
        if line.startswith("# zeta_"):
            lon, lat = (float(x) for x in line.split(":")[1].split(","))
            coords.append((lon, lat))
        elif not line.startswith("#"):
            break
    fh.seek(0)
    df = pd.read_csv(fh, comment="#", parse_dates=["time"]).set_index("time")
    fh.close()
    df.index = pd.to_datetime(df.index, utc=True)
    return df, coords


def _cycle_time(name):
    return pd.Timestamp(name[:10] + " " + name[11:13] + ":00", tz="UTC")


# --------------------------------------------------------------------------- loading
def load_sources(models_root, lid_of, latlon, navd_to_mllw, msl_to_mllw, t_lo, t_hi):
    """{source label: {station: [(cycle, Series ft MLLW)]}} for cycles t_lo..t_hi."""
    stations_of = {lid: st for st, lid in lid_of.items()}
    out = {}
    for key, (label, pattern) in SOURCES.items():
        out[label] = {}
        base = Path(models_root) / key
        for cdir in sorted(base.glob("*/*/*")):
            try:
                ct = _cycle_time(cdir.name)
            except ValueError:
                continue
            if not (t_lo <= ct <= t_hi):
                continue
            files = runfiles.glob(cdir, pattern)          # loose or packed in run.tar
            if not files:
                continue
            if key == "cbofs":
                df, coords = parse_cbofs(runfiles.open_text(cdir, files[0]))
                for st, (la, lo) in latlon.items():
                    if st not in msl_to_mllw:
                        continue
                    d = [np.hypot((la - y) * 111, (lo - x) * 111 * np.cos(np.radians(la))) for x, y in coords]
                    j = int(np.argmin(d))
                    if d[j] > 3:
                        continue
                    s = df[f"zeta_{j}"].astype(float) / 0.3048 + msl_to_mllw[st]
                    out[label].setdefault(st, []).append((ct, s.dropna()))
                continue
            ser = parse_shef(runfiles.open_text(cdir, files[0]), set(stations_of))
            for lid, s in ser.items():
                st = stations_of[lid]
                if key.startswith("nwm"):
                    if st not in navd_to_mllw:
                        continue
                    s = s + navd_to_mllw[st]
                out[label].setdefault(st, []).append((ct, s))
    return out


# --------------------------------------------------------------------------- scoring
def score(sources, obs, now):
    """One row per source, station and cycle: 36-h and 72-h statistics (partial when obs end early)."""
    rows = []
    for label, by in sources.items():
        for st, cyc in by.items():
            o = obs.get(st)
            if o is None or o.empty:
                continue
            last = o.dropna().index.max()
            for ct, s in cyc:
                for h, H in ((36, H36), (72, H72)):
                    seg = s.loc[ct:ct + H]
                    pr = pd.DataFrame({"predicted": seg, "observed": o.reindex(seg.index)}).dropna()
                    if len(pr) < 3:
                        continue
                    st_ = V._stats(pr)
                    rows.append({"source": label, "station": st, "cycle": ct, "horizon": h,
                                 "partial": bool(last < ct + H), "hours_verified": (pr.index[-1] - ct) / pd.Timedelta(hours=1),
                                 **{k: st_.get(k) for k in ("N", "R2", "Bias", "MAE", "RMSE", "Skill")}})
    return pd.DataFrame(rows)


def high_tides(o, lo, hi, min_sep_h=8):
    """Observed high-water peaks (time, height) in [lo, hi]."""
    win = pd.Timedelta(hours=min_sep_h / 2)
    s = o.loc[lo - win:hi + win].dropna()        # neighbours outside [lo, hi] still decide if a peak is a peak
    if s.empty:
        return []
    sm = s.rolling(5, center=True, min_periods=1).mean()
    peaks = []
    for t, v in sm.loc[lo:hi].items():
        w = sm.loc[t - win:t + win]
        if v == w.max() and t - win >= s.index[0] and t + win <= s.index[-1]:
            if not peaks or t - peaks[-1][0] > pd.Timedelta(hours=min_sep_h):
                tt = s.loc[t - pd.Timedelta(minutes=30):t + pd.Timedelta(minutes=30)].idxmax()
                peaks.append((tt, float(s.loc[tt])))
    return peaks


def peak_errors(series_by_source, obs, lo, hi, lead_max=H36):
    """Forecast minus observed high-water height (and timing) at each observed high tide."""
    rows = []
    for label, by in series_by_source.items():
        for st, cyc in by.items():
            o = obs.get(st)
            if o is None:
                continue
            for tp, hp in high_tides(o, lo, hi):
                for ct, s in cyc:
                    if not (ct + pd.Timedelta(hours=1) <= tp <= ct + lead_max):
                        continue
                    w = s.loc[tp - pd.Timedelta(hours=2):tp + pd.Timedelta(hours=2)]
                    if len(w) < 3:
                        continue
                    rows.append({"source": label, "station": st, "peak_time": tp, "obs_peak": hp, "cycle": ct,
                                 "lead_h": (tp - ct) / pd.Timedelta(hours=1), "fc_peak": float(w.max()),
                                 "err_ft": float(w.max()) - hp,
                                 "timing_min": (w.idxmax() - tp) / pd.Timedelta(minutes=1)})
    return pd.DataFrame(rows)


def lidmeta(gauges_json):
    with (gzip.open if str(gauges_json).endswith(".gz") else open)(gauges_json, "rt") as fh:
        d = json.load(fh)
    return {g["lid"]: (g["latitude"], g["longitude"], g.get("name", "")) for g in d.get("gauges", [])}
