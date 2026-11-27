# StormSense inundation

Which roads and buildings the gauges and the 72-hour forecast put under water, and what that costs. Pilot: Norfolk
(FIPS 51710), 2026-11-25. Derek's rules: roads under more than 0.5 ft of water are orange, ponded water (0.25 ft) is
yellow, buildings in more than 0.5 ft are red and labelled "Flooded Building". Since 2026-11-26 the water also
travels through the stormwater pipes, floodwalls hold it back, and buildings carry year built, assessed value and an
estimated damage.

## How it works

1. **Once per area** (`python -m inundation.prep`): VGIN's road centerlines are cut into pieces of at most 5 m
   and VGIN's building footprints are read. Each piece and each building gets
   - `ground_ft`: the ground under it, ft NAVD88 (roads: the middle of five points along the piece; buildings:
     the middle value over the footprint, with `low_ft` the low side),
   - `connect_ft`: the lowest of the 49 water levels (-2 to 10 ft NAVD88 in 0.25 ft steps, the levels of Derek's
     HUC-12 depth grids) at which water reaches it, over the surface or through the storm drains
     (`connect_surface_ft`: over the surface only; `via_pipes`: 1 where the drains bring it sooner),
   - `ponded_ft`, `flooded_ft`: the water level at which it holds 0.25 ft and 0.5 ft: `max(ground + depth, connect)`,
   - `huc12` and `station`: its HUC-12 and the gauge it is tethered to (the nearest StormSense gauge;
     `tether_overrides.csv` sends a whole HUC-12 to a named gauge).
2. **Every forecast run** (`python -m inundation.status`): each tethered gauge's published fore72 (NAVD88) and
   last observation are compared with those levels: state now, at the forecast's highest water, at the 90% level,
   first and last time flooded, hours flooded, and an approximate chance of flooding within 72 hours.
   A gauge the run published no forecast for (it is out, like Back River's after the 2026 Thanksgiving storm)
   follows the nearest gauge that has one (`status.read_levels`), so its roads and buildings are not counted dry;
   the summary lists it under `stations_following` and the pages name it.

Nothing heavy happens per run: the rasters are only read in step 1.

## Depth source

`depth.py` reduces any source to `ground_ft` and `connect_ft` per point.

- Pilot: the 1 m CoNED Chesapeake Bay topobathy DEM (the DEM behind VIMS's
  `USGS/Chesapeake_Bay_Topobathy_DEM` service), read from NOAA's public bucket
  `noaa-nos-coastal-lidar-pds/dem/Chesapeake_Coned_update_DEM_2016_8656/` (EPSG:26918, m NAVD88). At each
  level, water covers the cells at or below it that are joined to tidal water, so a low spot behind higher ground
  stays dry until that ground is overtopped, or until a storm drain reaches it (next section).
  Tidal water is the largest body of water at -2 ft in the locality's frame, and every other body of 5 hectares or
  more that runs off the frame's edge (`depth.tidal_seeds`): an estuary that meets the first one outside the frame,
  like the Piankatank north of Gloucester (frame: the York and Mobjack Bay) or the York north of James City (frame:
  the James). Until 2026-11-27 only the largest body counted, and the second river's shore was land. A body that
  leaves the frame inside a watershed no gauge stands for (below) is not tidal water (`prep.sea_cells`).
- Memory: one byte per cell for the result plus the union-find's pages over low ground (`spread.sparse`; the frame
  of King and Queen County is 2.96 billion cells and needed all 16 GB before the untouched pages were kept out).
- Derek's 0.25 ft HUC-12 depth grids, once hosted (planned: mobjack.vims.edu `VIMS_PHYS`): sample each grid at the
  points and pass the depths to `depth.from_depth_stack`; everything after that is unchanged.

The Norfolk SLR services on Mobjack (`VIMS_CCRM/Norfolk_Current`, `_Int1`, `_High`) are two-color extent
pictures (RGB), with no depth values, so they cannot feed this.

## Storm drains and floodwalls

`drainage.py` reads a city's stormwater pipes, ditches and structures (`drainage_sources.json`, per locality FIPS)
and joins them into networks. At a water level, if any opening of a network (inlet, outfall, ditch, free pipe end)
is in the water, the water also comes up at the network's other openings that lie at or below the level. It is a
static rule like Norfolk's TITAN: no head loss, no pipe capacity, no rain. Manholes, junction boxes and the like
are not openings, nor are structures with a flap valve or tide gate. Cities differ in what they publish, and the
source file says how to read each: a layer can be `"closed"` as a whole (manholes), `"snap_m"` widens the distance
at which pipe ends and structures are joined (Newport News draws its pipes to the edge of the box, 0.85 m short of
the structure point), and `"pipe_ends_open"` takes every pipe end as an inlet or outlet where a city publishes
the lines only (Chesapeake, Hampton). The log prints `pipe_ends_on_structures`, the share of pipe ends that met a
structure: check it for every new city before trusting the networks.

`barriers.py` reads floodwalls and levees from the USACE National Levee Database (the bare-earth DEM has a gap where
a wall stands). The system's line is burned into the terrain as cells water cannot cross, and pipes are cut where
they enter the leveed area. The system is taken as closed and not overtopped up to 10 ft NAVD88; water can still
come round the end of a wall over low ground (downtown Norfolk: from about 7.5 ft in this DEM).

`check_titan.py` compares the result with TITAN's own depth maps. Downtown Norfolk and the Hague, wet in both over
wet in either: 0.35 over the surface only and 0.72 with the pipes and the floodwall at 5.5 ft NAVD88 (0.22 and 0.61
at 4.0 ft, 0.68 and 0.77 at 6.5 ft). Two things the comparison found and fixed: without the floodwall downtown
flooded at 5.5 ft, and a first rule that shut every network near a stormwater pump station kept the St. Paul's area
dry, which TITAN floods through the pipes. What is left is mostly terrain (TITAN uses the city's own DEM).

## Values and damage

`value.py` gives each building the parcel its middle falls in (`value_sources.json`: the locality's parcel polygons
and assessment roll) and from it the use, year built, assessed total and improvement value; a parcel's improvement
value is shared between its buildings by footprint area (`value_bldg`). VGIN's footprints and parcels carry no
values themselves. Norfolk: the Real Estate Assessor's "Property Assessment and Sales - FY27" (values effective
1 July 2026); 91,645 of 93,969 buildings matched, 81,068 of them homes. The second opinion is Zillow's Home Value
Index for the ZIP code (typical home value, newest month): Zillow does not publish per-address estimates for bulk
use, so this is the ZIP's typical home, not the house.

`damage.py` holds the depth-damage curves: the U.S. Army Corps of Engineers generic depth-damage relationships for
residential structures without basements (Economic Guidance Memorandum 01-03, 4 Dec 2000, repeated in EGM 04-01,
10 Oct 2003): one story, two or more stories, split level, percent of structure value by depth from the first
floor. These are the curves behind HEC-FDA studies and FEMA's Hazus defaults. They are for homes, so only
single-family homes and duplexes to fourplexes get an estimate. Virginia has no first floor elevations, so the
first floor is assumed at Hazus's default height for a crawlspace: 3 ft above ground for homes built up to the
community's first flood insurance rate map (Norfolk: 1979, to be confirmed), 4 ft after. That assumption carries
the answer: at the 26 Nov forecast peak, 811 homes and $2.4 million with crawlspaces against $43 million if every
floor sat 1 ft up (slab; `damage_peak_if_slab_usd`). Contents are not estimated.

`ffe.py` replaces the assumed height with a surveyed one where a building has a FEMA elevation certificate that
HRPDC has keyed in ("Hampton Roads Elevation Certificates (NAVD 88)" on geo.hrsd.com: 5,268 building footprints,
finished first floor in ft NAVD88). A certificate goes to the VGIN building under the middle of its footprint;
`first_floor_ft` is then the certificate's floor minus the building's ground and `floor_source` says
"certificate". Norfolk has 786 such buildings (648 of them homes), and with them the 26 Nov estimate is 792 homes
and $2.5 million ($37 million with slab floors for the rest). The certificates also check the assumption: over
the region's homes the median floor height above grade is 3.4 ft before the first flood map and 3.9 ft after
(1,011 and 2,396 certificates). Derek's Newport News survey can be added the same way.

Outside Norfolk the values come from HRPDC's "Hampton Roads Regional Parcels" (`_hrpdc` in `value_sources.json`;
an entry with `"like"` starts from a template): assessed improvement and total value as the localities supplied
them in early 2026, year built for some, the site ZIP code, and no use codes. A home is then a building of at most
500 m2 on a parcel whose zoning matches the locality's `residential_patterns` (Chesapeake has no zoning there, so
`use_from` reads the property class from the city's own parcel layer). These rules are read from the code counts,
not from the localities' code tables, and the first flood map year is only set for Norfolk (elsewhere every assumed
floor is 3 ft).

## Building footprints

VGIN's statewide footprints, by locality FIPS. VGIN publishes none for King and Queen County (November 2026): there
the outlines of FEMA's USA Structures (Oak Ridge National Laboratory, traced from imagery) stand in
(`vgin.buildings`), and the page's sources say so.

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
| `depth.py` | DEM tiles, water connectivity by level (surface, pipes, walls), depth-stack adapter |
| `spread.py` | the same connectivity in one pass that reads the grid in row order (numba): 5 to 6 bytes per cell in memory, DEM and results on disk; Norfolk in 2 minutes, identical cell for cell |
| `drainage.py`, `drainage_sources.json` | stormwater pipes, ditches and structures as networks of openings |
| `barriers.py` | floodwalls and leveed areas from the National Levee Database |
| `value.py`, `value_sources.json` | parcels and assessment roll onto the buildings; Zillow ZIP values |
| `damage.py` | USACE depth-damage curves, Hazus first floor heights |
| `ffe.py` | surveyed first floor elevations from HRPDC's elevation certificates |
| `check_titan.py` | comparison with Norfolk's TITAN depth maps |
| `tether.py` | HUC-12s, nearest gauge, overrides |
| `prep.py` | step 1; writes `<name>_roads.csv.gz`, `<name>_buildings.csv.gz`, `<name>.gpkg` (`--gdb`: `<name>.gdb`), `<name>_tether.csv`, `page/` |
| `status.py` | step 2; writes `<name>_roads_status.csv`, `<name>_buildings_status.csv` (+ `.geojson` with `--layers`), `<name>_summary.json`, `<name>_timeline.csv`, `page/levels.json` |
| `page.py`, `templates/map.html` | the area's self-contained map page (no CDN, no map tiles): forecast, Historical Floods and Scenario views, the prediction plot, counts, gauge table; `python -m inundation.page --prepared <page dir> --work <work dir>` redraws an area's flood picture without a new `prep`; with `--shapes` it only writes the page's coordinates again (they are whole units in 16 bits, half a metre each, doubled for a frame longer than 32.7 km) |
| `history.py`, `events/` | past floods: 72 hours of every gauge's record around the highest water at Sewells Point |

Extra packages (not needed by the StormSense run): `pip install -r inundation/requirements.txt`.

## Running it

```
python -m inundation.prep --fips 51710 --name norfolk --out prepared --work work --gdb
python -m inundation.status --prepared prepared --name norfolk --site <StormSense Latest site dir> \
    --out status --page prepared/page --layers prepared/norfolk.gdb
```

`prep` downloads about 6 GB of DEM tiles for Norfolk (or pass local copies with `--dem`) and takes about
12 minutes on 4 cores with 12 GB of memory (the pipes double the connectivity step). `--no-drainage`,
`--no-barriers`, `--no-values` and `--no-certificates` leave those parts out. `status` takes seconds.

## The map page

- Flood picture: 5 m cells from the 1 m grids, each with the lowest level that reaches it, its gauge and its ground
  (0.05 ft steps), so the page colors depth in Derek's eleven blues (up to 0.5 ft, then each foot to 10 ft). It is
  written as `page/tiles/z0.png` (the whole area, at most 2048 pixels) plus 1024-pixel tiles the page loads as the
  reader zooms in. One big picture (`extent.png`, before 2026-11-27) did not draw on phones. Cells in a watershed no
  gauge measures are left empty.
- Views: Now, Forecast peak (the page opens here), Last observed, Historical Floods, Scenario. Scenario sets one
  water level for every gauge, -2 to 10 ft NAVD88 in 0.25 ft steps, and the roads, buildings and damage follow it; it
  opens at the step nearest 7 ft above MLLW at Sewells Point.
- "StormSense Prediction Plot / Anticipated Impacts in the Next 72 Hours": the forecast at the NOAA tide gauge nearest
  the area's centre, one inside the area first (`static.json` `plot_gauge`: Sewells Point for Norfolk, Portsmouth and
  Hampton, Yorktown for Newport News, Money Point for Chesapeake, the Bay Bridge Tunnel for Virginia Beach), with
  its 10-90% band and NWS stages on the left axis, road miles and buildings on two right axes (Derek's layout,
  2026-11-27).

## The site and the map of all localities

`areas.csv` lists the localities (FIPS, the short name used in file names and URLs, the title, the region):
Hampton Roads and the Middle Peninsula so far. Add a row to add a locality.

    python -m inundation.site --prepared <folder of <name>/ folders> --latest <Latest site> --out site/inundation

writes one site: `<name>/` is each prepared locality's own map page (its tables under `<name>/data/`) and
`index.html` is the map of all of them (`templates/statewide.html`). That map opens on Sewells Point over a
satellite basemap with roads and place names, or a street map (Esri's World Imagery, its two reference layers and
World Street Map, drawn with Leaflet from `crest/jslib`). The water is drawn from the localities' own depth tiles:
they are in each locality's UTM zone, so the page reprojects them as it draws (a far view from `tiles/z1.png`,
`tiles/z0.png` closer, the 1024-pixel tiles from zoom 14), cut to the locality's outline (`<name>_outline.geojson`,
written by `prep`) so neighbours do not paint over each other. Roads, buildings and damage are counted from
`areas.json`'s `curves`: for each locality and gauge, the totals at each of the 49 levels, read off at the gauge's
water level, so the far view never loads the road and building tables. From zoom 15 in, the page reads the
`static.json` of each locality in view (its own page's data) and draws every flooded or ponded road piece and
flooded building on the water; a click on one gives its depth, ground and gauge, a click elsewhere in a locality its
counts and the link to its own map. The plot is Sewells Point's forecast.

`.github/workflows/inundation.yml` runs it after every StormSense run and publishes to GitHub Pages at
https://derekloftis.github.io/StormSense-archive/inundation/ (and to release `inundation-pages`). GitHub Pages
holds one site per repository, so this workflow and `crest.yml` each unpack the other's newest site zip beside their
own before deploying. A locality is prepared on Actions too, once: run the workflow by hand with `prepare` set to
names from `areas.csv`, `missing` or `all` (one job per locality, four at a time); the result is kept on release
`inundation-prepared` as `<name>_prepared.tar` and `<name>_inundation_gdb.zip`. Prepare a locality again after its
sources or the method change. A pull request prepares Poquoson and keeps its site as an artifact.

## Historical floods

`events/events.json` lists the storms; each has `events/<id>/event.json` (peak, window, every gauge with its highest
water, datum note) and `observations.csv.gz` (`gauge, date_time_z, water_level_ft_navd88`). The window is the 36 hours
before and after the highest water at Sewells Point (NOAA 8638610, on record since 1927) within the storm's dates.
The pages' Historical Floods menu lists them newest first (`history.events`).

| Storm | Highest water at Sewells Point | Gauges with a record |
|---|---|---|
| 2026 Thanksgiving Day Nor'easter | 5.99 ft NAVD88 (7.60 ft MLLW), 2026-11-26 15:18Z | 68 (so far, through 2026-11-27T12:12Z, with 15 forecast runs) |
| 2016 Hurricane Matthew | 4.25 ft NAVD88 (5.86 ft MLLW), 2016-10-09 06:54Z | 28 (16 USGS, 2 on an estimated NAVD88) |
| 2012 Hurricane Sandy | 5.20 ft NAVD88 (6.81 ft MLLW), 2012-10-29 13:18Z | 14 (3 USGS, 2 on an estimated NAVD88) |
| 2011 Hurricane Irene | 5.94 ft NAVD88 (7.55 ft MLLW), 2011-08-27 23:54Z | 17 (3 USGS, 5 on an estimated NAVD88) |
| 2009 Nor'Ida | 6.13 ft NAVD88 (7.74 ft MLLW), 2009-11-12 23:24Z | 17 (3 USGS, 4 on an estimated NAVD88) |
| 2003 Hurricane Isabel | 6.28 ft NAVD88 (7.89 ft MLLW), 2003-09-18 21:00Z | 13 (4 on an estimated NAVD88) |
| 1933 Hurricane | 6.41 ft NAVD88 (8.02 ft MLLW), 1933-08-23 14:00Z | 2 |

- `python -m inundation.history noaa --all` (or `--event <id>`): NOAA CO-OPS verified water levels for every Virginia
  station, active or historic, plus StormSense's own NOAA gauges (6-minute, else hourly heights), and USGS tide gauges
  (parameter 62620) for storms since 2007. A station NOAA has no NAVD88 for is read in MLLW and moved with StormSense's
  offset (Money Point) or its own mean sea level plus the nearest station's NAVD88-to-mean-sea-level difference
  (`navd_estimated` in `event.json`). Needs api.tidesandcurrents.noaa.gov and nwis.waterservices.usgs.gov (where USGS
  keeps readings older than 120 days).
- `python -m inundation.history stormsense --event 2026-thanksgiving-noreaster --runs ensemble_*.tar.gz`: StormSense's
  own gauges from the archived runs' `observed_ft`, plus every run's published forecast (`forecasts.csv.gz`: `cycle,
  station, date_time_z, forecast_ft_navd88, p10_ft, p90_ft`). `complete` is false until the 36 hours after the peak are
  archived: run it again then. An ens72 table made before 27 Nov 2026 draws its `observed_ft` straight across a gauge's outage; twelve or more
  readings in one line are taken as such a bridge and left out (`bridged_readings_left_out`: the Bay Bridge Tunnel
  gauge was down from 3:36 AM to 2 PM EST on 26 Nov, through the peak).
- On a page (`status --page` writes `events.json`), each of the area's gauges uses its own record where it has one
  through the peak (more than 70% of the readings within 1.5 hours of it, and 60% of the window anyone recorded);
  otherwise the nearest gauge that does. In 1933 that is Sewells Point for every place. The ground, roads and
  buildings are today's.
- `python -m inundation.history marks --all` (or `--event <id>`): the U.S. Geological Survey's surveyed high water
  marks and storm-tide sensor peaks in Virginia for the storms with a `"marks"` entry in `events.json`, written to
  `events/<id>/marks.csv` (`id`, `kind` hwm or sensor, `lat, lon, elev_ft_navd88, elev_ft, datum, quality, within_ft, type,
  environment, wave, county, waterbody, place, time_z, source`). Two sources, merged: the Flood Event Viewer's
  Short-Term Network service (`fev_event`; since 2026 at api.waterdata.usgs.gov/stnservices, where stn.wim.usgs.gov
  forwards to) and the tables of the Survey's open-file reports (`reports`: the report's own mark ids, its
  wave-affected column, and the peaks of its temporary storm-tide sensors). A report's mark and the viewer's mark
  within 60 m at the same height to 0.1 ft are one mark. Isabel's marks were surveyed in NGVD29 and are moved to
  NAVD88 at each mark's own place with NOAA NGS's VERTCON (geodesy.noaa.gov/api/ncat; -0.77 to -1.23 ft across
  tidewater Virginia, -0.82 ft at Sewells Point). Needs openpyxl for the report tables.

  | Storm | High water marks with an elevation | Sensor peaks | Sources |
  |---|---|---|---|
  | 2012 Hurricane Sandy | 26 (16 noted as wave-affected) | 10 | Open-File Report 2013-1043 tables 3 and 6; Flood Event Viewer event 24 |
  | 2011 Hurricane Irene | 19 (2 on rivers) | 11 | Open-File Report 2012-1022 tables 2 and 5; Flood Event Viewer event 23 |
  | 2003 Hurricane Isabel | 99 (59 more have no surveyed elevation; 19 noted as wave run-up) | none | Flood Event Viewer event 133 |

  The viewer has no Virginia marks for Nor'Ida (9 water-level sensors were out, no peaks are published) or Matthew
  (Open-File Report 2017-1122 covers Florida to North Carolina), and nothing for 1933.
- On a page the marks inside the frame are drawn in the Historical Floods view: diamonds for high water marks
  (hollow where wave-affected), triangles for sensor peaks. A tap gives the mark, its quality and place, and the map's
  highest water there (the highest reading of the gauge that the nearest road piece within 1.5 km follows), and the
  flood's note gives the median difference of the marks not noted as wave-affected. A river's marks and marks more
  than 6 ft above every gauge's highest water are left off (`marks_left_off`). First check, Norfolk in Isabel: 21
  marks stand a median 0.03 ft below the map's highest water (Sewells Point, 6.28 ft).

## Numbers (run of 2026-11-26 00Z, a forecast major flood)

At each gauge's forecast peak (most between 4.9 and 6.1 ft NAVD88, about 14:30Z on 26 Nov):

| City | Roads flooded (mi) | of which only by pipes | Ponded (mi) | Buildings flooded | Homes with damage | Est. damage | At the 90% level (mi / buildings) |
|---|---|---|---|---|---|---|---|
| Norfolk | 39.7 | 6.7 | 6.7 | 2,053 | 792 | $2.50M | 47.4 / 2,576 |
| Newport News | 3.0 | 0.0 | 0.6 | 168 | 97 | $0.23M | 3.7 / 206 |
| Virginia Beach (north of the Back Bay watershed) | 5.0 | 0.8 | 2.1 | 284 | 120 | $0.15M | 7.5 / 433 |
| Portsmouth (no pipes published) | 6.0 | – | 1.2 | 424 | 211 | $0.79M | 7.5 / 525 |
| Hampton (no assessed values found) | 26.3 | 1.3 | 6.0 | 2,090 | – | – | 33.3 / 2,649 |
| Chesapeake (north of the Great Bridge lock; pipe lines only) | 23.6 | 2.9 | 8.1 | 1,332 | 516 | $0.99M | 32.8 / 1,794 |
| Suffolk (north of the Blackwater watershed; no pipes or values found) | 0.6 | – | 0.1 | 60 | – | – | 0.7 / 66 |
| Isle of Wight County (no pipes found) | 1.4 | – | 0.3 | 18 | 5 | $0.09M | 1.6 / 25 |
| Poquoson (no pipes or values found) | 23.0 | – | 2.9 | 1,752 | – | – | 26.2 / 2,122 |
| York County (no pipes found) | 10.5 | – | 2.4 | 1,013 | 457 | $0.99M | 12.9 / 1,274 |
| James City County | 2.8 | 0.0 | 0.5 | 205 | 103 | $0.33M | 3.4 / 253 |
| Williamsburg | 0.0 | – | 0.0 | 0 | 0 | $0 | 0.0 / 0 |
| Gloucester County (no pipes found) | 44.5 | – | 4.4 | 719 | 325 | $1.68M | 49.0 / 858 |
| Mathews County (no pipes or values found) | 37.7 | – | 6.0 | 957 | – | – | 44.2 / 1,152 |
| Middlesex County (no pipes or values found) | 1.9 | – | 0.4 | 78 | – | – | 2.4 / 110 |
| King and Queen County (no pipes or values found; FEMA USA Structures outlines) | 2.6 | – | 0.5 | 24 | – | – | 3.2 / 34 |
| King William County (no pipes or values found) | 2.2 | – | 0.3 | 112 | – | – | 2.6 / 126 |
| Essex County (no pipes or values found) | 3.1 | – | 0.6 | 55 | – | – | 3.8 / 89 |

- Norfolk: 360,651 pieces (1,765 km), 93,969 buildings, 9 gauges; 32,598 pipes, 4,956 ditches and 38,352 structures
  in 2,441 networks; one floodwall system. Before the pipes and the floodwall: 34.8 miles and 1,958 buildings.
- The damage figure is ruled by the first floor: with every home on a slab at grade the same water gives $37.0M in
  Norfolk, $16.8M in Chesapeake, $6.5M in Portsmouth, $4.1M in Virginia Beach and $3.8M in Newport News. Elevation
  certificates replace the assumed floor for 786 Norfolk buildings, 608 in Chesapeake, 235 in Virginia Beach, 66 in
  Portsmouth and 5 in Newport News.
- The rural counties' totals are small beside Gloucester's and Mathews's because their shores are bluffs and marsh:
  the low, built-up ground is on Mobjack Bay (Guinea, Gwynn's Island, the Mathews necks) and in Poquoson.
- Newport News: the pipes add almost nothing at this level because nearly all the low ground is already joined to
  tidal water over the surface. Hampton: half of the total is the Back River gauge's (BRDH) area.
- Gauge levels: the flags are only as good as each gauge's NAVD88 reference, and a 0.25 ft step shows it. Over the
  week before this run most of the 29 gauges used here average 0.36 to 0.58 ft NAVD88; Rudee Inlet (RDIV, -0.34)
  sits 0.7 ft under Lake Rudee (USLR, 0.36) right behind it, and LCEV (-0.01), HPPV (0.11), CBBT (0.13), SLRV (0.14),
  DPCV (0.70) and WRSV (1.03) are off the pack too. Some of that is real (river slope, ocean against bay), some is
  likely datum: a question for the forecast, since these numbers follow it. Chesapeake's total is nearly all the
  Southern Branch: Money Point's area (MNPT, peak 5.49 ft) 12.1 miles and Deep Creek's (DPCV, 6.29 ft) 10.2.
- Not yet checked against observed flooding (Norfolk publishes flooded-street reports:
  `Storm/Storm_Data/MapServer/26` on orfmaps.norfolk.gov).

## Watersheds without a gauge

A HUC-12 whose water level no StormSense gauge measures is left out rather than tied to the nearest gauge:
`tether_overrides.csv` takes `-` as the station, and a code ending in `*` covers every HUC-12 that starts with it.
The one rule so far is `03010205*`: Back Bay, the North Landing River and the Northwest River (the south of Virginia
Beach and Chesapeake) drain to Currituck Sound, are driven by wind rather than tide, and are shut off from the
Elizabeth River by the Great Bridge lock. Tied to Lake Rudee they showed 66 road miles flooded on a day when nothing
there was. That leaves out 239,164 road pieces and 78,804 buildings in Virginia Beach and 173,410 pieces and 41,189 buildings in
Chesapeake (Great Bridge south of the lock, Hickory, the Dismal Swamp). Their pieces and buildings stay in the tables with an empty `station`; `status` and the page skip them and
the page says which watersheds are missing. A gauge there (or a Currituck Sound forecast) brings them back.

## Open points

- Building rule: half the footprint under 0.5 ft (now) or 0.5 ft at the low side (`wet_low_ft`), Derek's call.
- Low spots with no pipe to tidal water stay dry; rain is not in it.
- Other buildings than homes get no damage estimate; contents are not estimated; first floor heights are assumed.
- The water picture on the page is drawn at 5 m (roads and buildings use the 1 m DEM); the tiles would carry 1 m.
- Historical floods use the tide gauges only (no high water marks, no rain), on today's ground. Chesapeake Bay
  Bridge Tunnel's old station (8638863) and other historic stations are on an estimated NAVD88.
- No storm drain layers were found for Suffolk, Poquoson, Isle of Wight, York, Gloucester, Mathews, Middlesex, King
  and Queen, King William or Essex, so their water travels over the surface only. HRPDC's regional parcels carry
  values for Williamsburg, Gloucester, Isle of Wight, James City, York and Franklin, but not for Hampton, Poquoson or
  Suffolk, and nothing was found for the Middle Peninsula counties north of Gloucester: no damage estimate there.
  Homes are picked by zoning patterns outside
  Norfolk and Chesapeake: an assumption per city until the cities' use codes are in. The year of each city's first
  FIRM is only set for Norfolk (1979, unverified), so the 4 ft post-FIRM floor is not applied elsewhere.
- Derek's Newport News first floor survey (several thousand houses) goes in the way `ffe.py` adds the certificates.
- `chance` uses the 10-90% band at the forecast's peak; the run's own error trajectories would be better.
- "Now" is the last observation at the forecast run; a lighter, more frequent job could refresh it.
- Statewide, 5 m pieces are tens of millions of features: decide the packaging (per locality or HUC-12) first.
- Publishing: the hosted layers in the AGOL folder "StormSense CREST" are not wired yet (Pages is: see "The site").
