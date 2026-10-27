"""Offline checks of the high-water scoring and the calibrated cone."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from stormsense import flood, uncertainty as U


def tide(hours, amp=1.0, mean=1.5):
    return mean + amp * np.cos(2 * np.pi * hours / 12.42)


def test_peaks_one_per_high_tide():
    h = np.arange(0, 72, 0.1)
    idx = flood.peaks(h, tide(h))
    assert len(idx) == 5  # the crest at hour 0 is an end point, not a peak
    assert all(abs((h[i] % 12.42)) < 0.2 or abs(h[i] % 12.42 - 12.42) < 0.2 for i in idx)


def test_verify_counts_hits_misses_false_alarms():
    h = np.arange(0, 72, 0.1)
    obs = tide(h)
    fc = tide(h - 0.5) + 0.3  # 30 minutes late, 0.3 ft high
    ev = flood.verify(h, obs, fc, {"action": 2.4, "minor": 2.6})
    s = flood.summarize(ev)
    assert s["high_waters"] >= 5
    assert abs(s["peak_error_ft"] - 0.3) < 0.02
    assert abs(s["timing_error_min"] - 30) < 7
    assert s["action_hit"] == s["high_waters"]  # obs peak 2.5, forecast 2.8
    assert s["minor_fa"] == s["high_waters"] and s["minor_miss"] == 0


def test_cone_and_chance_from_past_errors():
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    rng = np.random.default_rng(1)
    trajs = [("SWPT", now - timedelta(days=4, hours=6 * k), np.full(72, e)) for k, e in enumerate(rng.normal(0, 0.2, 40))]
    m = U.Model(trajs, "SWPT", now)
    assert m.ok
    lead = np.arange(-24, 72.01, 0.1)
    fc = tide(lead)
    lo, hi = m.cone(lead, fc)
    width = np.nanmean(hi - lo)
    assert 0.3 < width < 0.7  # about 2 x 1.28 x 0.2 ft
    assert np.isnan(lo[lead <= 0]).all()
    assert m.chance(lead, fc, 2.5) > 0.4  # peaks reach 2.5: about half the error trajectories are positive
    assert m.chance(lead, fc, 4.0) == 0.0


def test_history_rows_and_trajectories():
    t0 = pd.Timestamp("2026-10-20T00:00Z")
    grid = np.array([t0 + pd.Timedelta(minutes=6 * k) for k in range(-240, 721)])
    lead = np.array([(t - t0).total_seconds() / 3600 for t in grid])
    rows = U.rows_for("2026-10-20T00Z", "SWPT", grid, t0, tide(lead))
    assert len(rows) == 72
    h = U.append(U.read(None), rows)
    truth = {"SWPT": (grid, tide(lead) + 0.1)}
    tr = U.trajectories(h, truth, t0 + pd.Timedelta(days=4))
    assert len(tr) == 1 and np.allclose(tr[0][2], 0.1, atol=1e-3)
