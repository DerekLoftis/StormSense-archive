# StormSense inundation

Which roads and buildings the gauges and the 72-hour forecast put under water. Pilot: Norfolk (FIPS 51710),
2026-11-25. Derek's rules: roads under more than 0.5 ft of water are orange, ponded water (0.25 ft) is yellow,
buildings in more than 0.5 ft are red.

## How it works

1. **Once per area** (`python -m inundation.prep`): VGIN's road centerlines are cut into pieces of at most 5 m
   and VGIN's building footprints are read. Each piece and each building gets
   - `ground_ft`: the ground under it, ft NAVD88 (roads: the middle of five points along the piece; buildings:
     the middle value over the footprint, with `low_ft` the low side),
   - `connect_ft`: the lowest of the 49 water levels (-2 to 10 ft NAVD88 in 0.25 ft steps, the levels of Derek's
     HUC-12 depth grids) at which water reaches it,
   - `ponded_ft`, `flooded_ft`: the water level at which it holds 0.25 ft and 0.5 ft: `max(ground + depth, connect)`,
   - `huc12` and `station`: its HUC-12 and the gauge it is tethered to (the nearest StormSense gauge;
     `tether_overrides.csv` sends a whole HUC-12 to a named gauge).
2. **Every forecast run** (`python -m inundation.status`): each tethered gauge's published fore72 (NAVD88) and
   last observation are compared with those levels: state now, at the forecast's highest water, at the 90% level,
   first and last time flooded, hours flooded, and an approximate chance of flooding within 72 hours.

Nothing heavy happens per run: the rasters are only read in step 1.

## Depth source

`depth.py` reduces any source to `ground_ft` and `connect_ft` per point.

- Pilot: the 1 m CoNED Chesapeake Bay topobathy DEM (the DEM behind VIMS's
  `USGS/Chesapeake_Bay_Topobathy_DEM` service), read from NOAA's public bucket
  `noaa-nos-coastal-lidar-pds/dem/Chesapeake_Coned_update_DEM_2016_8656/` (EPSG:26918, m NAVD88). At each
  level, water covers the cells at or below it that are joined to tidal water, so a low spot behind higher ground
  stays dry until that ground is overtopped. Storm drains and culverts are not in it.
- Derek's 0.25 ft HUC-12 depth grids, once hosted (planned: mobjack.vims.edu `VIMS_PHYS`): sample each grid at the
  points and pass the depths to `depth.from_depth_stack`; everything after that is unchanged.

The Norfolk SLR services on Mobjack (`VIMS_CCRM/Norfolk_Current`, `_Int1`, `_High`) are two-color extent
pictures (RGB), with no depth values, so they cannot feed this.

## Bridges, piers

The bare-earth DEM has no bridge decks. Road pieces are kept in the table but never flagged when `bridge` > 0:
1 the DEM shows water or a cut under the piece (ground below 1 ft NAVD88), 2 VGIN codes the segment as a
bridge (`SEG_TYPE` 2), 3 the same street within 60 m of a 1. Buildings standing in the water (`over_water`) are
never flagged either. VGIN's own bridge coding is incomplete (most bridges are `SEG_TYPE` 99), so a proper
bridge layer (VDOT structures) would be better.

## Files

| Module | What it does |
|---|---|
| `vgin.py` | roads, buildings and the locality boundary from VGIN's REST services |
| `segments.py` | cuts centerlines into 5 m pieces |
| `depth.py` | DEM tiles, water connectivity by level, depth-stack adapter |
| `tether.py` | HUC-12s, nearest gauge, overrides |
| `prep.py` | step 1; writes `<name>_roads.csv.gz`, `<name>_buildings.csv.gz`, `<name>.gpkg` (`--gdb`: `<name>.gdb`), `<name>_tether.csv`, `page/` |
| `status.py` | step 2; writes `<name>_roads_status.csv`, `<name>_buildings_status.csv` (+ `.geojson` with `--layers`), `<name>_summary.json`, `<name>_timeline.csv`, `page/levels.json` |
| `page.py`, `templates/map.html` | the area's self-contained map page (no CDN, no map tiles): time slider through the 72 hours, counts, gauge table |

Extra packages (not needed by the StormSense run): `pip install -r inundation/requirements.txt`.

## Running it

```
python -m inundation.prep --fips 51710 --name norfolk --out prepared --work work --gdb
python -m inundation.status --prepared prepared --name norfolk --site <StormSense Latest site dir> \
    --out status --page prepared/page --layers prepared/norfolk.gdb
```

`prep` downloads about 6 GB of DEM tiles for Norfolk (or pass local copies with `--dem`) and takes about
5 minutes on 4 cores with 8 GB of memory. `status` takes seconds.

## Numbers from the pilot (run of 2026-11-26 00Z, a forecast major flood)

Norfolk: 15,322 road segments, 360,651 pieces (1,765 km), 93,969 buildings, 9 gauges. At each gauge's forecast
peak (4.9 to 5.9 ft NAVD88, about 14:30Z on 26 Nov): 34.8 road miles flooded, 4.6 ponded, 1,958 buildings
flooded; at the 90% level 44.5 miles and 2,486 buildings. Streets flooded at 3.0 ft NAVD88 are the known ones
(Mayflower Rd, Richmond Cres, Boush St, Mowbray Arch, Llewellyn Ave). Not yet checked against observed flooding.

## Open points

- Building rule: half the footprint under 0.5 ft (now) or 0.5 ft at the low side (`wet_low_ft`), Derek's call.
- Low spots cut off from tidal water (7.6 road miles at this peak) are not shown; storm drains can flood them.
- `chance` uses the 10-90% band at the forecast's peak; the run's own error trajectories would be better.
- "Now" is the last observation at the forecast run; a lighter, more frequent job could refresh it.
- Statewide, 5 m pieces are tens of millions of features: decide the packaging (per locality or HUC-12) first.
- Publishing: hosted layers in the AGOL folder "StormSense CREST" and the page on Pages are not wired yet.
