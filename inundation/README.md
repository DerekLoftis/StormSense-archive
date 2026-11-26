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

Nothing heavy happens per run: the rasters are only read in step 1.

## Depth source

`depth.py` reduces any source to `ground_ft` and `connect_ft` per point.

- Pilot: the 1 m CoNED Chesapeake Bay topobathy DEM (the DEM behind VIMS's
  `USGS/Chesapeake_Bay_Topobathy_DEM` service), read from NOAA's public bucket
  `noaa-nos-coastal-lidar-pds/dem/Chesapeake_Coned_update_DEM_2016_8656/` (EPSG:26918, m NAVD88). At each
  level, water covers the cells at or below it that are joined to tidal water, so a low spot behind higher ground
  stays dry until that ground is overtopped, or until a storm drain reaches it (next section).
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
| `page.py`, `templates/map.html` | the area's self-contained map page (no CDN, no map tiles): time slider through the 72 hours, counts, gauge table |

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

## Numbers (run of 2026-11-26 00Z, a forecast major flood)

At each gauge's forecast peak (4.9 to 6.1 ft NAVD88, about 14:30Z on 26 Nov):

| City | Roads flooded (mi) | of which only by pipes | Ponded (mi) | Buildings flooded | Homes with damage | Est. damage | At the 90% level (mi / buildings) |
|---|---|---|---|---|---|---|---|
| Norfolk | 39.7 | 6.7 | 6.7 | 2,053 | 792 | $2.50M | 47.4 / 2,576 |
| Newport News | 3.0 | 0.0 | 0.6 | 168 | 97 | $0.23M | 3.7 / 206 |
| Virginia Beach (north of the Back Bay watershed) | 5.0 | 0.8 | 2.1 | 284 | 120 | $0.15M | 7.5 / 433 |
| Portsmouth (no pipes published) | 6.0 | – | 1.2 | 424 | 211 | $0.79M | 7.5 / 525 |
| Hampton (no assessed values found) | 26.3 | 1.3 | 6.0 | 2,090 | – | – | 33.3 / 2,649 |

- Norfolk: 360,651 pieces (1,765 km), 93,969 buildings, 9 gauges; 32,598 pipes, 4,956 ditches and 38,352 structures
  in 2,441 networks; one floodwall system. Before the pipes and the floodwall: 34.8 miles and 1,958 buildings.
- The damage figure is ruled by the first floor: with every home on a slab at grade the same water gives $37.0M in
  Norfolk, $3.8M in Newport News, $4.1M in Virginia Beach and $6.5M in Portsmouth. Elevation certificates replace the
  assumed floor for 786 Norfolk buildings, 235 in Virginia Beach, 66 in Portsmouth and 5 in Newport News.
- Newport News: the pipes add almost nothing at this level because nearly all the low ground is already joined to
  tidal water over the surface. Hampton: half of the total is the Back River gauge's (BRDH) area.
- Virginia Beach: the Lake Rudee gauge (USLR) peaks at 6.08 ft where Rudee Inlet (RDIV) peaks at 5.01 ft, and its
  observations sit about 1 ft above its neighbours': worth a look at its datum before its area's numbers are trusted.
- Not yet checked against observed flooding (Norfolk publishes flooded-street reports:
  `Storm/Storm_Data/MapServer/26` on orfmaps.norfolk.gov).

## Watersheds without a gauge

A HUC-12 whose water level no StormSense gauge measures is left out rather than tied to the nearest gauge:
`tether_overrides.csv` takes `-` as the station, and a code ending in `*` covers every HUC-12 that starts with it.
The one rule so far is `03010205*`: Back Bay, the North Landing River and the Northwest River (the south of Virginia
Beach and Chesapeake) drain to Currituck Sound, are driven by wind rather than tide, and are shut off from the
Elizabeth River by the Great Bridge lock. Tied to Lake Rudee they showed 66 road miles flooded on a day when nothing
there was. Their pieces and buildings stay in the tables with an empty `station`; `status` and the page skip them and
the page says which watersheds are missing. A gauge there (or a Currituck Sound forecast) brings them back.

## Open points

- Building rule: half the footprint under 0.5 ft (now) or 0.5 ft at the low side (`wet_low_ft`), Derek's call.
- Low spots with no pipe to tidal water stay dry; rain is not in it.
- Other buildings than homes get no damage estimate; contents are not estimated; first floor heights are assumed.
- The water picture on the page is drawn at 5 m (roads and buildings use the 1 m DEM); 1 m needs tiles.
- Hampton Roads: Suffolk, Poquoson, York, James City, Gloucester and Mathews are not configured yet (no pipes found
  for Suffolk or Poquoson; HRPDC's regional parcels carry values for Williamsburg, Gloucester, Isle of Wight, James
  City, York and Franklin too, but not for Hampton, Poquoson or Suffolk). Homes are picked by zoning patterns outside
  Norfolk and Chesapeake: an assumption per city until the cities' use codes are in. The year of each city's first
  FIRM is only set for Norfolk (1979, unverified), so the 4 ft post-FIRM floor is not applied elsewhere.
- Derek's Newport News first floor survey (several thousand houses) goes in the way `ffe.py` adds the certificates.
- `chance` uses the 10-90% band at the forecast's peak; the run's own error trajectories would be better.
- "Now" is the last observation at the forecast run; a lighter, more frequent job could refresh it.
- Statewide, 5 m pieces are tens of millions of features: decide the packaging (per locality or HUC-12) first.
- Publishing: hosted layers in the AGOL folder "StormSense CREST" and the page on Pages are not wired yet.
