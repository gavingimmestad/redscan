"""Red palette, gradients and the Rich theme used across RedScan."""

from __future__ import annotations

from rich.theme import Theme
from rich.text import Text

# --- The palette: ash -> blood -> crimson -> ember -> flare -------------------
ASH = "#2B0509"
SOOT = "#4A0E0E"
BLOOD = "#8B0000"
CRIMSON = "#C81E3A"
RED = "#E8102B"
EMBER = "#FF3B2E"
FLARE = "#FF6B4A"
GLOW = "#FFB3A0"

HEAT = [SOOT, BLOOD, CRIMSON, RED, EMBER, FLARE, GLOW]
BANNER_RAMP = [BLOOD, CRIMSON, RED, EMBER, FLARE]

THEME = Theme(
    {
        "rs.dim": f"{SOOT}",
        "rs.muted": "#9A6A6A",
        "rs.label": f"bold {GLOW}",
        "rs.value": f"bold {FLARE}",
        "rs.accent": f"bold {RED}",
        "rs.hot": f"bold {EMBER}",
        "rs.open": f"bold {FLARE}",
        "rs.closed": f"{SOOT}",
        "rs.filtered": "#8A5A5A",
        "rs.good": f"bold {FLARE}",
        "rs.bad": f"bold {BLOOD}",
        "rs.border": CRIMSON,
        "rs.border.dim": SOOT,
        "rs.title": f"bold {RED}",
        "rs.bar.back": SOOT,
        "rs.bar.done": RED,
        "rs.bar.finished": FLARE,
        "rs.bar.pulse": EMBER,
        "rs.spinner": EMBER,
        "rs.rule": CRIMSON,
        "rs.key": f"bold {EMBER}",
        "rs.warn": f"bold {EMBER}",
    }
)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def blend(start: str, end: str, t: float) -> str:
    """Linear interpolation between two hex colors."""
    a, b = _hex_to_rgb(start), _hex_to_rgb(end)
    return _rgb_to_hex(tuple(round(x + (y - x) * t) for x, y in zip(a, b)))


def ramp_color(t: float, stops: list[str] | None = None) -> str:
    """Sample a multi-stop gradient at position ``t`` in [0, 1]."""
    stops = stops or HEAT
    t = min(max(t, 0.0), 1.0)
    if len(stops) == 1:
        return stops[0]
    span = 1.0 / (len(stops) - 1)
    idx = min(int(t / span), len(stops) - 2)
    return blend(stops[idx], stops[idx + 1], (t - idx * span) / span)


def gradient_text(message: str, stops: list[str] | None = None, bold: bool = True) -> Text:
    """Paint a string character-by-character along the gradient."""
    text = Text(message)
    divisor = max(len(message) - 1, 1)
    for i, _ in enumerate(message):
        style = ramp_color(i / divisor, stops)
        text.stylize(f"bold {style}" if bold else style, i, i + 1)
    return text


def heat_style(t: float) -> str:
    """Style string for a 0-1 'heat' value, cool (dark) to hot (bright)."""
    return f"bold {ramp_color(t)}"
