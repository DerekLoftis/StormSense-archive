"""Offline tests of CREST's statistics, feeds and pages on a small synthetic archive."""
import csv
import json
import math
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crest import feeds, inputs, page, schema, stats  # noqa: E402

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


def test_a_flood_the_record_ends_in_is_marked_and_not_scored_yet(tmp_path):
    # the gauge's newest observation is on the rising limb, above Action: the highest water so far is not the peak
    o = obs_series(peak=2.0)
    cut = o[o.index <= T0 + pd.Timedelta(hours=24)]
    lv = {"action": 3.5, "minor": 4.0, "moderate": 5.0, "major": 6.0}
    runs = {"2026-11-10T00Z": run(o, {"primary": 0.2, "petss": -0.5})}
    ev = stats.events(cut[cut.index >= T0], lv, runs, "TEST", last=cut.index[-1])
    assert len(ev) == 1 and ev[0]["ongoing"] and ev[0]["by"] == {} and ev[0]["peak"] == round(float(cut.iloc[-1]), 2)
    # the same flood once the record runs past it (the water has fallen) is finished and scored as before
    done = stats.events(o[o.index >= T0], lv, runs, "TEST", last=o.index[-1])
    assert len(done) == 1 and "ongoing" not in done[0] and done[0]["by"]["primary"]["n"] == 1 and done[0]["peak"] > ev[0]["peak"]
    # a finished month is cut at its end, not at the gauge's newest observation: a flood crossing it is not "in progress"
    assert "ongoing" not in stats.events(cut[cut.index >= T0], lv, runs, "TEST", last=o.index[-1])[0]

    # the pages' data and the feeds say so, and give no peak error for it
    sc = scores([{"cycle": "2026-11-10T00Z", "station": "TEST", "member": m, "band": b, "Skill": 0.85}
                 for m in ("primary", "petss") for b in ("0-72", "full", "0-36") + tuple(stats.SEGMENTS)])
    period = {"id": "30d", "label": "Last 30 days", "start": T0.isoformat(), "end": (T0 + pd.Timedelta(days=4)).isoformat(),
              "start_t": T0, "end_t": T0 + pd.Timedelta(days=4)}
    info = {"name": "Test gauge", "lat": 37.0, "lon": -76.3, "navd88": 1.6, "suspect": False, "added": "", "source": "coops:1"}
    d = stats.station_doc("TEST", info, period, sc, pd.DataFrame(), pd.DataFrame(), runs, {"TEST": cut}, lv, pd.DataFrame(), [])
    assert d["events"][-1]["ongoing"] and d["card"]["events"] == 1
    # a finished month that ended during the flood: the gauge's record goes on after it, so nothing is "in progress"
    month = dict(period, end_t=T0 + pd.Timedelta(hours=24), end=(T0 + pd.Timedelta(hours=24)).isoformat())
    over = stats.station_doc("TEST", info, month, sc, pd.DataFrame(), pd.DataFrame(), runs, {"TEST": o}, lv, pd.DataFrame(), [])
    assert over["events"] and "ongoing" not in over["events"][-1]
    net = stats.network_doc(period, [d], [])
    assert net["stations"][0]["last_event_ongoing"] is True
    feeds.write(str(tmp_path / "feeds"), net, [d], sc, {"TEST": cut}, {"TEST": lv}, [], period)
    flood = next(csv.DictReader(open(tmp_path / "feeds" / "flood_events.csv", newline="")))
    assert flood["status"] == "In progress" and flood["category"] == "Minor" and flood["peak_time"] == "2026-11-11T00:00:00Z"
    assert flood["peak_err_primary"] == flood["timing_err_min_primary"] == flood["runs_primary"] == flood["peak_err_petss"] == ""
    assert flood["summary"].startswith("Still at or above Action at the last observation (11 Nov 00:00Z): 4.51 ft MLLW so far")
    station = next(csv.DictReader(open(tmp_path / "feeds" / "stations.csv", newline="")))
    assert station["events"] == "1" and station["last_event"] == "2026-11-11 (in progress)"


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
    # every feed has exactly the columns of schema.py, and every value reads as its column's type and fits its
    # length (the ArcGIS layers are typed from the schema, so a value of another kind would not load)
    reads = {"str": lambda v: True, "int": lambda v: re.fullmatch(r"-?\d+", v), "float": lambda v: math.isfinite(float(v)),
             "date": lambda v: re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", v)}
    for key, (_, name, _, cols) in schema.FEEDS.items():
        with open(out / "feeds" / name, newline="") as f:
            rows = csv.DictReader(f)
            assert rows.fieldnames == schema.columns(key), name
            for row in rows:
                for c in cols:
                    v = row[c[0]]
                    assert v == "" or reads[c[1]](v), (name, c[0], v)
                    assert c[1] != "str" or len(v) <= schema.esri_fields(key)[c[0]]["length"], (name, c[0], v)
    # the forecast archive: one row per forecast and guidance, with its own peak, its flood category and its chart
    fc = list(csv.DictReader(open(out / "feeds" / "forecasts.csv", newline="")))
    row = next(r for r in fc if r["member"] == "primary")
    assert len(fc) == 2 and row["status"] == "Verified" and row["rating"] == "Good" and float(row["S"]) == 0.85
    assert float(row["fc_peak_ft_mllw"]) > 4.0 and row["fc_category"] == "Minor" and float(row["fc_over_action_ft"]) > 0.5
    assert row["issued"] == "2026-11-10T00:00:00Z" and row["issued_day"] == "2026-11-10T12:00:00Z"
    assert row["summary"].startswith("Peak ") and "(Minor)" in row["summary"] and "S 0.850" in row["summary"]
    assert row["chart_url"].endswith("crest_test.html?embed=1&view=forecast&theme=light#run=2026-11-10T00Z&show=primary")
    flood = next(csv.DictReader(open(out / "feeds" / "flood_events.csv", newline="")))
    assert flood["category"] in ("Action", "Minor") and flood["peak_time"].endswith("Z") and flood["name"] == "Test gauge"
    assert flood["status"] == "Ended" and flood["summary"].startswith("Peak ") and float(flood["peak_err_primary"]) == 0.1
    manifest = {"built": "x", "default": "30d", "stations": ["TEST"], "periods": [{"id": "30d", "label": "Last 30 days", "stations": ["TEST"]}]}
    page.write_site(str(out), manifest, {"TEST": info})
    html = (out / "crest_test.html").read_text()
    assert "StormSense(TM) Predictive Plots" in html and "__DOC__" not in html and "</script>" in html
    # the pages take their colors from schema.py, and have the compact one-forecast view the ArcGIS dashboard embeds
    assert "__COLORS__" not in html and json.dumps(schema.COLORS, separators=(",", ":")) in html and 'Q.get("view") === "forecast"' in html
    # the floods tab has a line for a flood still under way at the gauge's last observation
    assert 'id="evNow"' in html and "e.ongoing" in html
    assert (out / "img" / "stormsense_logo.png").exists() and (out / "img" / "ccrm_logo.png").exists()
    assert "Test gauge" in (out / "index.html").read_text()


def test_obs_tarballs_unpack_and_the_runs_fill_the_newest_hours(tmp_path):
    import io
    import tarfile
    # the daily tarball keeps its CSVs under obs_<day>/6min/
    tp = tmp_path / "obs_2026-11-10.tar.xz"
    with tarfile.open(tp, "w:xz") as t:
        for name, body in (("obs_2026-11-10/6min/TEST.csv", b"date_time_z,water_level_ft_mllw\n2026-11-10T00:00Z,1.5\n"),
                           ("obs_2026-11-10/raw/TEST.csv", b"x\n"), ("obs_2026-11-10/report.csv", b"x\n")):
            ti = tarfile.TarInfo(name)
            ti.size = len(body)
            t.addfile(ti, io.BytesIO(body))
    folder = tmp_path / "day"
    assert inputs.unpack_obs(str(tp), str(folder)) == 1 and (folder / "TEST.csv").exists()
    # a run carries the day before its issue time; the archive wins where both have a value
    arch = pd.Series([1.0, 1.1], index=pd.date_range("2026-11-10T00:00Z", periods=2, freq="6min"))
    t0 = pd.Timestamp("2026-11-10T00:06Z")
    runs = {"2026-11-10T06Z": {"stations": {"TEST": {"t0": 0, "members": {}, "obs": {"t0": int(t0.value // 10**6), "v": [9.0, None, 1.3]}},
                                            "NEW": {"t0": 0, "members": {}, "obs": {"t0": int(t0.value // 10**6), "v": [2.0]}},
                                            "OLD": {"t0": 0, "members": {}}}}}
    out = inputs.top_up_obs({"TEST": arch}, runs)
    assert out["TEST"].tolist() == [1.0, 1.1, 1.3] and out["TEST"].index[-1] == pd.Timestamp("2026-11-10T00:18Z")
    assert out["NEW"].tolist() == [2.0] and "OLD" not in out
