# StormSense inundation

Which roads and buildings the gauges and the 72-hour forecast put under water, and what that costs. Pilot: Norfolk
(FIPS 51710), 2026-11-25. Derek's rules: roads under more than 0.5 ft of water are orange, ponded water (0.25 ft) is
yellow, buildings in more than 0.5 ft are red (a click names one "Flood Affected Structure"; Derek, 2026-11-27: no labels on the map, they covered the buildings). Since 2026-11-26 the water also
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
- A pit is not tidal water (2026-11-28). The quarry south of the James at Richmond goes down to -290 ft NAVD88 and
  was the largest body at -2 ft in the city's frame, so the first Richmond map flooded from the quarry and the
  river stayed dry. A body at -2 ft counts only if it lies in water that runs off the frame's edge at or below 3 ft
  (`depth.leaving_water`, `prep.tidal_cells`); the log names each pit left out.
- A river without its bed (2026-11-28). Above the surveyed channel the elevation data has the river as the water
  the lidar met: the James through Richmond lies at 0 to 2 ft and the Appomattox at Petersburg at -1.5 ft, with no
  ground at -2 ft at all, and Petersburg's first preparation stopped with nothing to flood from. Where a frame has
  no tidal ground, `prep.tidal_surface` takes the water of 5 hectares or more that runs off the frame's edge at the
  lowest level that has one (`depth.water_surface`, looked for up to 3 ft) and the even water in the same low
  ground (`depth.flat_water`: cells at or below 3 ft within 0.01 ft of all their neighbours, which only water as
  the lidar met it is; the reaches upstream lie a little higher than the one at the edge), writes them into the saved grid at
  -2 ft and the water spreads from there. A deck or a dock over that water then stands over water like any other
  (before, three docks at Rocketts Landing were flooded buildings at every high tide). `<name>_prep.json` has the
  level under `water_surface_ft`. A frame that has tidal ground is left as it is, also where a reach upstream in it
  is such a surface.
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
the lines only (Chesapeake, Hampton, Stafford). A layer's `"where"` splits pipes from open channels kept in one
layer (Arlington, Henrico, Hopewell), and `"end_type_fields"` reads the structures off the pipes themselves where no
structure layer names them (Prince William). The log prints `pipe_ends_on_structures`, the share of pipe ends that met a
structure: check it for every new city before trusting the networks.

`barriers.py` reads floodwalls and levees from the USACE National Levee Database (the bare-earth DEM has a gap where
a wall stands). The system's line is burned into the terrain as cells water cannot cross, and pipes are cut where
they enter the leveed area. The system is taken as closed and not overtopped up to 10 ft NAVD88; water can still
come round the end of a wall over low ground (downtown Norfolk: from about 7.5 ft in this DEM).

A layer its city has taken down: Newport News's stormwater manholes (`Operational/StormwaterManhole`) have answered
"Layer not found" since 27 Nov 2026. The pipes there stop short of the manholes, so without them the networks fall
apart and every loose end would count as an opening. `drainage_sources.json` names a copy for that layer (`"saved"`,
`saved_layers/51700_stormwater_manhole.geojson.gz`: the 4,810 manholes as read on 26 Nov 2026); `drainage.fetch`
uses it when the layer cannot be read, the log warns, and `<name>_prep.json` lists it under `drainage.saved_layers`.
The live layer is used again as soon as it answers. A layer without a copy stops the preparation.

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

## What the upriver and Northern Virginia localities publish

Derek asked (28 Nov 2026) for storm drains and parcel values in the 20 localities added that day. Each locality's
own ArcGIS Online organization and map server was searched (and Arlington's open data service); copies of a
locality's data on other accounts were not used. Storm drains were found in 10 and assessed values in 11:

| Locality | Storm drains | Parcel values |
|---|---|---|
| Alexandria | the city's StormDrainage layers: 15,980 gravity mains, 57 open channels, 17,114 structures (inlets, catch basins, pipe ends and culvert ends open; manholes, nodes and control devices closed); 1,153 networks, 11 cut at the Four Mile Run (Alexandria, Arlington) and Cameron Run levee systems. Old Town's combined sewers are not storm drains and are left out | the parcel layer has the land use but no values: not used |
| Arlington County | Stormwater_Mains and Stormwater_Junctions: 28,008 pipes, 1,434 streams and open channels (told apart by `PipeType`), 37,869 junctions (manholes, junctions without a structure, stormwater management and detention facilities closed: the last two are an assumption); 651 networks | REA property polygons joined to the county's Real Estate tables (datahub-v2.arlingtonva.us): assessments dated 1 Jan 2026, property class, ZIP, dwelling type and year built; 35,596 of 38,705 parcels have a building value, 33,940 are homes (classes 511 to 515). A condominium building's units are assessed under their own numbers, so those buildings carry no value |
| Fairfax County | DPWES stormwater network (fairfaxcounty.gov): in the mapped part 109,659 pipes and culverts, 13,191 open channels, 127,060 structures (manholes, junction boxes, clean outs and the like closed); 11,827 networks, 5 cut at a levee system | the county's open data Parcels joined to three Tax Administration tables (values for tax year 2026, dwelling style, year built and floor area, land use): in the mapped part 228,349 of 241,019 parcels have a building value, 185,386 are homes. The tables are read whole (369,000 rows each, 1,000 a request: about 15 minutes). No ZIP code, so no Zillow value |
| Prince William County | Public Works stormwater inventory: in the mapped part 34,711 pipe segments and 6,467 open segments (riprap, grass and concrete ditches, natural streams, by `SegmentType`); 5,383 networks. The point layer does not say what a structure is; the segments name the structure at each end (`end_type_fields`), and they are drawn from the inflow end to the outflow end (the two ends agree at 96% of 5,461 junctions checked). Manholes, tees, junction boxes and cleanouts are closed | the CAMA parcel layer has use codes and floor area but no values: not used |
| Stafford County | Stormwater_MOBILE: in the mapped part 9,926 pipes and 44 outfalls; 867 networks. The structure points do not say which are inlets, so every pipe end is an opening (`pipe_ends_open`, as for Hampton) | none found (a structures layer has the use only) |
| King George County | Storm layers: 1,348 gravity mains and culverts, 1,600 inlets, discharges and manholes (manholes and "Closed Lid Manhole" inlets closed); 262 networks | the parcel layer: improvement and total value, stories, year built, ZIP; 10,582 of 14,651 parcels have a building value. No use code: a home is a building of at most 500 m2 on a parcel with one or more stories on record |
| Spotsylvania County | none found | Parcel View: building and land assessment, year built, floor area, ZIP, zoning; in the mapped part 8,386 of 9,443 parcels have a building value; homes by zoning |
| Fredericksburg | none found | the 2024 reassessment layer: building and total value on 8,126 of 8,990 parcels, nothing else, so the values are shown but no building is counted as a home and no damage is estimated |
| Caroline County | none found | none found |
| Hanover County | none found | the parcel layer: land and improvement value, ZIP, zoning; in the mapped part 2,746 of 3,355 parcels have a building value; homes by zoning, no year built |
| Henrico County | Storm_Water layers (portal.henrico.gov): in the mapped part 26,903 pipes and culverts, 15,814 channels, 16,524 inlets, end treatments, manholes and junctions (the last two closed); 6,705 networks. A fifth of the pipe ends stop 0.5 to 1 m short of their structure, so `snap_m` is 1 (as for Newport News) | Tax Parcels and CAMA Data, tax year 2026: land and improvement value, use, house style, stories, year built, floor area, ZIP; in the mapped part 23,820 of 27,478 parcels have a building value, 22,617 are homes |
| Richmond (City) | none found | the city's Parcels (values dated 1 July 2026, land use, property class) with year built and living area from the Assessor's improvement points; 70,143 of 76,971 parcels have a building value, 56,437 are homes. No ZIP code, so no Zillow value |
| Chesterfield County | the county's Stormwater layers: in the mapped part 16,202 mains (roof drains left out), 6,952 ditches, 21,948 inlets, outlets, structures and valves (the last two closed); 3,223 networks | Cadastral parcels, assessment year 2024: improvement value, total, use code, stories, year built, floor area, ZIP; in the mapped part 30,028 of 35,700 parcels have a building value, 28,237 are homes (use codes SD, TH, TN, DU, read from the counts) |
| Colonial Heights | none found | the city's parcel layer: building and total value, land use code, building type, year built, ZIP; 6,939 of 7,619 parcels have a building value, 6,251 are homes (land use 111 and 312) |
| Petersburg | the city's 2025 stormwater field inventory: 7,657 pipes, 6,261 open channels, 10,433 structures (manholes and unverified structures closed, 28 pipe ends with a flap gate); 693 networks. The inventory was still being surveyed in November 2026 | the parcel layer has the use and year built but no values: not used |
| Hopewell | StormInfrastructure: 4,456 pipes, 31 ditches, 5,017 structures (manholes and unknown closed); 769 networks | none found (zoning only) |
| Prince George County | none found | none found |
| Charles City County | none found | Parcels with Sales, Valuation and Reports, 2022 reassessment: improvement and total value, use class, year built, ZIP; 3,488 of 6,103 parcels have a building value; a home is a building of at most 500 m2 on a single family or agricultural parcel |
| Surry County | none found | none found |
| New Kent County | none found | none found |

"In the mapped part": a county prepared with `--low` or `--box` reads its layers inside the frame only (plus 2 km
for the drains, so that pipes which leave the frame keep their networks whole), not the whole county. The share of
pipe ends that meet a structure is 0.95 to 1.00 everywhere but Henrico (0.92 after the 1 m join). None of these
localities has a first flood map year set, so every assumed floor is 3 ft above the ground, and the home rules are
read from the counts of each layer's codes, not from the localities' code tables. Parcel services also hold the
owners' names: only the columns named in `value_sources.json` are ever asked for.

Three things the sources needed. `vgin.layer_features` finds a layer's own object id field (King George's is
`FID`, Alexandria's `OBJECTID_1`), reads inside a box, and checks the read against the layer's count; where a
server counts more inside a box than it hands out (Henrico counts features just outside it), the rest are asked
for by id and those that do touch the box are added, so every id is accounted for. `"more"` in `value_sources.json`
joins tables to the parcel polygons by the parcel number (Fairfax keeps values, dwellings and land use in three
tables; Arlington's are served the OData way, `"type": "odata"`, 50,000 rows a request). And a roll with land and
improvement values but no total gets their sum (`land_value`).

## Building footprints

VGIN's statewide footprints, by locality FIPS, plus the outlines of FEMA's USA Structures (Oak Ridge National
Laboratory, traced from imagery, 450 square feet and larger) that come within 3 m of no VGIN footprint
(`vgin.buildings`; the building table's `outline` says VGIN or FEMA, `<name>_prep.json` counts them as
`buildings_fema`, and the page's sources say so). VGIN's layer has gaps:

| County | VGIN footprints | FEMA structures | FEMA structures away from any footprint |
|---|---|---|---|
| King and Queen | 0 | all of them stand in | |
| Northumberland | 4,056 | 14,101 | 11,825 |
| King William | 11,227 | 12,904 | 6,246 |
| Gloucester | 19,101 | 20,962 | 6,142 |
| Mathews | 6,362 | 9,044 | 5,001 |
| Lancaster | 12,943 | 10,038 | 1,670 |
| Essex | 12,726 | 9,246 | 1,130 |
| Accomack | 44,084 | 28,815 | 1,129 |
| Northampton | 15,804 | 10,874 | 1,096 |
| Richmond | 10,042 | 6,480 | 972 |
| Poquoson | 6,459 | 6,050 | 698 |
| Middlesex | 12,610 | 10,785 | 665 |

They are other buildings, not the same ones drawn to one side: in Mathews the median distance from such a structure
to the nearest VGIN footprint is 28 m, and only 137 have a VGIN footprint of like size within 15 m that FEMA lacks.
VGIN keeps the sheds FEMA leaves out, which is why it has more where it is complete. If FEMA's service cannot be
read, VGIN's footprints stand alone and the log warns. A locality prepared before 27 Nov 2026 has VGIN's only until
it is prepared again.

## Bridges, piers

The bare-earth DEM has no bridge decks. Road pieces are kept in the table but never flagged when `bridge` > 0:
1 the DEM shows water or a cut under the piece, 2 VGIN codes the segment as a bridge (`SEG_TYPE` 2), 3 the same
street within 60 m of a 1. Buildings standing in the water (`over_water`) are never flagged either. Water is
ground below 1 ft NAVD88, or below the gauge's mean higher high water where that is lower (`prep.over_water_ft`,
from the datum sheets in `stormsense/data/datums`): 1.15 ft at Sewells Point, but 0.52 ft at Tangier, 0.21 at
Windmill Point, 0.68 at Lewisetta. Tangier Island's lanes and yards lie at 0.7 to 1.5 ft, and with 1 ft everywhere
1,219 of its 1,381 road pieces were bridges and 459 of its 581 buildings piers. Elsewhere the change is a handful
of pieces (11 in Mathews, 8 in Lancaster, none in Norfolk). VGIN's own bridge coding is incomplete (most bridges are `SEG_TYPE` 99), so a proper
bridge layer (VDOT structures) would be better.

4 (2026-11-28) is a deck over low ground that the road's own profile gives away (`prep.decks`): an overpass or a
viaduct with no water under it and no bridge code. A road that has run level (within 5 ft for 20 m) cannot then fall
12 ft within 15 m; where the ground under it does, the road has left the ground at an abutment, and the pieces
beyond are under the deck as far as the piece where the ground is back within 5 ft of the abutment's height, or
where the road is over water, if there is one within 600 m along the same street (150 m where the road is not a
highway or a ramp; `prep.chain` follows a street across VGIN's cuts and from two carriageways into one). A road
that comes down a bank and stays low is not under a deck, and neither is a low street with one high piece in it
(another road's deck left in the elevation data), a road that comes down to a landing or crosses a ravine on a
grade (Beaverdam Road and Marina Drive in Surry fall 3 ft in every 5 m for 100 m and more), or a street that runs
low for 575 m between two bluffs (River Walk in Middlesex). Interstate 95 by Pocahontas Island in Petersburg was
the case: 47 ft at the abutment, 3.3 to 4 ft under the deck, a flooded interstate at 4 ft without the rule.

Each locality has only its half of a bridge to the next one: its roads stop in the middle, with no far abutment.
Where a road stops within 150 m of an abutment, with no other road there and within 100 m of the locality's
boundary (`prep.at_border`), the pieces between are under the deck, and there a fall of 10 ft within 20 m is an
abutment. Mount Vernon Avenue and South Arlington Ridge Road over Four Mile Run, between Alexandria and Arlington
(ground at 1 to 3 ft under them), and Interstate 395 towards Washington were flooded roads at every high tide
without it. A dead end inside the locality is a dead end.

On the saved tables of localities prepared before the rule, it would take out 96 pieces that can go under by
10 ft in Portsmouth (ramps and decks of Interstate 264 and the Martin Luther King Freeway), 30 in Gloucester
(Hickory Fork Road, 130 m at 3 to 4 ft between banks of 40%), 12 in Northampton (Ellens Lane, between two dunes:
likely not a deck), 8 in Hampton, 6 in Newport News and none in Poquoson: they get it when they are next
prepared. It finds the Woodrow Wilson Bridge's approaches over Jones Point in Alexandria (382 pieces).
`python -m inundation.check_decks --prepared <dir> --work <prep's work dir> --name <name>` lists what the rule
does on a prepared locality's saved roads, and with `--street` the ground along one street: read the profiles
of the newly marked streets before changing the rule.

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
- Basemap (Derek, 2026-11-27: the plain background made the flooding hard to place): Satellite (Esri World Imagery
  with its road and place-name layers; the page opens on it), Streets (World Street Map) or Plain (the page's own
  drawing of land, water, roads and buildings). The page's view is the area's UTM grid, so each Web Mercator map tile
  is drawn through the affine map of three of its corners (`drawBase`; `utm` and `lonlat` are Krueger's series).
  Over a basemap the flood picture leaves ordinary tidal water clear and the page's own road network and building
  outlines are not drawn, only the flagged ones. Where the map tiles cannot be loaded (a Claude artifact blocks other
  hosts) the page falls back to Plain and says so. A viewer's own choice is remembered in the browser.
- Views: Now, Forecast peak (the page opens here), Last observed, Historical Floods, Scenario. Scenario sets one
  water level for every gauge, -2 to 10 ft NAVD88 in 0.25 ft steps, and the roads, buildings and damage follow it; it
  opens at the step nearest 7 ft above MLLW at Sewells Point.
- "StormSense Prediction Plot / Anticipated Impacts in the Next 72 Hours": the forecast at the NOAA tide gauge nearest
  the area's centre, one inside the area first (`static.json` `plot_gauge`: Sewells Point for Norfolk, Portsmouth and
  Hampton, Yorktown for Newport News, Money Point for Chesapeake, the Bay Bridge Tunnel for Virginia Beach), with
  its 10-90% band and NWS stages on the left axis, road miles and buildings on two right axes (Derek's layout,
  2026-11-27).

## The site and the map of all localities

`areas.csv` lists the localities (FIPS, the short name used in file names and URLs, the title, the region, and a
`box` for a part of a locality, see "Counties that are mostly water"): Hampton Roads, the Middle Peninsula, the
Northern Neck, the Eastern Shore, the upper James, the Fredericksburg area and Northern Virginia, which is all 45
localities of Derek's coastal list since 28 Nov 2026. Add a row to add a locality.

    python -m inundation.site --prepared <folder of <name>/ folders> --latest <Latest site> --out site/inundation

writes one site: `<name>/` is each prepared locality's own map page (its tables under `<name>/data/`) and
`index.html` is the map of all of them (`templates/statewide.html`). That map opens on the whole coastal zone, with every StormSense gauge, over a
satellite basemap with roads and place names, or a street map (Esri's World Imagery, its two reference layers and
World Street Map, drawn with Leaflet from `crest/jslib`). The water is drawn from the localities' own depth tiles:
they are in each locality's UTM zone, so the page reprojects them as it draws (a far view from `tiles/z1.png`,
`tiles/z0.png` closer, the 1024-pixel tiles from zoom 14), cut to the locality's outline (`<name>_outline.geojson`,
written by `prep`) so neighbours do not paint over each other. Roads, buildings and damage are counted from
`areas.json`'s `curves`: for each locality and gauge, the totals at each of the 49 levels, read off at the gauge's
water level, so the far view never loads the road and building tables. From zoom 15 in, the page reads the
`static.json` of each locality in view (its own page's data) and draws every flooded or ponded road piece and
flooded building on the water; a click on one gives its depth, ground and gauge, a click elsewhere in a locality its
counts and the link to its own map. The plot is Sewells Point's forecast until a gauge is clicked: then it is that
gauge's, with its flood levels (Derek, 2026-11-27).

Gauges, on the map of all localities and on each locality's own: the dot has the color of the National Weather
Service flood category of the gauge's level at the selected time (green below Action, yellow Action, orange Minor,
red Moderate, purple Major, navy where no levels are set, grey without a reading), and a ring around it the color of
the highest category its forecast reaches in the 72 hours. Zoomed out, the map of all localities labels the gauges
with their abbreviations (a label that would sit on another gives way: the plot's gauge, the highest category and
NOAA's gauges keep theirs); from zoom 10.5 in, the label also carries the level. When no gauge that a prepared
locality follows is at Action, a note under the map says that this is why there is no blue to see.

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
| 2026 Thanksgiving Day Nor'easter | 5.99 ft NAVD88 (7.60 ft MLLW), 2026-11-26 15:18Z | 68 (with 18 forecast runs; the Rappahannock at Fredericksburg crested at 8.62 ft on 27 Nov 13:42Z) |
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
| Gloucester County (no pipes found) | 44.5 | – | 4.4 | 1,175 | 614 | $2.18M | 49.0 / 1,392 |
| Mathews County (no pipes or values found) | 37.8 | – | 6.0 | 1,542 | – | – | 44.2 / 1,878 |
| Middlesex County (no pipes or values found) | 1.3 | – | 0.4 | 56 | – | – | 1.7 / 84 |
| King and Queen County (no pipes or values found; FEMA USA Structures outlines) | 2.6 | – | 0.5 | 25 | – | – | 3.2 / 35 |
| King William County (no pipes or values found) | 2.2 | – | 0.3 | 145 | – | – | 2.6 / 161 |
| Essex County (no pipes or values found) | 3.1 | – | 0.6 | 59 | – | – | 3.8 / 95 |
| Lancaster County (no pipes or values found) | 8.8 | – | 2.1 | 281 | – | – | 11.2 / 378 |
| Northumberland County (no pipes or values found) | 3.0 | – | 0.6 | 235 | – | – | 3.6 / 290 |
| Richmond County (no pipes or values found) | 1.0 | – | 0.2 | 51 | – | – | 1.2 / 66 |
| Westmoreland County (no pipes or values found) | 2.1 | – | 0.6 | 286 | – | – | 2.7 / 331 |
| Northampton County (no pipes or values found) | 8.3 | – | 0.8 | 455 | – | – | 9.2 / 486 |
| Accomack County, south of 37.75 N (no pipes or values found) | 33.7 | – | 4.5 | 891 | – | – | 40.0 / 1,099 |
| Accomack County, north of 37.75 N (no pipes or values found) | 96.9 | – | 5.9 | 7,422 | – | – | 103.7 / 7,854 |
| Tangier Island (no pipes or values found) | 3.7 | – | 0.0 | 510 | – | – | 3.7 / 510 |
| Surry County (no pipes or values looked for) | 1.1 | – | 0.1 | 107 | – | – | 1.2 / 110 |
| Prince George County, near tidal water (no pipes or values looked for) | 0.1 | – | 0.0 | 10 | – | – | 0.2 / 11 |
| Hopewell (no pipes or values looked for) | 0.0 | – | 0.0 | 1 | – | – | 0.0 / 1 |
| Petersburg (no pipes or values looked for) | 0.0 | – | 0.0 | 0 | – | – | 0.0 / 0 |
| Colonial Heights (no pipes or values looked for) | 0.0 | – | 0.0 | 0 | – | – | 0.0 / 0 |
| Charles City County (no pipes or values looked for) | 1.1 | – | 0.1 | 5 | – | – | 1.2 / 8 |
| Chesterfield County, near tidal water (no pipes or values looked for) | 0.0 | – | 0.0 | 6 | – | – | 0.0 / 8 |
| Henrico County, near tidal water (no pipes or values looked for) | 0.0 | – | 0.0 | 8 | – | – | 0.0 / 11 |
| Richmond (City) (no pipes or values looked for) | 0.0 | – | 0.0 | 0 | – | – | 0.0 / 0 |
| New Kent County (no pipes or values looked for) | 1.0 | – | 0.1 | 80 | – | – | 1.1 / 95 |
| Hanover County, near tidal water (no pipes or values looked for) | 0.0 | – | 0.0 | 0 | – | – | 0.0 / 0 |
| Caroline County, near tidal water (no pipes or values looked for) | 0.0 | – | 0.0 | 2 | – | – | 0.0 / 3 |
| Spotsylvania County, near tidal water (no pipes or values looked for) | 0.0 | – | 0.0 | 0 | – | – | 0.0 / 0 |
| Fredericksburg (no pipes or values looked for) | 0.0 | – | 0.0 | 0 | – | – | 0.0 / 0 |
| King George County (no pipes or values looked for) | 0.3 | – | 0.1 | 12 | – | – | 0.4 / 18 |
| Stafford County, near tidal water (no pipes or values looked for) | 0.1 | – | 0.0 | 2 | – | – | 0.1 / 8 |
| Prince William County, near tidal water (no pipes or values looked for) | 1.4 | – | 0.4 | 13 | – | – | 1.8 / 17 |
| Fairfax County, near tidal water (no pipes or values looked for) | 0.1 | – | 0.0 | 19 | – | – | 0.1 / 22 |
| Arlington County (no pipes or values looked for) | 0.0 | – | 0.0 | 1 | – | – | 0.0 / 2 |
| Alexandria (no pipes or values looked for) | 0.3 | – | 0.0 | 5 | – | – | 0.4 / 9 |

- The rows from Prince William County down (Northern Virginia, 28 Nov): the forecast peak was 4.4 ft NAVD88 at
  Alexandria's Potomac gauge, 3.9 at Four Mile Run, 4.3 at the Washington Channel, 5.1 at Georgetown, 3.8 at
  Leesylvania and 3.6 at Indian Head. Alexandria's flooded roads are King, Union, Strand and Prince Streets in Old
  Town, where the ground is 1.9 to 3 ft. Prince William's are mostly unnamed roads through the marsh of the Occoquan
  Bay refuge, at 1.2 to 3 ft, and its four buildings in the water at the run's start are shore structures on ground
  at 1.1 to 1.3 ft. Ten of Fairfax's nineteen buildings stand on the Occoquan at Colchester, the others on Dogue and
  Little Hunting Creeks and Pohick Bay. Arlington's one is a 36 m2 structure in the Columbia Island lagoon. The
  Potomac itself is the District's and Maryland's: only Virginia's shore is mapped.
- The rows from Caroline County to Stafford County (the Fredericksburg area, 28 Nov): the forecast peak was 3.7 ft NAVD88 at
  Fredericksburg, 3.4 at Tappahannock, 4.4 at Dahlgren, 3.8 at Leesylvania and 2.9 at Crow's Nest. King George's
  flooded roads and nine of its buildings follow Dahlgren. Through Fredericksburg the elevation data has the
  Rappahannock's surface at 5.0 ft (from the railroad bridge to above the city dock) and 6.5 ft at the Chatham
  bridge, flown at a higher stage than the -1.1 ft below the city, so the map draws the river there only from
  5.25 ft up. The ground beside it is not affected: nothing in the data lies lower than that water.
- The rows from Surry County to Hanover County (the upper James, 28 Nov) are small because little there lies low: the James and
  the Appomattox run between bluffs above Hopewell, and the forecast peak was 4.9 ft NAVD88 at Jordan Point, 5.8 at
  Richmond Locks and 6.3 at Pocahontas. Charles City County has no VGIN footprints at all (6,381 FEMA outlines).
- The rows from Gloucester down were prepared on 27 and 28 Nov with the gauges by water body, the high tide limit
  for water under a deck and FEMA's outlines where VGIN has no footprint; the Hampton Roads rows above them are as
  prepared before those three changes. FEMA's outlines took Mathews from 957 to 1,542 buildings, Gloucester from 719
  to 1,175 and Northumberland from 69 to 235; the gauge rules took Middlesex from 78 buildings to 56 (its shores now follow
  Windmill Point, the Piankatank and Tappahannock, not West Point on the York).
- The Eastern Shore's totals follow two gauges that read far above the rest. NOAA's own preliminary record at
  Wachapreague peaks at 8.44 ft NAVD88 on 26 Nov (Kiptopeke 4.93, Sewells Point 5.98), and the forecast of 00Z had it
  at 7.57 ft; Chincoteague's forecast peak was 5.29 ft, and 6,817 of northern Accomack's 7,422 buildings are on that
  gauge (Chincoteague Island's ground is mostly 3 to 5 ft). Onancock's forecast peak was 2.74 ft, with Saxis at 4.22
  and Kiptopeke at 5.01 either side of it: a question for the forecast.
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

## Gauges by water body

The nearest gauge in a straight line is often on other water: Wachapreague (seaside) is the nearest gauge to much of
the Eastern Shore's bay shore, Dahlgren (Potomac) to northern Essex on the Rappahannock, West Point (York) to
Middlesex's Rappahannock shore. A row of `tether_overrides.csv` can name several gauges joined by `|`, and the
watersheds it covers (a code ending in `*` covers every HUC-12 that starts with it) take the nearest of those:

| Watersheds | Water | Gauges |
|---|---|---|
| `0204030*` | Eastern Shore, sea side (Chincoteague Bay and the lagoons) | CHIV, WACH, CCHV |
| `0208011*` | Eastern Shore, bay side, Tangier and Pocomoke Sounds | KIPT, ONAV, SAXV, PRTV, CRSM |
| `02070011*` | lower Potomac | LEWI, SGSM, LENM, DAHL, CNRC, LSPV, IHDM |
| `02080104*` | tidal Rappahannock | WMPT, RAPV, FEDV |
| `0208010201*` | the Northern Neck's bay shore | LEWI, WMPT |
| `02080105*` | Mattaponi | WSPV |
| `02080106*` | Pamunkey | GS02, WSPV |
| `0208010202*` | Dragon Swamp | WCKV |
| `0208010203*` | Piankatank and Milford Haven | WCKV, WMPT |
| `0208020604*` to `06*` | Chickahominy | USJT (its own gauge at Mamanahunt, GS01, has no trusted readings yet) |
| `0208020601*` | the James from the falls at Richmond to Hopewell | RICV, JPHV |
| `0208020602*` | the James from Hopewell to Flowerdew Hundred | JPHV, USJT |
| `02080207*` | Appomattox | ARPV, JPHV |
| `020802071002` | the lower Appomattox, Ashton Creek to City Point | JPHV (Pocahontas, at the falls, stands about a foot above the James) |
| `02070010*` | tidal Potomac from Occoquan Bay to Little Falls, the Anacostia, Four Mile Run | WADC, GTND, AXTV, ALEV, BPAD, LSPV, IHDM |

The seaside gauges ran far above the bay on 26 Nov 2026: Wachapreague reached 8.44 ft NAVD88 (NOAA preliminary) and
Oyster 6.55 ft before its record stopped, against 4.93 ft at Kiptopeke and 5.21 ft at Chincoteague, so which side
of the peninsula a road is tied to decides whether it floods.

## Counties that are mostly water

A county's boundary runs far out into the bay and the ocean, and the frame is 1 m cells:

- `prep.land_bounds` cuts the frame back to the roads and buildings plus 1.5 km (Northampton 25% smaller).
- `spread.open_sea` gives every body of open water one node of the union-find: 8 m squares that are wholly below
  the lowest level and joined in bodies of 64 squares or more (Northampton: 1.29 of 2.19 billion cells in 44
  bodies). The answer is the same as cell by cell; only the time and the memory change.
- A locality whose frame is still too large is prepared in parts: `areas.csv` has a `box` column (west south east
  north, degrees) and `prep --box` keeps the roads and buildings that start inside and clips the outline. Accomack's
  cut frame is 4.30 billion cells, more than the union-find's 32-bit nodes and about 26 GB of grids, so it is three
  areas: `accomack_north` and `accomack_south` (split at 37.75 N, between Onancock and Parksley) and `tangier`.

## Counties that are mostly upland

Henrico, Chesterfield, Hanover, Prince George, Caroline, Spotsylvania, Stafford, Prince William and Fairfax reach
from a tidal river to the Piedmont, and nearly all of their
roads and buildings stand higher than any level mapped here. A row of `areas.csv` with `low` = 1 (`prep --low`)
keeps only what the water could reach:

- `depth.low_grid` reads one row of the 1 m tiles every 64 m, whole, and marks the 64 m cells with ground or water
  at or below 10 ft NAVD88.
- `depth.tidal_low` keeps the low patches the tide can be part of: the largest, every one that runs off the frame's
  edge and every one of 2 km2 or more, with patches within two cells of each other counted as one. A quarry floor
  below sea level in the middle of the county is left out (two of them sit in Henrico's frame).
- `prep.near_low` keeps the roads with a vertex, and the buildings with their first point, within 1.5 km of that
  ground, and cuts the frame to them and to the whole of each low patch they lie near, so that the river they flood
  from is in the frame.

| County | road segments kept | buildings kept | frame, billion cells |
|---|---|---|---|
| Henrico | 314 of 23,014 | 2,963 of 179,682 | 0.49 (1.78 whole) |
| Chesterfield | 1,935 of 25,594 | 16,167 of 210,830 | 0.74 (2.22 whole) |
| Prince George | 457 of 2,627 | 5,070 of 30,325 | 0.67 |
| Hanover | 61 of 7,740 | 975 of 89,063 | 0.26 |
| Caroline | 240 of 3,339 | 1,817 of 27,853 | 0.57 |
| Spotsylvania | 372 of 9,712 | 3,644 of 96,492 | 0.14 |
| Stafford | 1,783 of 8,106 | 14,726 of 68,438 | 0.76 |
| Prince William | 5,518 of 26,614 | 27,287 of 176,143 | 0.36 |
| Fairfax | 5,847 of 55,763 | 30,090 of 272,000 | 0.93 |

`<name>_prep.json` has the counts under `low_frame`, and the page says what it holds. The tables of such a
locality are not the whole locality.

The localities' boundary layer holds Prince George County in two rows, a 2 ha sliver first: `vgin.locality` puts
every row's polygons together.

## Watersheds without a gauge

A HUC-12 whose water level no StormSense gauge measures is left out rather than tied to the nearest gauge:
`tether_overrides.csv` takes `-` as the station, and a code ending in `*` covers every HUC-12 that starts with it.
The first rule was `03010205*` (the Blackwater and Nottoway, `03010202*` and `03010201*`, which run to Albemarle
Sound, followed): Back Bay, the North Landing River and the Northwest River (the south of Virginia
Beach and Chesapeake) drain to Currituck Sound, are driven by wind rather than tide, and are shut off from the
Elizabeth River by the Great Bridge lock. Tied to Lake Rudee they showed 66 road miles flooded on a day when nothing
there was. That leaves out 239,164 road pieces and 78,804 buildings in Virginia Beach and 173,410 pieces and 41,189 buildings in
Chesapeake (Great Bridge south of the lock, Hickory, the Dismal Swamp). Their pieces and buildings stay in the tables with an empty `station`; `status` and the page skip them and
the page says which watersheds are missing. A gauge there (or a Currituck Sound forecast) brings them back.

## Open points

- Building rule: half the footprint under 0.5 ft (now) or 0.5 ft at the low side (`wet_low_ft`), Derek's call.
- Low spots with no pipe to tidal water stay dry; rain is not in it.
- Above the head of tide (Richmond, Petersburg, Fredericksburg, Little Falls) a river in flood from rain is not in
  these maps: the gauges at the falls (RICV, ARPV, FEDV) give the tide's level below them. Pocahontas (ARPV) stood
  about a foot higher than the James at Jordan Point through the week before 26 Nov 2026; whether that is the
  river's slope at the falls or its datum (NWPS: NAVD88 = stage - 8.03 ft) is not settled.
- Other buildings than homes get no damage estimate; contents are not estimated; first floor heights are assumed.
- The water picture on the page is drawn at 5 m (roads and buildings use the 1 m DEM); the tiles would carry 1 m.
- Historical floods use the tide gauges only (no high water marks, no rain), on today's ground. Chesapeake Bay
  Bridge Tunnel's old station (8638863) and other historic stations are on an estimated NAVD88.
- Storm drain layers and parcel values have not been looked for in the localities added on 28 Nov (the upper James,
  the Fredericksburg area, Northern Virginia): their water travels over the surface only and they have no damage
  estimate. Alexandria, Arlington and Fairfax publish their stormwater networks.
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
