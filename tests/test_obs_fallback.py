"""A stale first source is filled in after its last reading by the next source in the chain."""
from datetime import datetime, timedelta, timezone

import numpy as np

from stormsense import obs


def test_stale_source_filled_by_next(monkeypatch):
    b = datetime(2026, 11, 27, 6, 0, tzinfo=timezone.utc)
    info = {"chain": [("nwis", "x"), ("nwps", "Y")], "navd88": 0.0, "stage_to_mllw": -0.112}

    def fake(st, info, src, sid, a, b_):
        if src == "nwis":
            t = np.array([b - timedelta(hours=10) + timedelta(minutes=6 * i) for i in range(20)])
            return t, np.ones(20)
        t = np.array([b - timedelta(hours=12) + timedelta(minutes=6 * i) for i in range(121)])
        keep = (t >= a) & (t <= b_)
        return t[keep], np.full(keep.sum(), 2.0)

    monkeypatch.setattr(obs, "read_source", fake)
    used = {}
    t, v = obs.fetch_raw("ST", b - timedelta(days=1), b, info, used)
    assert v[0] == 1.0 and v[-1] == 2.0 and t[-1] == b
    assert np.all(np.diff([x.timestamp() for x in t]) > 0)
    assert used["ST"].startswith("nwis:x to ") and used["ST"].endswith("then nwps:Y")


def test_fresh_source_not_filled(monkeypatch):
    b = datetime(2026, 11, 27, 6, 0, tzinfo=timezone.utc)
    info = {"chain": [("nwis", "x"), ("nwps", "Y")]}
    calls = []

    def fake(st, info, src, sid, a, b_):
        calls.append(src)
        t = np.array([b - timedelta(minutes=6 * i) for i in range(10)][::-1])
        return t, np.ones(10)

    monkeypatch.setattr(obs, "read_source", fake)
    obs.fetch_raw("ST", b - timedelta(days=1), b, info, {})
    assert calls == ["nwis"]
