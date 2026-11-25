"""Offline tests of CREST's statistics, feeds and pages on a small synthetic archive."""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crest import feeds, page, stats  # noqa: E402

T0 = pd.Timestamp("2026-11-10T00:00Z")


def obs_series(days=6, peak=None):
    t = pd.date_range(T0 - pd.Timedelta(days=1), periods=days * 240, freq="6min")
    h = (t - T0).total_seconds().to_numpy() / 3600
    v = 1.5 + 1.2 * np.cos(2 * np.pi * h / 12.42)
    if peak is not None:
        v = v + peak * np.exp(-((h - 24.84) / 4) ** 2)
    return pd.Series(v, index=t)


def run(obs, members, t0=T0):
    grid = pd.date_range(t0, t0 + pd.Timedelta(hours=72), freq="6min")
    o = obs.reindex(grid).to_numpy()
    return {"cycle": t0.strftime("%Y-%m-%dT%HZ"), "updated": "", "obs_basis": "stormsense",
            "stations": {"TEST": {"t0": int(t0.value // 10**6),
                                  "members": {m: [round(float(x + off), 3) for x in o] for m, off in members.items()}}}}


def scores(rows):
    base = {"t0": T0.isoformat(), "span_h": 72, "N": 700, "R2": 0.9, "Bias": 0.1, "MAE": 0.2, "RMSE": 0.25,
            "SDobs": 0.8, "PeakError": 0.1, "ConeHit": np.nan, "member_run": "", "lead_end": ""}
    return pd.DataFrame([dict(base, **r) for r in rows])


def test_ens_mean_scores_the_blend_like_skill_py():
    o = obs_series()
    runs = {"2026-11-10T00Z": run(o, {"petss": 0.3, "stofs2d": -0.3, "nws": 0.0, "tidewatch": 5.0})}
    em = stats.ens_mean_rows(runs, {"TEST": o}, None)
    full = em[em["band"] == "0-72"].iloc[0]
    # mean of +0.3, -0.3, 0 is the observations themselves (Tidewatch is not in the blend)
    assert abs(full["Bias"]) < 1e-3 and full["Skill"] > 0.99


def test_events_find_the_flood_and_score_each_guidance_peak():
    o = obs_series(peak=2.0)
    lv = {"action": 3.5, "minor": 4.0, "moderate": 5.0, "major": 6.0}
    runs = {"2026-11-10T00Z": run(o, {"primary": 0.2, "petss": -0.5})}
    ev = stats.events(o[o.index >= T0], lv, runs, "TEST")
    assert len(ev) >= 1
    e = max(ev, key=lambda x: x["peak"])
    assert e["category"] in ("minor", "moderate")
    assert abs(e["by"]["primary"]["pe"] - 0.2) < 0.01 and abs(e["by"]["petss"]["pe"] + 0.5) < 0.01
    assert e["by"]["primary"]["hit"] == 1.0


def test_leaderboard_report_card_and_picks():
    rows = []
    cycles = [f"2026-11-1{d}T{h:02d}Z" for d in range(2) for h in (0, 6, 12)]
    for c in cycles:
        for m, s in (("primary", 0.9), ("petss", 0.8), ("tide", 0.95)):
            for b in ("0-12", "12-36", "36-72", "0-72", "full") + tuple(stats.SEGMENTS):
                rows.append({"cycle": c, "station": "TEST", "member": m, "band": b, "Skill": s})
    sc = scores(rows)
    lb, span = stats.leaderboard(sc)
    assert lb["primary"]["0-72"]["S"] == 0.9 and lb["petss"]["0-72"]["n"] == 6 and span["primary"] == 72
    dec = stats.decay(sc)
    card = stats.report_card(lb, dec, {}, {"cov": [100.0]}, [])
    assert card["best"] == "primary" and card["horizon_h"] == 72  # the tide alone is never "best"
    ph = pd.DataFrame({"cycle": ["2026-11-10T00Z", "2026-11-10T06Z"], "station": "TEST", "band": "0-12", "pick": "petss"})
    pk = stats.picks(ph, sc)
    assert pk["counts"]["0-12"]["petss"] == 2 and pk["achieved"]["0-12"] == 0.8 and pk["best"]["0-12"] == 0.8


def test_station_doc_feeds_and_page(tmp_path):
    o = obs_series(peak=2.0)
    runs = {"2026-11-10T00Z": run(o, {"primary": 0.1, "petss": -0.2, "primary_p10": -0.3, "primary_p90": 0.4})}
    rows = [{"cycle": "2026-11-10T00Z", "station": "TEST", "member": m, "band": b, "Skill": 0.85}
            for m in ("primary", "petss") for b in ("0-72", "full", "0-36") + tuple(stats.SEGMENTS)]
    sc = scores(rows)
    period = {"id": "30d", "label": "Last 30 days", "start": T0.isoformat(), "end": (T0 + pd.Timedelta(days=4)).isoformat(),
              "start_t": T0, "end_t": T0 + pd.Timedelta(days=4)}
    info = {"name": "Test gauge", "lat": 37.0, "lon": -76.3, "navd88": 1.6, "suspect": False, "added": "", "source": "coops:1"}
    lv = {"action": 3.5, "minor": 4.0}
    d = stats.station_doc("TEST", info, period, sc, pd.DataFrame(), pd.DataFrame(), runs, {"TEST": o}, lv, pd.DataFrame(), [])
    json.dumps(d, allow_nan=False)
    assert d["runs"][0]["series"]["primary"] and d["card"]["primary"] == 0.85 and d["events"]
    net = stats.network_doc(period, [d], [])
    out = tmp_path / "site"
    (out / "data").mkdir(parents=True)
    json.dump(d, open(out / "data" / "TEST_30d.json", "w"))
    json.dump(net, open(out / "data" / "network_30d.json", "w"))
    feeds.write(str(out / "feeds"), net, [d], sc, {"TEST": o}, {"TEST": lv}, [], period)
    assert (out / "feeds" / "stations.geojson").exists() and (out / "feeds" / "daily_skill.csv").exists()
    manifest = {"built": "x", "default": "30d", "stations": ["TEST"], "periods": [{"id": "30d", "label": "Last 30 days", "stations": ["TEST"]}]}
    page.write_site(str(out), manifest, {"TEST": info})
    html = (out / "crest_test.html").read_text()
    assert "StormSense(TM) Predictive Plots" in html and "__DOC__" not in html and "</script>" in html
    assert "Test gauge" in (out / "index.html").read_text()
