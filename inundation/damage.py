"""Estimated flood damage to a building from the depth of water at it.

Depth-damage curves: the U.S. Army Corps of Engineers generic depth-damage relationships for residential structures,
the curves the Corps waives site-specific studies for and the ones behind FEMA's Hazus and benefit-cost tools:

    Economic Guidance Memorandum (EGM) 01-03, "Generic Depth-Damage Relationships", CECW-PG, 4 Dec 2000
        (homes without basements: one story, two or more stories, split level)
    EGM 04-01, "Generic Depth-Damage Relationships for Residential Structures with Basements", CECW-PG, 10 Oct 2003
        (repeats the no-basement tables; adds homes with basements)
    https://planning.erdc.dren.mil/toolbox/library/EGMs/egm01-03.pdf and .../egm04-01.pdf

They give the mean damage to the structure as a percent of its value against the depth of water above the first
floor (negative: below it), from surveys of losses in the floods of 1996 to 1998. Those were mostly river floods of
fresh water; salt water and waves do more. Only homes without basements are used here: basements are rare on the
coastal plain. The value they apply to is the depreciated replacement value of the structure; the assessed
improvement value stands in for it.

First floor height above the ground is not surveyed in Virginia. Until first floor elevations are available it
comes from FEMA's Hazus defaults by foundation type (Hazus-MH Flood Model Technical Manual, tables 3.11 and 3.14, as
tabulated in HRPDC report WR20-01, appendix A): slab 1 ft, crawlspace 3 ft before the community's first flood
insurance rate map and 4 ft after, basement 4 ft, pier or post 5 and 6 ft, pile or solid wall 7 and 8 ft.
"""
import numpy as np

DEPTH_FT = np.arange(-2, 17, dtype=float)       # water above the first floor, ft
# mean structure damage, percent of structure value (EGM 01-03 tables 1 to 3; same in EGM 04-01)
STRUCTURE_PCT = {
    "one_story": [0, 2.5, 13.4, 23.3, 32.1, 40.1, 47.1, 53.2, 58.6, 63.2, 67.2, 70.5, 73.2, 75.4, 77.2, 78.5, 79.5, 80.2, 80.7],
    "two_story": [0, 3.0, 9.3, 15.2, 20.9, 26.3, 31.4, 36.2, 40.7, 44.9, 48.8, 52.4, 55.7, 58.7, 61.4, 63.8, 65.9, 67.7, 69.2],
    "split_level": [0, 6.4, 7.2, 9.4, 12.9, 17.4, 22.8, 28.9, 35.5, 42.3, 49.2, 56.1, 62.6, 68.6, 73.9, 78.4, 81.7, 83.8, 84.4],
}
# mean content damage, percent of STRUCTURE value (EGM 01-03 tables 4 and 5); not used yet
CONTENT_PCT = {
    "one_story": [0, 2.4, 8.1, 13.3, 17.9, 22.0, 25.7, 28.8, 31.5, 33.8, 35.7, 37.2, 38.4, 39.2, 39.7, 40.0, 40.0, 40.0, 40.0],
    "two_story": [0, 1.0, 5.0, 8.7, 12.2, 15.5, 18.5, 21.3, 23.9, 26.3, 28.4, 30.3, 32.0, 33.4, 34.7, 35.6, 36.4, 36.9, 37.2],
}
CITATION = ("U.S. Army Corps of Engineers generic depth-damage relationships for residential structures without "
            "basements (Economic Guidance Memorandum 01-03, 2000; repeated in EGM 04-01, 2003)")

# Hazus default first floor heights, ft above grade: (before the first FIRM, after it)
FIRST_FLOOR_FT = {"slab": (1, 1), "fill": (2, 2), "crawlspace": (3, 4), "basement": (4, 4), "pier": (5, 6), "pile": (7, 8), "solid_wall": (7, 8)}
DEFAULT_FOUNDATION = "crawlspace"               # the usual foundation of older houses on low ground in Hampton Roads


def first_floor_ft(year_built, firm_year, foundation=None):
    """Assumed height of the first floor above the ground, ft, from the Hazus defaults."""
    pre, post = FIRST_FLOOR_FT[foundation or DEFAULT_FOUNDATION]
    if firm_year is None:                            # the community's first flood map year is not known: one height
        firm_year = np.inf
    y = np.asarray(year_built, float)
    return np.where(np.isfinite(y) & (y > firm_year), post, pre).astype(np.float32)


def structure_fraction(depth_above_floor_ft, curve="one_story"):
    """Share of the structure's value lost (0 to 1) at a depth above the first floor. NaN depth gives 0.
    `curve` is one name or an array of names from STRUCTURE_PCT; unknown names use the one-story curve."""
    d = np.asarray(depth_above_floor_ft, float)
    c = np.broadcast_to(np.asarray(curve, object), d.shape)
    out = np.interp(d, DEPTH_FT, STRUCTURE_PCT["one_story"], left=0.0)
    for name in ("two_story", "split_level"):
        m = c == name
        if m.any():
            out = np.where(m, np.interp(d, DEPTH_FT, STRUCTURE_PCT[name], left=0.0), out)
    return np.where(np.isfinite(d), out / 100.0, 0.0)


def estimate(depth_ft, value_usd, year_built, curve, firm_year, foundation=None, first_floor=None):
    """Damage in dollars for water `depth_ft` deep over the ground at the building.

    Returns (damage, fraction, depth above the first floor, first floor height). Buildings without a value, or dry
    ones, give 0. `first_floor` (ft above the ground) is used where it is finite, e.g. from an elevation
    certificate; elsewhere the height is assumed from the year built and the foundation.
    """
    ffh = first_floor_ft(year_built, firm_year, foundation)
    if first_floor is not None:
        known = np.asarray(first_floor, float)
        ffh = np.where(np.isfinite(known), known, ffh).astype(np.float32)
    d = np.asarray(depth_ft, float)
    above = np.where(d > 0, d - ffh, np.nan)
    frac = structure_fraction(above, curve)
    v = np.where(np.isfinite(np.asarray(value_usd, float)), np.asarray(value_usd, float), 0.0)
    return frac * v, frac, above, ffh
