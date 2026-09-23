"""Symbolic line colours -> hex.

`datasets/lines.csv` stores colours as names (`light_green`, `arl_red`, ...).
Keeping the one mapping here means the API, any Python report and the web UI
can never disagree about what colour a line is.
"""
from __future__ import annotations
import colorsys
import logging

logger = logging.getLogger(__name__)

LINE_COLORS: dict[str, str] = {
    "light_green": "#6CBE45",   # BTS Sukhumvit
    "dark_green": "#00734B",    # BTS Silom
    "gold": "#C1A15A",          # Gold Line APM
    "blue": "#1E4E9C",          # MRT Blue
    "purple": "#7B3F99",        # MRT Purple (+ South)
    "yellow": "#F5C400",        # MRT Yellow monorail
    "pink": "#E6398A",          # MRT Pink (+ Muang Thong spur)
    "dark_red": "#A3132B",      # SRT Dark Red (+ Rangsit ext.)
    "light_red": "#E8635A",     # SRT Light Red (+ 2 ext.)
    "arl_red": "#D1232A",       # Airport Rail Link
    "orange": "#F07D00",        # MRT Orange East/West
    "brown": "#7B4B28",         # MRT Brown monorail
    "grey": "#808285",          # Grey Line
    "silver": "#9FA2A6",        # Silver Line LRT
    "light_blue": "#5BC2E7",    # Light Blue Line
}

# `operating` lines are solid; everything still being built is dashed, which is
# also what separates the near-identical `grey` and `silver` planned lines.
STATUS_DASH: dict[str, str] = {
    "operating": "",
    "under_construction": "10 6",
    "planned": "2 6",
}


def hex_for(name: str) -> str:
    """Hex for a symbolic colour name, deriving a stable fallback for unknowns.

    A line added to lines.csv with a new colour name must never render as
    invisible-on-white, so fall back to a hash-derived hue at fixed saturation
    and lightness rather than to a default colour.
    """
    known = LINE_COLORS.get(name)
    if known is not None:
        return known

    logger.warning("unknown line colour %r; deriving a fallback hue", name)
    hue = (hash(name) % 360) / 360.0
    red, green, blue = colorsys.hls_to_rgb(hue, 0.45, 0.55)
    return "#{:02X}{:02X}{:02X}".format(int(red * 255), int(green * 255), int(blue * 255))


def dash_for(status: str) -> str:
    return STATUS_DASH.get(status, "4 4")
