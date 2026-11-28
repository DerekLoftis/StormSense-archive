"""What the deck rule (prep.decks) does on a prepared locality's saved roads, without preparing it again.

    python -m inundation.check_decks --prepared <dir with <name>_roads.csv.gz and <name>_prep.json>
        --work <the prep work dir: roads_raw.geojson, locality.geojson> --name <name> [--street "Beaverdam Rd"]

Lists the streets with pieces the rule puts under a deck that were on the ground (or a deck) in the saved table and
that water reaches by 10 ft, and those it takes back. With --street, the ground along that street line by line
(`*` under a deck, `b` any other bridge code in the table): read the profile before believing a new deck. A road
that comes down to a landing or crosses a ravine on a grade must stay a road.
"""
import argparse
import json

import numpy as np

from . import prep, segments


def read(prepared, work, name):
    import pandas as pd
    from pyproj import Transformer
    with open(f"{prepared}/{name}_prep.json") as fh:
        epsg = json.load(fh)["epsg"]
    fwd = Transformer.from_crs(4326, epsg, always_xy=True).transform
    with open(f"{work}/roads_raw.geojson") as fh:
        roads = json.load(fh)["features"]
    with open(f"{work}/locality.geojson") as fh:
        loc = json.load(fh)
    table = pd.read_csv(f"{prepared}/{name}_roads.csv.gz", low_memory=False)
    pieces = segments.split(roads, fwd)
    if len(pieces["road"]) != len(table):
        raise SystemExit(f"{name}: the saved table has {len(table)} pieces, the roads in {work} make "
                         f"{len(pieces['road'])} (a county cut to its low ground keeps its cut roads elsewhere)")
    return pieces, table, prep.decks(pieces, table.ground_ft.values, border=prep.at_border(pieces, loc, fwd))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prepared", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--street")
    ap.add_argument("--reach-ft", type=float, default=10.0)
    a = ap.parse_args(argv)
    pieces, table, deck = read(a.prepared, a.work, a.name)
    reach = (table.connect_ft.values <= a.reach_ft)
    if a.street:
        on = (table.street.astype(str).str.lower() == a.street.lower()).values
        road = np.asarray(pieces["road"])
        for r in dict.fromkeys(road[on]):
            i = np.flatnonzero(on & (road == r))
            print(f"line {r} from {table.lat[i[0]]:.5f}, {table.lon[i[0]]:.5f}:",
                  " ".join(f"{table.ground_ft[k]:.0f}{'*' if deck[k] else ''}{'b' if table.bridge[k] not in (0, 4) else ''}"
                           for k in i))
        return
    for title, pick in (("under a deck now", deck & np.isin(table.bridge.values, (0, 4)) & reach),
                        ("a deck in the table, on the ground now", ~deck & (table.bridge.values == 4) & reach)):
        print(f"{a.name}: {int(pick.sum())} pieces within reach of {a.reach_ft:g} ft, {title}")
        if pick.any():
            print(table[pick].groupby("street", dropna=False)
                  .agg(pieces=("piece", "size"), lowest_ft=("ground_ft", "min"), highest_ft=("ground_ft", "max"),
                       lat=("lat", "mean"), lon=("lon", "mean"))
                  .sort_values("pieces", ascending=False).head(25).round(5).to_string())


if __name__ == "__main__":
    main()
