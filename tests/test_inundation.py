"""Offline tests of the inundation pieces: road splitting, water connectivity, levels, tethering, status."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inundation import FLOODED_FT, PONDED_FT, depth, segments, status  # noqa: E402


def flat(lon, lat):
    """A stand-in projection: degrees are metres."""
    return np.asarray(lon, float), np.asarray(lat, float)


def road(coords, **props):
    return {"type": "Feature", "properties": {"OBJECTID": 1, "RCL_ID": 7, "ST_FULL": "Test St", "MTFCC": "S1400",
                                               "SEG_TYPE": 99, **props},
            "geometry": {"type": "LineString", "coordinates": coords}}


def test_split_makes_equal_pieces_no_longer_than_5_m():
    p = segments.split([road([[0, 0], [23, 0]]), road([[0, 10], [3, 10], [3, 14]], OBJECTID=2)], flat)
    first = p["road"] == 0
    assert first.sum() == 5 and np.allclose(p["length_m"][first], 4.6)
    assert np.allclose(p["x0"][first][1:], p["x1"][first][:-1])          # pieces join end to end
    assert np.isclose(p["length_m"].sum(), 23 + 7)
    assert p["sx"].shape == (len(p["road"]), segments.NSAMP)
    corner = p["lines"][int(np.flatnonzero(~first)[0])]                  # 3.5 m pieces: the first holds the corner
    assert len(corner) == 3 and tuple(corner[1]) == (3.0, 10.0)
    assert list(p["ST_FULL"][first]) == ["Test St"] * 5


def test_water_stays_out_of_a_low_spot_until_the_ground_between_is_overtopped():
    pytest.importorskip("scipy")
    dem = np.full((5, 9), 60.0, np.float32)
    dem[:, 0] = -5.0                 # tidal water on the west edge
    dem[2, 1:4] = [1.0, 1.0, 1.0]    # low ground joined to it
    dem[2, 4] = 3.0                  # a berm
    dem[2, 5:8] = 0.5                # a low spot behind the berm
    ci = depth.connect_index(dem)
    lev = depth.LEVELS_FT
    assert lev[ci[2, 2]] == 1.0
    assert lev[ci[2, 6]] == 3.0      # wet only once the berm is, though the ground is at 0.5
    assert ci[0, 8] == depth.NEVER
    g, c = depth.sample(dem, ci, (0, 5), np.array([6.5, 100.0]), np.array([2.5, 2.5]))
    assert np.isclose(g[0], 0.5) and c[0] == 3.0 and np.isnan(g[1])
    assert np.isclose(depth.first_level(g[:1], c[:1], FLOODED_FT)[0], 3.0)


def test_depth_stack_gives_the_same_ground_and_connect_level():
    lev = depth.LEVELS_FT
    ground, connect = 1.1, 3.0
    d = np.where(lev >= connect, lev - ground, 0.0)[:, None]
    g, c = depth.from_depth_stack(np.hstack([d, np.zeros_like(d)]))
    assert np.isclose(g[0], ground) and c[0] == connect and np.isnan(g[1]) and np.isnan(c[1])


def test_states_follow_dereks_depths():
    d = status.depth_at(4.0, np.array([3.8, 3.75, 3.5, 3.4, 1.0]), np.array([3.0, 3.0, 3.0, 3.0, 4.25]))
    assert list(status.state_of(d)) == [0, 1, 1, 2, 0]      # 0.2 dry, 0.25 ponded, 0.5 ponded, 0.6 flooded, cut off
    assert PONDED_FT == 0.25 and FLOODED_FT == 0.5


def levels(peak, obs):
    t0 = datetime(2026, 11, 26, 0, 12, tzinfo=timezone.utc)
    t = [t0 + timedelta(minutes=6 * i) for i in range(721)]
    h = np.arange(721) / 10
    f = 1.0 + (peak - 1.0) * np.exp(-((h - 14) / 3.0) ** 2)
    k = int(f.argmax())
    return {"station": "AAAA", "updated": "", "guidance": "", "t": t, "f": f, "p10": f - 0.3, "p90": f + 0.3,
            "flood_levels_ft": {}, "obs_ft": obs, "obs_time": t0, "peak_ft": float(f[k]), "peak_time": t[k],
            "peak_p10_ft": float(f[k] - 0.3), "peak_p90_ft": float(f[k] + 0.3)}


def test_evaluate_now_peak_window_and_chance():
    cols = {"station": np.array(["AAAA"] * 4 + ["ZZZZ"], dtype=object),
            "ground_ft": np.array([2.0, 4.4, 4.9, 9.0, 0.0]), "connect_ft": np.array([2.0, 4.5, 5.0, np.nan, 0.0])}
    r = status.evaluate(cols, {"AAAA": levels(5.0, 2.6)}, np.zeros(5, bool))
    assert list(r["state_now"]) == [2, 0, 0, 0, 0]
    assert list(r["state_peak"]) == [2, 2, 0, 0, 0]
    assert list(r["state_p90"]) == [2, 2, 1, 0, 0]
    assert r["hours_flooded"][0] > r["hours_flooded"][1] > 0 and r["hours_flooded"][2] == 0
    assert r["first"][1] < levels(5.0, 2.6)["peak_time"] < r["last"][1]
    assert r["chance"][0] > 0.99 and 0.5 < r["chance"][1] < 1 and 0 < r["chance"][2] < 0.5 and r["chance"][3] == 0
    assert not r["has_forecast"][4]                          # a gauge with no forecast is left out
    skipped = status.evaluate(cols, {"AAAA": levels(5.0, 2.6)}, np.array([True, False, False, False, False]))
    assert skipped["state_peak"][0] == 0                     # bridges and piers are never flagged


def test_nearest_gauge_and_huc12_override():
    pytest.importorskip("scipy")
    from inundation import tether
    sts = [{"station": "WEST", "name": "", "lon": 0.0, "lat": 0.0}, {"station": "EAST", "name": "", "lon": 10000.0, "lat": 0.0}]
    x, y = np.array([1000.0, 9000.0, 2000.0]), np.zeros(3)
    huc = np.array(["A", "A", "B"], dtype=object)
    st, km = tether.nearest(x, y, sts, flat)
    assert list(st) == ["WEST", "EAST", "WEST"] and np.allclose(km, [1, 1, 2])
    st, km = tether.nearest(x, y, sts, flat, huc, {"B": "EAST"})
    assert list(st) == ["WEST", "EAST", "EAST"] and np.isclose(km[2], 8)
    with pytest.raises(ValueError):
        tether.nearest(x, y, sts, flat, huc, {"B": "NOPE"})
    # a watershed no gauge stands for is left untethered; a full code wins over a prefix
    huc = np.array(["0301A", "0301B", "0208C"], dtype=object)
    st, km = tether.nearest(x, y, sts, flat, huc, {"0301*": "-", "0301B": "EAST"})
    assert list(st) == ["", "EAST", "WEST"] and np.isnan(km[0]) and np.isclose(km[1], 1)
    assert tether.rule("0301A", {"0301*": "-"}) == "" and tether.rule("0208C", {"0301*": "-"}) is None
    assert tether.overrides()["03010205*"] == "-"            # Back Bay and the North Landing River
    assert all(s["lat"] and not s["station"].startswith(("NN", "VB")) for s in tether.stations())


def test_station_levels_reads_the_published_navd_files(tmp_path):
    os.makedirs(tmp_path / "fore72"); os.makedirs(tmp_path / "7day")
    rows = [{"date_time_z": f"2026-11-26T{h:02d}:00:00Z", "SSforecast_ft": v, "SSforecast_p10_ft": v - 0.2, "SSforecast_p90_ft": v + 0.2}
            for h, v in enumerate([1.0, 2.0, 5.4, 3.0])]
    (tmp_path / "fore72" / "AAAA_fore72_NAVD.json").write_text(json.dumps(
        {"datum": "NAVD88", "units": "feet", "updated": "x", "guidance": "g", "flood_levels_ft": {"minor": 2.9}, "data": rows}))
    (tmp_path / "7day" / "AAAA_7day_NAVD.json").write_text(json.dumps(
        {"data": [{"date_time_z": "2026-11-25T23:54:00Z", "water_level_ft": 1.9}, {"date_time_z": "2026-11-26T00:00:00Z", "water_level_ft": None}]}))
    lv = status.station_levels(str(tmp_path), "AAAA")
    assert lv["peak_ft"] == 5.4 and lv["peak_time"].hour == 2 and np.isclose(lv["peak_p90_ft"], 5.6) and lv["obs_ft"] == 1.9
    (tmp_path / "fore72" / "BBBB_fore72_NAVD.json").write_text(json.dumps({"datum": "MLLW", "units": "feet", "data": rows}))
    with pytest.raises(ValueError):
        status.station_levels(str(tmp_path), "BBBB")


def test_page_packing_round_trips():
    pytest.importorskip("scipy")
    from inundation import page
    import base64
    a = page.hundredths([1.234, np.nan, -2.0])
    assert list(a) == [123, page.NO_LEVEL, -200]
    assert list(np.frombuffer(base64.b64decode(page.b64(a, "<i2")), "<i2")) == list(a)
    line = np.array([[0, 0], [5, 0.05], [10, 0], [10, 10]], float)
    assert len(page.simplify(line, 1.0)) == 3 and len(page.simplify(line, 0.01)) == 4
    assert page.road_class("S1100HOV") == 2 and page.road_class("S1200PRI") == 1 and page.road_class("S1400") == 0


def test_a_pipe_carries_water_under_the_berm():
    pytest.importorskip("scipy")
    from inundation import drainage
    dem = np.full((5, 12), 60.0, np.float32)
    dem[:, 0] = -5.0                 # tidal water on the west edge
    dem[2, 1:4] = 1.0                # low ground joined to it
    dem[2, 4] = 6.0                  # a berm
    dem[2, 5:8] = 0.5                # a low spot behind the berm
    dem[2, 9:11] = 2.0               # a second low spot, behind a floodwall
    lon = lambda c: c + 0.5          # cell centres as coordinates (identity transform, origin (0, 5))
    pipe = lambda c0, c1: {"geometry": {"type": "LineString", "coordinates": [[lon(c0), 2.5], [lon(c1), 2.5]]}, "properties": {}}
    inlet = lambda c, kind="Catch Basin": {"geometry": {"type": "Point", "coordinates": [lon(c), 2.5]}, "properties": {"T": kind}}
    layers = {"pipes": [pipe(2, 6), pipe(3, 9)], "structures": [inlet(2), inlet(6), inlet(3), inlet(9)]}
    src = {"structure_type_field": "T", "closed_types": ["manhole"]}
    same = lambda x, y: (x, y)
    net = drainage.network(layers, same, src)
    assert net["counts"]["networks"] == 2
    surface, piped = depth.connect_index(dem, links=drainage.links(net, dem, (0, 5), window=0))
    lev = depth.LEVELS_FT
    assert lev[surface[2, 6]] == 6.0                 # over the surface: only once the berm is under water
    assert lev[piped[2, 6]] == 1.0                   # through the pipe: as soon as the inlet at the other end is
    assert lev[piped[2, 10]] == 2.0 and surface[2, 10] == depth.NEVER
    # a manhole lid is not an opening
    layers["structures"][1] = inlet(6, "Manhole")
    net = drainage.network(layers, same, src)
    _, piped = depth.connect_index(dem, links=drainage.links(net, dem, (0, 5), window=0))
    assert lev[piped[2, 6]] == 6.0


def test_a_floodwall_holds_the_water_and_cuts_the_pipes():
    pytest.importorskip("scipy")
    pytest.importorskip("shapely")
    from shapely.geometry import LineString, box
    from inundation import barriers, drainage
    dem = np.full((5, 12), 60.0, np.float32)
    dem[:, 0] = -5.0                 # tidal water on the west edge
    dem[:, 1:] = 1.0                 # flat low ground all the way east
    wall = barriers.cells([LineString([(6.5, -3), (6.5, 9)])], dem.shape, (0, 5), half=0)
    assert sorted(zip(*wall)) == [(r, 6) for r in range(5)]            # the line's cells, clipped to the grid
    open_, held = depth.connect_index(dem), depth.connect_index(dem, blocked=wall)
    lev = depth.LEVELS_FT
    assert lev[open_[2, 9]] == 1.0 and lev[held[2, 3]] == 1.0 and held[2, 9] == depth.NEVER and held[2, 6] == depth.NEVER
    # a pipe from the wet side to the dry side is cut at the leveed area
    pt = lambda c: {"geometry": {"type": "Point", "coordinates": [c + 0.5, 2.5]}, "properties": {}}
    layers = {"pipes": [{"geometry": {"type": "LineString", "coordinates": [[3.5, 2.5], [9.5, 2.5]]}, "properties": {}}], "structures": [pt(3), pt(9)]}
    same = lambda x, y: (x, y)
    net = drainage.network(layers, same)
    _, through = depth.connect_index(dem, blocked=wall, links=drainage.links(net, dem, (0, 5), window=0))
    assert lev[through[2, 9]] == 1.0                                   # with no leveed area the pipe passes under the wall
    net = drainage.network(layers, same, protected=[box(7, 0, 12, 5)])
    assert net["counts"]["cut_at_barriers"] == 1 and list(net["zone"]) == [0, 1]
    _, cut = depth.connect_index(dem, blocked=wall, links=drainage.links(net, dem, (0, 5), window=0))
    assert cut[2, 9] == depth.NEVER


def test_depth_damage_follows_the_corps_tables():
    from inundation import damage
    # EGM 01-03 / 04-01, structure, no basement: the water measured from the first floor
    assert damage.structure_fraction(0, "one_story") == pytest.approx(0.134)
    assert damage.structure_fraction(1, "one_story") == pytest.approx(0.233)
    assert damage.structure_fraction(3, "two_story") == pytest.approx(0.263)
    assert damage.structure_fraction(-1, "split_level") == pytest.approx(0.064)
    assert damage.structure_fraction(0.5, "one_story") == pytest.approx((0.134 + 0.233) / 2)
    assert damage.structure_fraction(-2, "one_story") == 0 and damage.structure_fraction(-5, "two_story") == 0
    assert damage.structure_fraction(30, "one_story") == pytest.approx(0.807)
    assert damage.structure_fraction(np.nan, "one_story") == 0
    # Hazus default first floor heights for a crawlspace: 3 ft before the first flood map, 4 ft after
    assert list(damage.first_floor_ft(np.array([1950.0, 1995.0, np.nan]), 1979)) == [3, 4, 3]
    assert damage.first_floor_ft(1950, 1979, "slab") == 1
    usd, frac, above, ffh = damage.estimate(4.0, 200000, 1950, "one_story", 1979)
    assert (round(float(usd)), float(above), float(ffh)) == (46600, 1.0, 3.0) and float(frac) == pytest.approx(0.233)
    assert float(damage.estimate(0.0, 200000, 1950, "one_story", 1979)[0]) == 0      # dry ground: nothing, whatever the floor


def test_assessed_values_go_from_the_parcel_to_its_buildings():
    pytest.importorskip("shapely")
    from shapely.geometry import box, mapping
    from inundation import value
    src = {"parcels": {"key": "GPIN"}, "residential_uses": ["single family"],
           "one_story_classes": ["^510 "], "two_story_classes": ["^513 "], "split_level_classes": []}
    records = [
        {"key": "A", "use": "Single Family", "class": "513 Two story", "year_built": "1921", "living_area": "2,400",
         "improvement_value": "300000", "total_value": "450000", "zip": "23507-1234"},
        {"key": "B", "use": "Office", "class": "400 Commercial", "year_built": "1988", "living_area": "",
         "improvement_value": "900000", "total_value": "1500000", "zip": "23510"},
        {"key": "B", "use": "Office", "class": "400 Commercial", "year_built": "1990", "living_area": "",
         "improvement_value": "100000", "total_value": "100000", "zip": "23510"}]
    rolled = value.roll(records, src)
    assert rolled["A"]["residential"] and rolled["A"]["curve"] == "two_story" and rolled["A"]["zip"] == "23507"
    assert rolled["B"]["impr"] == 1000000 and rolled["B"]["year"] == 1988 and not rolled["B"]["residential"]
    parcels = [{"geometry": mapping(box(0, 0, 40, 40)), "properties": {"GPIN": "A"}},
               {"geometry": mapping(box(50, 0, 90, 40)), "properties": {"GPIN": "B"}}]
    house, shed, office, nowhere = box(5, 5, 20, 20), box(30, 30, 35, 35), box(55, 5, 80, 30), box(200, 200, 210, 210)
    c = value.attach([house, shed, office, nowhere, None], parcels, lambda x, y: (x, y), rolled, src, {"23507": [410000.0, "2026-10"]})
    assert list(c["parcel"]) == ["A", "A", "B", "", ""]
    assert c["value_bldg"][0] == 270000 and c["value_bldg"][1] == 30000 and c["value_bldg"][2] == 1000000   # by footprint area
    assert c["value_total"][0] == 450000 and c["year_built"][0] == 1921 and c["curve"][0] == "two_story"
    assert list(c["residential"]) == [1, 1, 0, 0, 0] and c["zhvi"][0] == 410000 and np.isnan(c["zhvi"][2])
    assert np.isnan(c["value_bldg"][3]) and np.isnan(c["year_built"][4])


def test_one_pass_spread_matches_labelling_level_by_level():
    pytest.importorskip("scipy")
    pytest.importorskip("numba")
    rng = np.random.default_rng(7)
    for trial in range(6):
        dem = rng.uniform(-3, 12, (70, 90)).astype(np.float32)
        dem = (dem + np.roll(dem, 1, 0) + np.roll(dem, 1, 1) + np.roll(dem, (1, 1), (0, 1))) / 4      # some structure
        dem[:, :3] = -6.0                                   # the sea
        dem[rng.random(dem.shape) < 0.02] = np.nan          # holes in the data
        n = 60
        links = (rng.integers(0, 70, n), rng.integers(3, 90, n), rng.integers(0, 9, n))
        wall = (np.arange(10, 60), np.full(50, 40)) if trial % 2 else None
        a = depth.connect_index(dem, links=links, blocked=wall, method="label")
        b = depth.connect_index(dem, links=links, blocked=wall, method="spread")
        assert (a[0] == b[0]).all() and (a[1] == b[1]).all()
        assert (depth.connect_index(dem, blocked=wall, method="label") == depth.connect_index(dem, blocked=wall, method="spread")).all()
        assert (b[1] <= b[0]).all() and (b[1] < b[0]).any()  # pipes only bring the water sooner


def test_one_pass_spread_can_keep_its_grids_on_disk(tmp_path, monkeypatch):
    pytest.importorskip("numba")
    from inundation import spread
    rng = np.random.default_rng(3)
    dem = rng.uniform(-3, 12, (60, 80)).astype(np.float32)
    dem[:, :3] = -6.0
    links = (rng.integers(0, 60, 40), rng.integers(3, 80, 40), rng.integers(0, 6, 40))
    want = depth.connect_index(dem, links=links, method="spread")
    monkeypatch.setattr(spread, "BIG", 0)                    # as for a city too large for memory
    np.save(tmp_path / "dem.npy", dem)
    paths = (str(tmp_path / "surface.npy"), str(tmp_path / "piped.npy"))
    got = depth.connect_index(np.load(tmp_path / "dem.npy", mmap_mode="r"), links=links, method="spread", store=paths)
    assert (got[0] == want[0]).all() and (got[1] == want[1]).all()
    assert (np.load(paths[0]) == want[0]).all() and (np.load(paths[1]) == want[1]).all()
    assert sorted(f.name for f in tmp_path.iterdir()) == ["dem.npy", "piped.npy", "surface.npy"]
    one = depth.connect_index(dem, method="spread", store=str(tmp_path / "only.npy"))
    assert (one == want[0]).all()


def test_elevation_certificate_floor_replaces_the_assumed_height():
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import box, mapping
    from inundation import damage, ffe
    same = lambda x, y: (x, y)
    bgeoms = [box(0, 0, 10, 10), box(30, 0, 40, 10), None, box(60, 0, 70, 10)]
    cert = lambda g, v, when, unit="Feet": {"geometry": mapping(g), "properties": {"FFE": v, "ISSUE_DATE": when, "LENGTH_UNIT": unit}}
    certs = [cert(box(1, 1, 9, 9), 9.0, 1), cert(box(2, 2, 8, 8), 11.5, 5),          # two on the first: the later one wins
             cert(box(41, 0, 45, 10), 8.0, 1),                                       # 3 m off the second: still its certificate
             cert(box(100, 0, 110, 10), 7.0, 1), cert(box(61, 1, 69, 9), None, 1)]   # nothing near; no elevation
    floor = ffe.attach(certs, bgeoms, same)
    assert floor[0] == 11.5 and floor[1] == 8.0 and np.isnan(floor[2]) and np.isnan(floor[3])
    # 4 ft of water over ground at 4 ft: the surveyed floor at 11.5 ft is far above it, the assumed 3 ft floor is not
    ground = 4.0
    usd, frac, above, ffh = damage.estimate([4.0, 4.0], [200000, 200000], [1950, 1950], "one_story", 1979,
                                            first_floor=[floor[0] - ground, np.nan])
    assert ffh[0] == 7.5 and ffh[1] == 3 and above[0] == -3.5 and usd[0] == 0 and above[1] == 1 and usd[1] > 0


def test_flood_raster_is_an_overview_plus_tiles(tmp_path):
    Image = pytest.importorskip("PIL.Image")                          # with inundation/requirements.txt, like scipy above
    from inundation import page
    rng = np.random.default_rng(3)
    nh, nw = page.TILE + 76, 2 * page.TILE + 52                       # 2 rows and 3 columns of tiles, overview at 1/2
    idx = rng.integers(0, 49, (nh, nw)).astype(np.uint8)
    zone = rng.integers(0, 4, (nh, nw)).astype(np.uint8)
    gnd = rng.integers(0, 255, (nh, nw)).astype(np.uint8)
    idx[:8, :8], zone[:8, :8], gnd[:8, :8] = 255, page.NO_ZONE, 40    # a corner never reached, in a watershed left out
    r = page.write_raster(str(tmp_path), idx, zone, gnd)
    assert (r["rows"], r["cols"], r["overview"]["factor"]) == (2, 3, 2)
    assert (r["overview"]["width"], r["overview"]["height"]) == (-(-nw // 2), -(-nh // 2))
    got = np.zeros((nh, nw, 3), np.uint8)
    for row in range(r["rows"]):
        for col in range(r["cols"]):
            t = np.asarray(Image.open(tmp_path / "tiles" / f"t_{row}_{col}.png"))
            got[row * page.TILE:row * page.TILE + t.shape[0], col * page.TILE:col * page.TILE + t.shape[1]] = t
    assert np.array_equal(got, np.dstack([idx, zone, gnd]))           # nothing lost at full detail
    over = np.asarray(Image.open(tmp_path / r["overview"]["file"]))
    assert tuple(over[0, 0]) == (255, page.NO_ZONE, 40)               # a block keeps one of its own cells, all three bands
    assert tuple(over[10, 10]) in {tuple(v) for v in np.dstack([idx, zone, gnd])[20:22, 20:22].reshape(-1, 3)}


def test_plot_gauge_is_the_nearest_noaa_tide_gauge():
    from inundation import page
    g, ref = page.plot_gauge(36.85, -76.29)                           # downtown Norfolk
    assert g["id"] in ("SWPT", "MNPT") and g["noaa_id"] in ("8638610", "8639348")
    assert ref["id"] == "SWPT" and ref["name"] == "Sewells Point" and abs(ref["navd88_above_mllw_ft"] - 1.61) < 0.02
    assert page.plot_gauge(37.25, -76.50)[0]["id"] != g["id"]         # Gloucester Point is not Norfolk's gauge
    assert page.plot_gauge(36.75, -76.02)[0]["id"] == "MNPT"          # Virginia Beach's centre is nearest Money Point ...
    assert page.plot_gauge(36.75, -76.02, inside=["CBBT", "BBIV"])[0]["id"] == "CBBT"   # ... but its own NOAA gauge goes first


def test_a_past_flood_gives_each_gauge_its_own_record_or_the_nearest(tmp_path, monkeypatch):
    from inundation import history
    monkeypatch.setattr(history, "EVENTS", str(tmp_path))
    (tmp_path / "events.json").write_text(json.dumps([{"id": "storm", "name": "Test Storm", "search": ["2003-09-17", "2003-09-20"]}]))
    peak = datetime(2003, 9, 18, 21, 0, tzinfo=timezone.utc)
    t0 = peak - timedelta(hours=history.HALF_H)
    rows = []
    for k in range(721):                                              # the whole 72 hours at Sewells Point, peak 6.28 ft
        rows.append(("8638610", t0 + timedelta(minutes=6 * k), 6.28 - abs(k - 360) * 0.01))
    for k in range(300):                                              # a gauge the storm took out six hours before the peak
        rows.append(("LOST", t0 + timedelta(minutes=6 * k), 3.0))
    for k in range(0, 721, 10):                                      # an hourly gauge with a four-hour hole after the peak
        if not 400 < k < 440:
            rows.append(("HOURLY", t0 + timedelta(minutes=6 * k), 5.0))
    gauges = {"8638610": {"name": "Sewells Point", "lat": 36.943, "lon": -76.329, "step_min": 6},
              "LOST": {"name": "Lost Gauge", "lat": 37.25, "lon": -76.50, "step_min": 6},
              "HOURLY": {"name": "Hourly Gauge", "lat": 37.60, "lon": -76.29, "step_min": 60}}
    doc = history.write_event(history.events()[0], peak, 6.28, rows, gauges)
    assert doc["window"] == ["2003-09-17T09:00Z", "2003-09-20T09:00Z"] and doc["gauges"]["LOST"]["readings"] == 300
    page_doc = history.for_page([{"station": "SWPT", "lat": 36.943, "lon": -76.329}, {"station": "YRCG", "lat": 37.25, "lon": -76.48},
                                 {"station": "HOURLY", "lat": 37.60, "lon": -76.29}], coops_of={"SWPT": "8638610"})
    ev = page_doc["events"][0]
    assert (ev["n"], ev["peak_i"], ev["step_min"], ev["gauges"], ev["gauges_recorded"]) == (721, 360, 6, 2, 3)
    sw, yr, hr = (ev["stations"][s] for s in ("SWPT", "YRCG", "HOURLY"))
    assert sw["own"] and sw["from"] == "Sewells Point" and sw["f"][360] == 6.28 and sw["f"][0] == pytest.approx(2.68)
    assert not yr["own"] and yr["from_id"] == "8638610" and yr["km"] > 30  # the lost gauge stands for nothing, not even its neighbour
    assert hr["own"] and hr["f"][5] == 5.0                             # between two hourly readings
    assert hr["f"][420] is None and hr["f"][400] == 5.0 and hr["f"][440] == 5.0   # nothing drawn across the hole
    assert "runs" not in ev                                            # no forecasts kept for this storm
    import csv
    import gzip
    with gzip.open(tmp_path / "storm" / "forecasts.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cycle", "station", "date_time_z", "forecast_ft_navd88", "p10_ft", "p90_ft"])
        for cyc, top in (("2003-09-18T00Z", 5.5), ("2003-09-18T18Z", 6.0), ("2003-09-19T00Z", 4.0)):   # the last one is after the peak
            c0 = datetime.strptime(cyc, "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)
            for k in range(0, 300):
                w.writerow([cyc, "SWPT", history.iso(c0 + timedelta(minutes=6 * k)), top - abs(k - 100) * 0.01, "", ""])
    ev = history.for_page([{"station": "SWPT", "lat": 36.943, "lon": -76.329}], coops_of={"SWPT": "8638610"}, plot="SWPT")["events"][0]
    assert [r["cycle"] for r in ev["runs"]] == ["2003-09-18T00Z", "2003-09-18T18Z"] and ev["runs_at"] == "SWPT"
    assert ev["runs"][1]["max_ft"] == 6.0 and ev["runs"][1]["f"][0] is None and len(ev["runs"][0]["f"]) == 721


def test_a_line_drawn_across_an_outage_is_not_a_record():
    from inundation import history
    t0 = datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc)
    tide = [(t0 + timedelta(minutes=6 * k), round(2.5 * np.sin(k / 119 * 2 * np.pi) + 0.004 * ((k * 7) % 5 - 2), 3)) for k in range(240)]
    assert history.unbridged(tide) == tide                             # a gauge's own readings stay, even near mid-tide
    a, b = tide[40], tide[150]                                         # the gauge is down for eleven hours ...
    line = [(a[0] + timedelta(minutes=6 * k), round(a[1] + (b[1] - a[1]) * k / 110, 3)) for k in range(1, 110)]
    kept = history.unbridged(tide[:41] + line + tide[150:])            # ... and the table draws a line across
    assert kept == tide[:41] + tide[150:]
    short = [(a[0] + timedelta(minutes=6 * k), round(a[1] + (tide[46][1] - a[1]) * k / 6, 3)) for k in range(1, 6)]
    assert history.unbridged(tide[:41] + short + tide[46:]) == tide[:41] + short + tide[46:]   # half an hour's fill is left alone


def test_page_coordinates_fit_a_long_county():
    """Half-metre units in 16 bits end at 32.7 km: Gloucester (42 km) drew everything past that on one line."""
    from inundation import page
    assert page.unit_for((0, 0, 16000, 17500)) == 0.5
    assert page.unit_for((0, 0, 36915, 42325)) == 1.0
    assert page.unit_for((0, 0, 70000, 140000)) == 4.0
    unit = page.unit_for((0, 0, 36915, 42325))
    far = page.units([42000.0], 0, unit)
    assert far[0] == 42000 and far[0] < 65535


def test_statewide_curves_count_each_gauges_roads_and_buildings_at_every_level():
    from inundation import site
    roads = {"station": np.array(["AAAA", "AAAA", "AAAA", "ZZZZ"], dtype=object), "bridge": np.array([0, 0, 1, 0.0]),
             "ground_ft": np.array([3.0, 3.0, 3.0, 1.0]), "connect_ft": np.array([3.0, 5.0, 3.0, 1.0]),
             "length_m": np.array([site.M_PER_MILE, site.M_PER_MILE / 2, 50.0, site.M_PER_MILE / 4])}
    bldgs = {"station": np.array(["AAAA", "AAAA"], dtype=object), "over_water": np.array([0, 1.0]),
             "ground_ft": np.array([4.0, 0.0]), "connect_ft": np.array([4.0, 0.0])}
    c = site.curves(roads, bldgs, {})
    lv = list(depth.LEVELS_FT)
    assert sorted(c) == ["AAAA", "ZZZZ"] and all(len(c["AAAA"][k]) == len(lv) == 49 for k in "fpb") and "d" not in c["AAAA"]
    at = lambda st, k, ft: c[st][k][lv.index(ft)]                      # noqa: E731
    assert at("AAAA", "f", 3.0) == 0 and at("AAAA", "p", 3.25) == 1.0 and at("AAAA", "p", 3.5) == 1.0
    assert at("AAAA", "f", 3.75) == 1.0 and at("AAAA", "p", 3.75) == 0      # the bridge is never counted
    assert at("AAAA", "f", 4.75) == 1.0 and at("AAAA", "f", 5.0) == 1.5     # the cut-off piece floods when water gets to it
    assert at("AAAA", "b", 4.5) == 0 and at("AAAA", "b", 4.75) == 1 and at("AAAA", "b", 10.0) == 1
    assert at("ZZZZ", "f", 1.75) == 0.25 and c["ZZZZ"]["b"] == [0] * 49


def test_the_list_of_areas_names_each_locality_once_and_the_workflow_reads_it():
    from inundation import site
    rows = site.areas()
    assert len({r["name"] for r in rows}) == len({r["fips"] for r in rows}) == len(rows) >= 18
    assert all(r["fips"].startswith("51") and len(r["fips"]) == 5 and r["title"] and r["region"] for r in rows)
    assert all(r["name"] == r["name"].lower() and " " not in r["name"] for r in rows)       # a folder and a URL
    assert {"norfolk", "poquoson", "gloucester", "york"} <= {r["name"] for r in rows}
    with open(os.path.join(os.path.dirname(site.__file__), "templates", "statewide.html")) as fh:
        html = fh.read()
    assert "__LEAFLET__" in html and "__LEAFLET_CSS__" in html and "areas.json" in html


def test_statewide_map_gets_a_small_far_view_of_an_area(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    from inundation import site
    os.makedirs(tmp_path / "tiles")
    a = np.zeros((900, 1500, 3), np.uint8)
    a[::3, ::3] = (7, 1, 40)
    Image.fromarray(a, "RGB").save(tmp_path / "tiles" / "z0.png")
    raster = {"dir": "tiles", "overview": {"file": "tiles/z0.png", "factor": 4, "width": 1500, "height": 900}}
    small = site.small_overview(str(tmp_path), raster)
    assert small == {"file": "tiles/z1.png", "factor": 12, "width": 500, "height": 300}
    b = np.asarray(Image.open(tmp_path / "tiles" / "z1.png"))
    assert b.shape == (300, 500, 3) and (b == a[1::3, 1::3]).all()          # cells are picked, never averaged
    raster["overview"].update(width=400, height=300)
    assert site.small_overview(str(tmp_path), raster) == raster["overview"]


def test_a_gauge_without_a_forecast_follows_the_nearest_gauge_that_has_one(tmp_path):
    os.makedirs(tmp_path / "fore72")
    t0 = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    rows = [{"date_time_z": (t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ"), "SSforecast_ft": 2.0 + h / 10} for h in range(4)]
    for st in ("USBK", "SWPT"):
        with open(tmp_path / "fore72" / f"{st}_fore72_NAVD.json", "w") as fh:
            json.dump({"datum": "NAVD88", "units": "feet", "data": rows}, fh)
    said = []
    levels, follows, missing = status.read_levels(str(tmp_path), ["BRDH", "USBK", "NOPE"], log=said.append)
    assert follows == {"BRDH": "USBK"} and missing == ["NOPE"]            # Back River's nearest gauge is Brick Kiln Creek
    assert levels["BRDH"]["follows"] == "USBK" and levels["BRDH"]["station"] == "BRDH" and "follows" not in levels["USBK"]
    assert levels["BRDH"]["peak_ft"] == levels["USBK"]["peak_ft"] == 2.3 and len(said) == 2 and "follow USBK" in said[0]


def two_estuaries():
    """Sea on the west edge, a second estuary running off the south edge behind a ridge, and a pond inside."""
    dem = np.full((40, 60), 30.0, np.float32)
    dem[:, :5] = -6.0                          # the sea: the largest body
    dem[:, 5:10] = 1.0                         # its shore
    dem[25:, 30:40] = -4.0                     # an estuary that meets the sea outside the grid
    dem[20:25, 30:40] = 2.0                    # its marsh
    dem[5:10, 45:50] = -4.0                    # a pond: the sea has to reach it
    dem[5:10, 50:55] = 2.0
    return dem


def test_every_estuary_that_runs_off_the_grid_is_tidal_water():
    pytest.importorskip("scipy")
    dem = two_estuaries()
    seeds = depth.tidal_seeds(dem, min_m2=100)
    assert len(seeds) == 2 and seeds[0][1] < 5 and seeds[1][0] == 39 and 30 <= seeds[1][1] < 40
    assert len(depth.tidal_seeds(dem, min_m2=200)) == 1                  # too small to be an estuary: not a seed
    lv = list(depth.LEVELS_FT)
    for method in ("label", "spread"):
        if method == "spread":
            pytest.importorskip("numba")
        one = depth.connect_index(dem, seed=seeds[0], method=method)
        both = depth.connect_index(dem, seed=seeds, method=method)
        assert one[30, 35] == depth.NEVER and one[22, 35] == depth.NEVER   # with the sea alone the estuary is land
        assert both[30, 35] == 0 and both[22, 35] == lv.index(2.0)          # its marsh floods at 2 ft
        assert both[7, 47] == depth.NEVER and both[7, 52] == depth.NEVER    # the pond is behind 30 ft of ground
        assert both[3, 7] == one[3, 7] == lv.index(1.0)


def test_water_leaving_the_grid_in_a_watershed_without_a_gauge_is_not_the_sea():
    pytest.importorskip("scipy"); pytest.importorskip("shapely")
    from inundation import prep
    dem = np.full((400, 600), 30.0, np.float32)
    dem[:, :200] = -6.0
    dem[150:, 300:500] = -4.0                                             # 50,000 cells off the south edge
    box = lambda x0, x1, code: {"type": "Feature", "properties": {"huc12": code, "name": code},     # noqa: E731
                                "geometry": {"type": "Polygon", "coordinates": [[[x0, 0], [x1, 0], [x1, 400], [x0, 400], [x0, 0]]]}}
    hucs = [box(0, 250, "020801010101"), box(250, 600, "030102050101")]
    said = []
    kept = prep.sea_cells(dem, (0, 400), hucs, {"03010205*": "-"}, flat, said.append)
    assert len(kept) == 1 and kept[0][1] < 200 and "030102050101" in said[0]
    assert len(prep.sea_cells(dem, (0, 400), hucs, {}, flat)) == 2


def test_the_union_find_takes_memory_only_where_it_is_written():
    from inundation import spread
    a = spread.sparse(50_000_000, np.uint32)
    a[7] = 9; a[-1] = 3
    assert a.dtype == np.uint32 and len(a) == 50_000_000 and a[7] == 9 and a[-1] == 3 and a[1_000_000] == 0
