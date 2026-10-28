"""Every station's own observations for one StormSense run: the last DAYS days from each gauge's
own source (obs.py), quality-controlled, on the 6-minute clock, ft above the station's MLLW.

A station that is the same gauge as another (alias_of) gets that gauge's record. With
STORMSENSE_OBS_DIR set (testing), <ST>.csv files there (date_time_z, water_level_ft_mllw) are
read instead of fetching those stations.
"""
import concurrent.futures
import csv
import os
from datetime import datetime, timedelta, timezone

import numpy as np

from . import obs

DAYS = 40  # m30 for runs up to the corrector's 7-day window back, plus slack


def _read_dir(path):
    rows = list(csv.reader(open(path)))[1:]
    t = np.array([datetime.strptime(r[0], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc) for r in rows])
    return t, np.array([float(r[1]) for r in rows])


def gather(table, now, days=DAYS, workers=8, local=None):
    """({station: (times, ft MLLW)}, {station: source used}, {station: error}) for every station
    with an observation source."""
    local = local if local is not None else os.environ.get("STORMSENSE_OBS_DIR")
    a = now - timedelta(days=days)
    out, used, errors = {}, {}, {}
    todo = []
    for st, info in table.items():
        if info["alias_of"]:
            continue
        p = os.path.join(local, f"{st}.csv") if local else None
        if p and os.path.exists(p):
            t, v = _read_dir(p)
            sel = (t >= a) & (t <= now)
            out[st], used[st] = (t[sel], v[sel]), f"file:{p}"
        elif info["chain"]:
            todo.append(st)
        else:
            errors[st] = f"no public observation source ({info['note'] or 'none listed'})"

    def one(st):
        u = {}
        return st, obs.fetch(st, a, now, table[st], u), u.get(st)

    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, st): st for st in todo}
        for f in concurrent.futures.as_completed(futs):
            st = futs[f]
            try:
                _, ser, src = f.result()
                if len(ser[0]):
                    out[st], used[st] = ser, src
                else:
                    errors[st] = "no readings after quality control"
            except Exception as e:
                errors[st] = str(e)[:300]
    for st, info in table.items():
        if info["alias_of"] and info["alias_of"] in out:
            out[st] = out[info["alias_of"]]
            standin = info["note"].lower().startswith("stand-in")
            used[st] = f"{'stand-in' if standin else 'same gauge'}: {info['alias_of']} ({used[info['alias_of']]})"
    return out, used, errors
