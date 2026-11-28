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
    # several gauges in a rule: the nearest of those, whatever else is nearer
    far = sts + [{"station": "NORTH", "name": "", "lon": 1000.0, "lat": 100.0}]       # nearest to the first point
    st, km = tether.nearest(x, y, far, flat, huc, {"0301A": "WEST|EAST", "0208C": "EAST|NORTH"})
    assert list(st) == ["WEST", "EAST", "NORTH"] and np.isclose(km[0], 1)
    assert tether.group("WACH|CHIV") == ["WACH", "CHIV"] and tether.group("") == [] and tether.group(None) == []
    ok = {s["station"] for s in tether.stations()}
    assert all(set(tether.group(v)) <= ok for v in tether.overrides().values() if v != "-")
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
    assert len({r["name"] for r in rows}) == len(rows) >= 18
    from inundation import prep
    parts = {}
    for r in rows:
        parts.setdefault(r["fips"], []).append(prep.parse_box(r["box"]))
    for fips, boxes in parts.items():                 # a locality once, or in parts that each have a box and share nothing
        assert len(boxes) == 1 or all(boxes), fips
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (fips, a, b)
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


def test_the_storms_are_listed_newest_first():
    from inundation import history
    ids = [e["id"] for e in history.events()]
    assert ids[0] == "2026-thanksgiving-noreaster" and ids[-1] == "1933-hurricane" and "2012-sandy" in ids
    assert ids == sorted(ids, reverse=True)                            # every id starts with its year


def test_a_reports_mark_takes_the_viewers_details_and_keeps_its_own_id():
    from inundation import history

    def mark(i, lat, ft, **kw):
        return {**{k: "" for k in history.MARK_COLS}, "id": i, "kind": "hwm", "lat": lat, "lon": -76.3, "elev_ft_navd88": ft, **kw}
    report = [mark("HWM-VA-NFK-005", 36.8588, 4.88, wave="yes", source="report"), mark("HWM-VA-NFK-009", 36.90, 5.31, source="report")]
    viewer = [mark("VANOR07695-6598", 36.85881, 4.9, quality="Good", within_ft=0.1, type="Stain line", environment="Coastal",
                   place="Fairfax Ave.", source="viewer"),             # the viewer keeps 0.1 ft: the same mark
              mark("VANOR07699-6601", 36.8588, 6.4, quality="Fair", source="viewer")]      # same spot, another height: its own mark
    out = history.merge_marks(report, viewer)
    assert [m["id"] for m in out] == ["HWM-VA-NFK-005", "HWM-VA-NFK-009", "VANOR07699-6601"]
    assert (out[0]["quality"], out[0]["type"], out[0]["wave"], out[0]["place"]) == ("Good", "Stain line", "yes", "Fairfax Ave.")
    assert out[0]["source"] == "report; viewer" and out[1]["source"] == "report" and out[1]["quality"] == ""
    assert history._within("Good: +/- 0.10 ft") == ("Good", 0.1) and history._within("Unknown/Historical") == ("Unknown/Historical", None)


def test_marks_on_a_page_are_the_tides_marks_inside_its_frame(tmp_path, monkeypatch):
    pytest.importorskip("pyproj")
    import csv
    from inundation import history
    monkeypatch.setattr(history, "EVENTS", str(tmp_path))
    (tmp_path / "storm").mkdir()
    rows = [("in", "hwm", 36.95, -76.25, 6.21, "7.05", "NGVD29", "Coastal", "", "Good", "0.1"),
            ("sensor", "sensor", 36.86, -76.30, 5.94, "5.94", "NAVD88", "", "", "", ""),
            ("river", "hwm", 36.90, -76.28, 6.0, "6.0", "NAVD88", "Riverine", "", "", ""),
            ("runup", "hwm", 36.96, -76.26, 14.2, "14.2", "NAVD88", "Coastal", "yes", "", ""),
            ("outside", "hwm", 37.90, -75.40, 5.0, "5.0", "NAVD88", "Coastal", "", "", "")]
    with open(tmp_path / "storm" / "marks.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, history.MARK_COLS)
        w.writeheader()
        for i, kind, lat, lon, ft, raw, datum, env, wave, q, u in rows:
            w.writerow({**{k: "" for k in history.MARK_COLS}, "id": i, "kind": kind, "lat": lat, "lon": lon, "elev_ft_navd88": ft,
                        "elev_ft": raw, "datum": datum, "environment": env, "wave": wave, "quality": q, "within_ft": u,
                        "source": "viewer" if kind == "hwm" else "report"})
    frame = {"epsg": 32618, "x0": 370000.0, "ytop": 4100000.0, "unit_m": 0.5, "width_m": 30000.0, "height_m": 30000.0}
    marks, src, off = history.marks_for_page("storm", frame, top_ft=6.3)
    assert [m["n"] for m in marks] == ["in", "sensor"] and off == 2 and src == ["viewer", "report"]
    assert marks[0]["k"] == "h" and marks[0]["o"] == "7.05 ft NGVD29" and marks[0]["u"] == 0.1 and marks[0]["s"] == 0
    assert marks[1]["k"] == "s" and "o" not in marks[1] and marks[1]["s"] == 1
    assert 0 < marks[0]["x"] < 60000 and 0 < marks[0]["y"] < 60000      # page units of half a metre from the frame's corner
    assert history.marks_for_page("no-such-storm", frame, 6.3) == ([], [], 0)


def test_a_layer_its_city_took_down_comes_from_the_saved_copy(monkeypatch):
    from inundation import drainage, vgin
    src = drainage.sources()["51700"]

    def query(url, where, fields, log=None, geometry=True):
        if "Manhole" in url:
            raise RuntimeError(f"{url}/query: HTTP Error 404: Not Found")
        return []
    monkeypatch.setattr(vgin, "query", query)
    monkeypatch.setattr(vgin, "count", lambda url, where: 0)
    out = drainage.fetch(src)
    assert len(out["structures"]) == out["saved"][0]["features"] > 4000
    assert all(f["properties"]["_closed"] == 1 for f in out["structures"])      # manholes are never openings
    assert out["saved"][0]["read_utc"].startswith("2026-11-26")
    # a layer without a copy still stops the preparation
    src = {"pipes": [{"url": "https://example.invalid/Manhole/0"}]}
    try:
        drainage.fetch(src)
    except RuntimeError as e:
        assert "404" in str(e)
    else:
        raise AssertionError("a layer that cannot be read and has no copy must fail")


def test_open_water_as_one_node_gives_the_same_answer():
    pytest.importorskip("scipy")
    pytest.importorskip("numba")
    from inundation import spread
    rng = np.random.default_rng(11)
    for trial in range(4):
        dem = rng.uniform(-3, 12, (200, 260)).astype(np.float32)
        dem = (dem + np.roll(dem, 1, 0) + np.roll(dem, 1, 1) + np.roll(dem, (1, 1), (0, 1))) / 4
        dem[:, :100] = -8.0                                  # the bay
        dem[:, 100:103] = rng.uniform(-3, 6, (200, 3))       # a ragged shore
        dem[60:150, 150:240] = -5.0                          # a deep lake behind higher ground ...
        dem[55:60, 150:240] = dem[150:155, 150:240] = dem[55:155, 145:150] = dem[55:155, 240:245] = 4.0 + trial
        dem[100, 103:150] = [1.0, 3.0, 5.0, 11.0][trial]     # ... and a ditch to it that fills at some level, or never
        dem[(rng.random(dem.shape) < 0.01) & (dem > -4)] = np.nan            # holes in the data, on land
        n = 80
        links = (rng.integers(0, 200, n), rng.integers(0, 260, n), rng.integers(0, 12, n))
        wall = (np.arange(20, 180), np.full(160, 125)) if trial % 2 else None
        g = spread.own_level(dem, depth.LEVELS_FT, wall)
        lab, bodies, cells = spread.open_sea(g, keep=(links[0], links[1]))
        assert bodies >= 2 and cells > 20000 and (g == spread.SEA).sum() == cells
        assert not (g[links[0], links[1]] == spread.SEA).any()               # an opening keeps its own node
        a = depth.connect_index(dem, links=links, blocked=wall, method="label")
        b = depth.connect_index(dem, links=links, blocked=wall, method="spread")
        assert (a[0] == b[0]).all() and (a[1] == b[1]).all()
        assert (depth.connect_index(dem, blocked=wall, method="label") == depth.connect_index(dem, blocked=wall, method="spread")).all()
    # the lake is water from the start, but the sea reaches it only over its 7 ft bank in the last trial (ditch at 11 ft)
    assert depth.LEVELS_FT[b[0][100, 200]] == 7.0 and b[0][100, 50] == 0


def test_the_frame_is_cut_back_to_the_roads_and_buildings():
    from inundation import prep
    county = (0, 0, 90000, 60000)                              # far out into the bay and the ocean
    x, y = np.array([30000.0, 70000.0, np.nan]), np.array([5000.0, 58000.0, 100.0])
    assert prep.land_bounds(county, x, y) == (28500, 3500, 71500, 59500)
    assert prep.land_bounds(county, x, y, margin=40000) == county            # never larger than the county's own
    assert prep.land_bounds(county, [], []) == county
    feats = [{"geometry": {"type": "Polygon", "coordinates": [[[1.0, 2.0], [1.1, 2.0], [1.1, 2.1], [1.0, 2.0]]]}},
             {"geometry": {"type": "MultiPolygon", "coordinates": [[[[3.0, 4.0], [3.1, 4.0], [3.1, 4.1], [3.0, 4.0]]]]}},
             {"geometry": None}]
    bx, by = prep.first_points(feats, lambda lon, lat: (lon * 10, lat * 10))
    assert list(bx) == [10.0, 30.0] and list(by) == [20.0, 40.0]



def test_a_locality_too_large_for_one_frame_is_prepared_in_boxes(tmp_path):
    pytest.importorskip("shapely")
    from inundation import prep
    assert prep.parse_box("") is None and prep.parse_box(None) is None
    assert prep.parse_box("-75.95 37.3 -75.0 37.75") == prep.parse_box("-75.95,37.3,-75.0,37.75") == (-75.95, 37.3, -75.0, 37.75)
    with pytest.raises(ValueError):
        prep.parse_box("-75 37.3 -75.95 37.75")
    line = lambda lon, lat: {"geometry": {"type": "LineString", "coordinates": [[lon, lat], [lon + 0.5, lat + 0.5]]}}
    feats = [line(1.0, 1.0), line(1.9, 1.9), line(2.0, 1.0), line(1.0, 2.0), {"geometry": None}]
    south, north, east = (1.0, 1.0, 2.0, 2.0), (1.0, 2.0, 2.0, 3.0), (2.0, 1.0, 3.0, 3.0)
    got = [prep.in_box(feats, b) for b in (south, north, east)]
    assert got[0] == feats[:2] and got[1] == [feats[3]] and got[2] == [feats[2]]       # each one once, by where it starts
    loc = {"properties": {"NAMELSAD": "Split County"},
           "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]}}
    prep.write_outline(str(tmp_path / "o_outline.geojson"), loc, box=south)
    with open(tmp_path / "o_outline.geojson") as fh:
        g = json.load(fh)["geometry"]
    assert g["type"] == "MultiPolygon"
    xs, ys = zip(*g["coordinates"][0][0])
    assert (min(xs), min(ys), max(xs), max(ys)) == south
    from inundation import site
    assert len(site.outline(str(tmp_path), "o", {})) == 1


def test_water_under_a_deck_is_judged_by_the_gauges_own_high_tide(tmp_path):
    pytest.importorskip("scipy")
    from inundation import prep, tether
    with open(tmp_path / "LOWT.json", "w") as fh:
        json.dump({"MHHW": 1.82, "NAVD88": 1.30}, fh)              # 0.52 ft NAVD88, Tangier's
    with open(tmp_path / "HIGH.json", "w") as fh:
        json.dump({"MHHW": 3.9, "NAVD88": 1.8}, fh)                # 2.1 ft: never above the 1 ft limit
    with open(tmp_path / "NONE.json", "w") as fh:
        json.dump({"NAVD88": 1.0}, fh)
    tide = lambda st: tether.high_tide_ft(st, str(tmp_path))
    assert tide("LOWT") == pytest.approx(0.52) and tide("NONE") is None and tide("GONE") is None and tide("") is None
    limit = prep.over_water_ft(np.array(["LOWT", "HIGH", "NONE", "", "LOWT"], object), tide)
    assert list(np.round(limit, 2)) == [0.52, 1.0, 1.0, 1.0, 0.52]
    assert tether.high_tide_ft("SWPT") == pytest.approx(1.15, abs=0.02)      # the datum sheets the run uses
    assert tether.high_tide_ft("PRTV") < 0.6
    # an island lane at 0.8 ft is a road where the high tide is 0.52 ft and water under a deck where the limit is 1 ft
    n = 6
    pieces = {"SEG_TYPE": [None] * n, "xm": np.arange(n) * 100.0, "ym": np.zeros(n), "ST_FULL": ["MAIN ST"] * n,
              "road": np.arange(n)}
    ground = np.array([0.8, 0.8, 0.3, 0.8, 1.4, np.nan])
    none = np.zeros(n, bool)
    assert list(prep.bridges(pieces, ground, deck=none)) == [1, 1, 1, 1, 0, 1]
    assert list(prep.bridges(pieces, ground, np.full(n, 0.52), deck=none)) == [0, 0, 1, 0, 0, 1]


def test_a_deck_over_low_ground_is_found_from_the_roads_own_profile():
    """Interstate 95 by Pocahontas Island in Petersburg: VGIN does not code the overpass as a bridge, no water lies
    under it, and the elevation data has the low ground under the deck (3.3 to 4 ft between embankments at 45 ft)."""
    pytest.importorskip("scipy")
    from inundation import prep, segments

    def road(name, xs, y=0.0):
        return {"type": "Feature", "properties": {"ST_FULL": name, "SEG_TYPE": 99},
                "geometry": {"type": "LineString", "coordinates": [[x, y] for x in xs]}}

    # one street in two lines that meet at x = 400, the second drawn the other way; a second street that only dips;
    # a third that comes down a bank and stays low; a side street that meets the first at the same point
    feats = [road("I 95 NB", [0, 400]), road("I 95 NB", [800, 400]), road("VALLEY RD", [0, 800], 100.0),
             road("RIVER RD", [0, 800], 200.0), road("SIDE ST", [400, 400.0001], 0.0), road("FRANKLIN ST", [0, 800], 300.0)]
    feats[4]["geometry"]["coordinates"] = [[400, 0], [400, 60]]
    p = segments.split(feats, lambda x, y: (np.asarray(x, float), np.asarray(y, float)))
    x, y, name = p["xm"], p["ym"], np.array([str(v) for v in p["ST_FULL"]])
    ground = np.full(len(x), 45.0)
    i95 = name == "I 95 NB"
    ground[i95 & (x > 300) & (x < 420)] = 3.5                                          # under the deck
    up = i95 & (x >= 420) & (x < 570)
    ground[up] = 3.5 + (x[up] - 420) * 0.25                                            # the far slope, 7.5% up to 41 ft
    low = name == "FRANKLIN ST"                                                        # a low street that rises a little
    ground[low] = 8.0 + 2.5 * (x[low] > 500)
    ground[low & (np.abs(x - 302.5) < 0.1)] = 22.0                                     # under another road's deck, left in the data
    valley = name == "VALLEY RD"
    ground[valley] = 45 - 41.5 * np.clip(1 - np.abs(x[valley] - 400) / 300, 0, 1)      # 14% both ways: steep, but a road
    river = name == "RIVER RD"
    ground[river & (x > 300)] = 3.5                                                    # down a bank, and low from there on
    nb = prep.chain(p)
    at = np.flatnonzero(i95 & (np.abs(x - 397.5) < 0.1))[0]
    assert set(name[nb[at]]) == {"I 95 NB"} and abs(x[nb[at]] - 397.5).max() <= 5.01    # across the two lines' ends
    deck = prep.decks(p, ground)
    assert deck[i95 & (x > 300) & (x < 420)].all()                    # the low ground, across the two lines
    assert deck[i95 & (x >= 420) & (x < 560)].all()                   # and the slope below 40 ft
    assert not deck[i95 & ((x < 300) | (x > 572))].any()
    assert not deck[valley].any() and not deck[river].any() and not deck[name == "SIDE ST"].any() and not deck[low].any()
    code = prep.bridges(p, ground)
    assert (code[deck] == 4).all() and (code[~deck] == 0).all()


def test_fema_outlines_fill_in_where_vgin_has_no_footprint(monkeypatch):
    pytest.importorskip("shapely")
    from inundation import prep, vgin
    sq = lambda x, y, d=0.0001: {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}
    footprints = [{"properties": {"OBJECTID": 1, "SOURCEFEATUREID": "a"}, "geometry": sq(-76.3, 37.9)},
                  {"properties": {"OBJECTID": 2, "SOURCEFEATUREID": "b"}, "geometry": None}]
    fema = [{"properties": {"OBJECTID": 7, "BUILD_ID": 70}, "geometry": sq(-76.30005, 37.90005)},      # the same house
            {"properties": {"OBJECTID": 8, "BUILD_ID": 80}, "geometry": sq(-76.29988, 37.9)},        # 2 m beside it
            {"properties": {"OBJECTID": 9, "BUILD_ID": 90}, "geometry": sq(-76.2995, 37.9)},         # the neighbour's
            {"properties": {"OBJECTID": 10, "BUILD_ID": 100}, "geometry": None}]
    assert [f["properties"]["OBJECTID"] for f in vgin.untouched(fema, footprints)] == [9]
    assert [f["properties"]["OBJECTID"] for f in vgin.untouched(fema, [])] == [7, 8, 9]
    copy = lambda fs: json.loads(json.dumps(fs))
    monkeypatch.setattr(vgin, "count", lambda layer, where: len(footprints if layer == vgin.BUILDINGS else fema))
    monkeypatch.setattr(vgin, "query", lambda layer, where, fields, log=None: copy(footprints if layer == vgin.BUILDINGS else fema))
    said = []
    got = vgin.buildings("51133", said.append)
    assert [f["properties"]["OBJECTID"] for f in got] == [1, 2, 9] and got[2]["properties"]["FOOTPRINTS"] == vgin.USA_STRUCTURES_NAME
    assert got[2]["properties"]["SOURCEFEATUREID"] == "90" and "1 outlines away from any VGIN footprint" in said[-1]
    assert prep.building_source(got) == f"VGIN building footprints, and 1 {vgin.USA_STRUCTURES_NAME} outlines where VGIN has no footprint"
    assert prep.building_source(got[:2]) == "VGIN building footprints"
    assert prep.building_source(got[2:]).endswith("(VGIN has none here)")

    def down(layer, where):                                   # FEMA's service out: VGIN's footprints alone, and the log says so
        if layer == vgin.USA_STRUCTURES:
            raise RuntimeError("HTTP 503")
        return len(footprints)
    monkeypatch.setattr(vgin, "count", down)
    said = []
    assert len(vgin.buildings("51133", said.append)) == 2 and "WARNING" in said[-1]


def test_the_pages_carry_what_derek_asked_of_the_maps():
    here = os.path.join(os.path.dirname(__file__), "..", "inundation", "templates")
    with open(os.path.join(here, "map.html")) as fh:
        page = fh.read()
    with open(os.path.join(here, "statewide.html")) as fh:
        state = fh.read()
    for html in (page, state):
        assert "Flooded Building" not in html and "Flood Affected Structure" in html       # the name in the popup, no labels
        assert all(k in html for k in ('action:"#ffe600"', 'minor:"#ff9900"', 'moderate:"#e60000"', 'major:"#c800e6"'))
        assert "World_Imagery" in html and "World_Street_Map" in html
    assert 'data-b="plain"' in page and "function drawBase" in page and "function lonlat" in page
    assert "function selectGauge" in state and "fitBounds" in state and "function declutter" in state
    from inundation import tether
    sts = tether.stations()
    assert {"navd88_above_mllw_ft", "noaa"} <= set(sts[0]) and any(s["noaa"] for s in sts) and not all(s["noaa"] for s in sts)


def test_an_elevation_strip_cut_short_is_read_again(tmp_path):
    """A read from the bucket that ends early (King William, 28 Nov 2026) is tried again from the tile opened anew."""
    rasterio = pytest.importorskip("rasterio")
    from rasterio.errors import RasterioIOError
    from rasterio.transform import from_origin
    from rasterio.windows import Window
    from inundation import depth
    p = str(tmp_path / "t.tif")
    a = np.arange(48, dtype=np.float32).reshape(6, 8)
    with rasterio.open(p, "w", driver="GTiff", height=6, width=8, count=1, dtype="float32", crs="EPSG:26918",
                       transform=from_origin(400000, 4100006, 1, 1)) as w:
        w.write(a, 1)

    class Short:
        calls = 0

        def read(self, band, window=None):
            Short.calls += 1
            raise RasterioIOError("Read failed.")

    got = depth._read_strip(Short(), p, Window(2, 1, 3, 4), wait=0)
    assert Short.calls == 1 and (got == a[1:5, 2:5]).all()
    with pytest.raises(RasterioIOError):
        depth._read_strip(Short(), str(tmp_path / "missing.tif"), Window(0, 0, 1, 1), tries=2, wait=0)
    out, corner = depth.read_dem([p], (400000, 4100000, 400008, 4100006))
    assert out.shape == (6, 8) and corner == (400000, 4100006) and np.allclose(out, a * depth.M_TO_FT)


def test_an_upland_county_keeps_what_lies_near_low_ground(tmp_path):
    """--low: a coarse map of the low ground, its tidal parts, and the roads and buildings near them."""
    rasterio = pytest.importorskip("rasterio")
    pytest.importorskip("scipy")
    from rasterio.transform import from_origin
    from inundation import depth, prep, vgin
    a = np.full((4000, 3000), 30.0, np.float32)               # metres: upland
    a[3600:, :] = -1.0                                        # a river along the south edge, 400 m wide
    a[3300:3600, 1000:1010] = 1.0                             # a creek 10 m wide running north from it
    a[1000:1100, 500:600] = -5.0                              # a quarry floor below sea level, shut in
    a[10, 2500] = -32767.0                                    # no data is not low ground
    p = str(tmp_path / "t.tif")
    with rasterio.open(p, "w", driver="GTiff", height=4000, width=3000, count=1, dtype="float32", crs="EPSG:26918",
                       nodata=-32767.0, transform=from_origin(500000, 4104000, 1, 1)) as w:
        w.write(a, 1)
    whole = (500000, 4100000, 503000, 4104000)
    low = depth.low_grid([p], whole, step=50)
    assert low.shape == (80, 60) and low[72:].all() and low[66:72, 20].all() and not low[66:72, 21].any()
    assert low[20:22, 10:12].all() and not low[0].any() and low.sum() == 8 * 60 + 6 + 4
    assert not depth.low_grid([p], whole, limit_ft=-20.0, step=50).any()        # -5 m is -16.4 ft
    tidal = depth.tidal_low(low)
    assert tidal[72:].all() and tidal[66:72, 20].all() and not tidal[20:22, 10:12].any()    # the quarry is left out
    assert len(np.unique(tidal)) == 2 and not depth.tidal_low(np.zeros((4, 4), bool)).any()
    flat = lambda x, y: (np.asarray(x, float), np.asarray(y, float))
    road = lambda *pts: {"geometry": {"type": "LineString", "coordinates": [list(q) for q in pts]}}
    house = lambda x, y: {"geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 10, y], [x + 10, y + 10], [x, y]]]}}
    roads = [road((500100, 4100600), (500300, 4100700)),            # by the river
             road((501000, 4103900), (501000, 4101200)),            # comes down to within reach of the creek's head
             road((500500, 4103000), (500600, 4103000)),            # by the quarry only
             road((502500, 4103900), (502900, 4103900))]            # upland
    bldgs = [house(502000, 4100900), house(500550, 4102950), house(502000, 4103500)]
    kr, kb, frame, parts = prep.near_low(roads, bldgs, tidal, whole, flat, margin=600, step=50)
    assert kr == roads[:2] and kb == bldgs[:1] and parts == 1
    assert frame == (500000, 4100000, 503000, 4104000)              # the whole river, and the road that runs north
    kr, kb, frame, parts = prep.near_low(roads[:1], bldgs[:1], tidal, whole, flat, margin=600, step=50)
    assert frame == (500000, 4100000, 503000, 4101500)              # the low part whole and the house, plus the margin
    assert prep.near_low(roads[3:], bldgs[2:], tidal, whole, flat, margin=600, step=50)[:2] == ([], [])
    # a locality the layer holds in two rows is one outline, its main body first
    sliver = {"type": "Feature", "properties": {"NAME": "X"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]}}
    body = {"type": "Feature", "properties": {"NAME": "X"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [9, 0], [9, 9], [5, 12], [0, 9], [0, 0]]]}}
    one = vgin.one_feature([sliver, body])
    assert one["geometry"]["type"] == "MultiPolygon" and len(one["geometry"]["coordinates"]) == 2
    assert one["geometry"]["coordinates"][0] == body["geometry"]["coordinates"] and vgin.one_feature([body]) is body
    assert prep.bounds_of(one, flat, buffer=0) == (0, 0, 9, 12)
    rows = prep_areas()
    assert {r["name"] for r in rows if r.get("low")} >= {"henrico", "chesterfield"} and not any(r.get("low") and r.get("box") for r in rows)


def prep_areas():
    from inundation import site
    return site.areas()


def test_a_river_without_its_bed_is_tidal_water_and_a_pit_is_not(tmp_path):
    """Richmond and Petersburg: the elevation data has the tidal river as the water's surface (0 to 2 ft), and the
    only ground below the lowest level is a quarry pit. The pit is not the sea; the river is lowered and becomes it."""
    from inundation import prep, spread
    dem = np.full((1200, 300), 25.0, np.float32)
    dem[:, 100:160] = (0.8 + 0.0005 * np.arange(1200, dtype=np.float32))[::-1, None]     # the river as the lidar met
    # it: 1.4 ft where it enters at the top, 0.8 ft where it leaves at the bottom
    dem[100:1100, 200:290] = -60.0        # a pit larger than the river (9 ha), with a high rim all round
    dem[600:620, 20:40] = 2.0             # a pond: even water, but nothing joins it to the river
    assert not depth.leaving_water(dem)[200, 240] and depth.leaving_water(dem)[200, 130]
    assert depth.flat_water(dem)[5, 130] and depth.flat_water(dem)[610, 30] and depth.flat_water(dem)[5, 100] and not depth.flat_water(dem)[5, 99]
    tidal, pits, _, step = prep.tidal_cells(dem)
    assert tidal == [] and len(pits) == 1 and step == 1
    path = str(tmp_path / "dem_ft.npy")
    np.save(path, dem)
    said = []
    level = prep.tidal_surface(np.load(path, mmap_mode="r"), path, lambda *a: said.append(" ".join(map(str, a))))
    assert level == 1.25 and "the river's surface" in said[0]          # the lowest level with 5 ha leaving the frame
    low = np.load(path)
    assert (low[:, 100:160] == depth.LEVELS_FT[0]).all()                # and the reach above it, 1.25 to 1.4 ft, too
    assert (low[100:1100, 200:290] == -60).all() and low[0, 0] == 25 and low[610, 30] == 2
    assert prep.tidal_surface(np.load(path, mmap_mode="r"), path) is None           # a second run finds the river
    keep = prep.sea_cells(low, (0, 1200), [], {}, None, lambda *a: said.append(" ".join(map(str, a))))
    assert len(keep) == 1 and 100 <= keep[0][1] < 160 and any("is a pit" in s for s in said)
    out = spread.connect_index(low, depth.LEVELS_FT, seed=keep)
    assert out[200, 130] == 0 and out[200, 240] == depth.NEVER
    # a large grid is looked over on every 8th cell and lowered cell by cell
    big = np.full((7200, 7000), 25.0, np.float32)
    big[:, 3000:3101] = 0.8
    big[:2000, 3000:3101] = 1.6           # a reach a step higher
    big[5000, 3050] = 9.0                 # a pile in the river keeps its height
    np.save(path, big)
    assert prep.tidal_surface(np.load(path, mmap_mode="r"), path) == 1.0
    low = np.load(path, mmap_mode="r")
    assert (low[2001::97, 3000:3101] == depth.LEVELS_FT[0]).all() and low[5000, 3050] == 9 and low[5000, 2990] == 25
    assert (low[:1999:97, 3000:3101] == depth.LEVELS_FT[0]).all()
    whole = depth.flat_water(big[1000:3100, 2990:3110])
    assert np.array_equal(depth.flat_water(big[1998:2030, 2990:3110])[2:-2], whole[1000:1028])     # strips agree with the whole
