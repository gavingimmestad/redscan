"""Palettes, gradients, animation clocks and the Rich theme used across RedScan.

RedScan started life with a single red palette. It still ships red by default,
but every colour now comes out of a swappable :class:`Palette` of eight stops
running dark to bright, which means the whole UI can be sent somewhere much
stranger without touching a single call site.

Palettes with a non-zero ``spin`` rotate their hues in real time: ask for the
same ``ramp_color(0.5)`` a second apart and you get a different colour. That is
the entire trick behind the psychedelic modes.
"""

from __future__ import annotations

import colorsys
import math
import time
from dataclasses import dataclass

from rich.text import Text
from rich.theme import Theme

# --- Role names ---------------------------------------------------------------
# Every palette supplies eight colours in the same order, darkest to brightest.
# Call sites ask for a *role*, never a literal, so palettes drop in cleanly.
ROLES = ("void", "deep", "mid", "strong", "core", "bright", "hot", "glow")


@dataclass(frozen=True)
class Palette:
    """Eight ordered colours plus how violently they should move."""

    name: str
    blurb: str
    stops: tuple[str, str, str, str, str, str, str, str]
    spin: float = 0.0       # hue degrees per second; 0 keeps the palette still
    psychedelic: bool = False

    def role(self, name: str) -> str:
        """The static hex for a role, before any hue spin is applied."""
        try:
            return self.stops[ROLES.index(name)]
        except ValueError as exc:  # pragma: no cover - programmer error
            raise KeyError(f"unknown palette role {name!r}") from exc


PALETTES: dict[str, Palette] = {
    "red": Palette(
        name="red",
        blurb="the original: ash, blood, crimson, ember, flare",
        stops=("#2B0509", "#4A0E0E", "#8B0000", "#C81E3A",
               "#E8102B", "#FF3B2E", "#FF6B4A", "#FFB3A0"),
    ),
    "acid": Palette(
        name="acid",
        blurb="purple into magenta into molten yellow",
        stops=("#1A0B2E", "#3B0F6B", "#7B2FF7", "#C026D3",
               "#F0279A", "#FF6B35", "#FFD23F", "#F7FFB0"),
        spin=14.0,
        psychedelic=True,
    ),
    "vapor": Palette(
        name="vapor",
        blurb="vaporwave: indigo, hot pink, pool-water cyan",
        stops=("#1B0938", "#2D1B69", "#5B2E8F", "#9B30D9",
               "#E040A0", "#FF6EC7", "#52E5E7", "#BFFCFF"),
        spin=9.0,
        psychedelic=True,
    ),
    "oil": Palette(
        name="oil",
        blurb="iridescent oil slick on wet asphalt",
        stops=("#0B0F2B", "#16215B", "#1E4F9C", "#178481",
               "#3FBF6F", "#D9C24A", "#F7C2D8", "#F7E8F0"),
        spin=22.0,
        psychedelic=True,
    ),
    "ultra": Palette(
        name="ultra",
        blurb="blacklight: UV violet and electric mint",
        stops=("#120024", "#2A0B4A", "#4B0F9B", "#7B2BFF",
               "#B14BFF", "#19E6FF", "#66FFC2", "#EAFFFF"),
        spin=30.0,
        psychedelic=True,
    ),
    "kaleido": Palette(
        name="kaleido",
        blurb="the full spectrum, spinning hard",
        stops=("#2E0854", "#5B21B6", "#2563EB", "#0EA5B8",
               "#22C55E", "#FACC15", "#FDD98A", "#FFF6D6"),
        spin=48.0,
        psychedelic=True,
    ),
}

DEFAULT_PALETTE = "red"
_ACTIVE: Palette = PALETTES[DEFAULT_PALETTE]


# --- Colour maths -------------------------------------------------------------

def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def luma(color: str) -> float:
    """Perceptual brightness of a colour, 0 (black) to 255 (white)."""
    r, g, b = _hex_to_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def shift_hue(color: str, degrees: float) -> str:
    """Rotate a colour around the hue wheel, keeping lightness and saturation."""
    r, g, b = (channel / 255 for channel in _hex_to_rgb(color))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    hue = (hue + degrees / 360.0) % 1.0
    return _rgb_to_hex(
        tuple(round(channel * 255) for channel in colorsys.hls_to_rgb(hue, light, sat))
    )


def rainbow(position: float, saturation: float = 0.95, value: float = 1.0) -> str:
    """A colour straight off the hue wheel; ``position`` wraps at 1.0."""
    r, g, b = colorsys.hsv_to_rgb(position % 1.0, _clamp(saturation), _clamp(value))
    return _rgb_to_hex((round(r * 255), round(g * 255), round(b * 255)))


def blend(start: str, end: str, t: float) -> str:
    """Linear interpolation between two hex colors."""
    a, b = _hex_to_rgb(start), _hex_to_rgb(end)
    return _rgb_to_hex(tuple(round(x + (y - x) * t) for x, y in zip(a, b)))


# --- The animation clock ------------------------------------------------------

_EPOCH = time.perf_counter()


def clock() -> float:
    """Seconds since the module loaded. The heartbeat for every animation."""
    return time.perf_counter() - _EPOCH


def phase(speed: float = 1.0, offset: float = 0.0) -> float:
    """A monotonically rising number for driving motion."""
    return clock() * speed + offset


def pulse(speed: float = 1.0, offset: float = 0.0) -> float:
    """A smooth 0..1 breath, for anything that should throb gently."""
    return (math.sin(phase(speed, offset) * math.tau) + 1.0) / 2.0


def plasma(x: float, y: float, t: float) -> float:
    """Classic demoscene plasma field sampled at ``(x, y)``, returned as 0..1."""
    value = (
        math.sin(x * 0.18 + t)
        + math.sin(y * 0.33 - t * 0.7)
        + math.sin((x + y) * 0.12 + t * 1.3)
        + math.sin(math.hypot(x - 20.0, y - 6.0) * 0.25 - t * 1.1)
    )
    return _clamp((value / 4.0 + 1.0) / 2.0)


# --- Palette access -----------------------------------------------------------

def active_palette() -> Palette:
    """The palette currently painting the UI."""
    return _ACTIVE


def set_palette(name: str) -> Palette:
    """Switch the global palette. Returns it, for convenience."""
    global _ACTIVE
    try:
        _ACTIVE = PALETTES[name.lower()]
    except KeyError as exc:
        known = ", ".join(sorted(PALETTES))
        raise ValueError(f"unknown palette {name!r} - try one of: {known}") from exc
    return _ACTIVE


def is_tripping() -> bool:
    """True when the active palette is one of the moving ones."""
    return _ACTIVE.psychedelic


def spin_offset(palette: Palette | None = None) -> float:
    """How far the active palette's hues have rotated, in degrees."""
    palette = palette or _ACTIVE
    return (clock() * palette.spin) % 360.0 if palette.spin else 0.0


def role(name: str, palette: Palette | None = None) -> str:
    """A role colour, hue-rotated to wherever the palette currently is."""
    palette = palette or _ACTIVE
    color = palette.role(name)
    drift = spin_offset(palette)
    return shift_hue(color, drift) if drift else color


def heat_stops(palette: Palette | None = None) -> list[str]:
    """The full dark-to-bright ramp, hue-rotated to the current moment."""
    palette = palette or _ACTIVE
    drift = spin_offset(palette)
    stops = list(palette.stops[1:])  # drop 'void' - too dark to read against
    return [shift_hue(stop, drift) for stop in stops] if drift else stops


def banner_stops(palette: Palette | None = None) -> list[str]:
    """A punchier ramp for large type: the middle of the palette."""
    return heat_stops(palette)[1:-1] or heat_stops(palette)


# --- Gradients ----------------------------------------------------------------

def ramp_color(t: float, stops: list[str] | None = None) -> str:
    """Sample a multi-stop gradient at position ``t`` in [0, 1]."""
    stops = stops or heat_stops()
    t = _clamp(t)
    if len(stops) == 1:
        return stops[0]
    span = 1.0 / (len(stops) - 1)
    idx = min(int(t / span), len(stops) - 2)
    return blend(stops[idx], stops[idx + 1], (t - idx * span) / span)


def cycle_color(t: float, stops: list[str] | None = None) -> str:
    """Like :func:`ramp_color` but seamless: ``t`` wraps instead of clamping."""
    stops = stops or heat_stops()
    if len(stops) == 1:
        return stops[0]
    loop = [*stops, stops[0]]
    t = t % 1.0
    span = 1.0 / (len(loop) - 1)
    idx = min(int(t / span), len(loop) - 2)
    return blend(loop[idx], loop[idx + 1], (t - idx * span) / span)


def gradient_text(message: str, stops: list[str] | None = None,
                  bold: bool = True) -> Text:
    """Paint a string character-by-character along the gradient."""
    text = Text(message)
    divisor = max(len(message) - 1, 1)
    for i, _ in enumerate(message):
        style = ramp_color(i / divisor, stops)
        text.stylize(f"bold {style}" if bold else style, i, i + 1)
    return text


def flowing_text(message: str, offset: float = 0.0, stops: list[str] | None = None,
                 bold: bool = True, wavelength: float = 18.0) -> Text:
    """Gradient text whose colours *scroll*, so the string looks like it flows.

    ``offset`` is normally a value from :func:`phase`; one whole unit shifts the
    gradient by one full cycle.
    """
    text = Text(message)
    for i, _ in enumerate(message):
        style = cycle_color(i / max(wavelength, 1.0) - offset, stops)
        text.stylize(f"bold {style}" if bold else style, i, i + 1)
    return text


def heat_style(t: float) -> str:
    """Style string for a 0-1 'heat' value, cool (dark) to hot (bright)."""
    return f"bold {ramp_color(t)}"


# --- The Rich theme -----------------------------------------------------------

def build_theme(palette: Palette | None = None) -> Theme:
    """A Rich theme whose every style is derived from a palette's roles.

    Hue spin is deliberately *not* baked in here: Rich resolves a theme once,
    so anything that needs to move asks for colours per frame instead.
    """
    palette = palette or _ACTIVE
    void, deep, mid, strong, core, bright, hot, glow = palette.stops
    return Theme(
        {
            "rs.dim": deep,
            "rs.muted": blend(mid, glow, 0.45),
            "rs.label": f"bold {glow}",
            "rs.value": f"bold {hot}",
            "rs.accent": f"bold {core}",
            "rs.hot": f"bold {bright}",
            "rs.open": f"bold {hot}",
            "rs.closed": deep,
            "rs.filtered": blend(deep, glow, 0.4),
            "rs.good": f"bold {hot}",
            "rs.bad": f"bold {core}",
            "rs.border": strong,
            "rs.border.dim": deep,
            "rs.void": void,
            "rs.title": f"bold {core}",
            "rs.bar.back": deep,
            "rs.bar.done": core,
            "rs.bar.finished": hot,
            "rs.bar.pulse": bright,
            "rs.spinner": bright,
            "rs.rule": strong,
            "rs.key": f"bold {bright}",
            "rs.warn": f"bold {bright}",
        }
    )


# --- Backwards-compatible red constants --------------------------------------
# RedScan 1.0 exposed these by name and they are still the red palette's stops.
_RED = PALETTES["red"]
ASH, SOOT, BLOOD, CRIMSON, RED, EMBER, FLARE, GLOW = _RED.stops

HEAT = list(_RED.stops[1:])
BANNER_RAMP = [BLOOD, CRIMSON, RED, EMBER, FLARE]
THEME = build_theme(_RED)
