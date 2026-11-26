"""StormSense inundation: which roads and buildings the gauges and the 72-hour forecast put under water.

One-time preparation per area (`python -m inundation.prep`): VGIN road centerlines cut into 5 m pieces
and VGIN building footprints each get the ground level under them, the water level (ft NAVD88) at which
water first reaches them, and the gauge they are tethered to. Every forecast run then only compares the
gauges' observations and published fore72 (NAVD88) with those levels (`python -m inundation.status`).
See README.md in this folder.
"""
PONDED_FT = 0.25    # ponded water: yellow
FLOODED_FT = 0.5    # flooded road: orange; flooded building: red
PIECE_M = 5.0       # road pieces
