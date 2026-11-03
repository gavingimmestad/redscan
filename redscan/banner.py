"""The obligatory oversized ASCII banner, now with weather.

Under the default red palette the banner looks exactly as it always has: a
top-to-bottom gradient, one hue per line. Under a psychedelic palette the same
art is lit per-character by a moving plasma field, the radar sweep grows extra
comet heads, and the rules under it ripple.
"""

from __future__ import annotations

import math

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from .theme import (
    BANNER_RAMP,
    banner_stops,
    cycle_color,
    flowing_text,
    heat_stops,
    is_tripping,
    phase as clock_phase,
    plasma,
    pulse,
    ramp_color,
    role,
)

BLOCK_LOGO = r"""
██████╗ ███████╗██████╗ ███████╗ ██████╗ █████╗ ███╗   ██╗
██╔══██╗██╔════╝██╔══██╗██╔════╝██╔════╝██╔══██╗████╗  ██║
██████╔╝█████╗  ██║  ██║███████╗██║     ███████║██╔██╗ ██║
██╔══██╗██╔══╝  ██║  ██║╚════██║██║     ██╔══██║██║╚██╗██║
██║  ██║███████╗██████╔╝███████║╚██████╗██║  ██║██║ ╚████║
╚═╝  ╚═╝╚══════╝╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝
""".strip("\n")

ASCII_LOGO = r"""
 ____  _____ ____  ____   ____    _    _   _
|  _ \| ____|  _ \/ ___| / ___|  / \  | \ | |
| |_) |  _| | | | \___ \| |     / _ \ |  \| |
|  _ <| |___| |_| |___) | |___ / ___ \| |\  |
|_| \_\_____|____/|____/ \____/_/   \_\_| \_|
""".strip("\n")

TAGLINE = "PORT SCANNER  ::  IP PINGER  ::  NETWORK RECON"
SUBTITLE = "no packets were harmed in the making of this banner"
TRIP_SUBTITLE = "no packets were harmed, but several were deeply moved"

RIPPLE = "⎺⎻⎼⎽⎼⎻"


def _supports_blocks(console: Console) -> bool:
    """True when the active encoding can actually render block characters."""
    try:
        "█╗╚═".encode(console.file.encoding or "utf-8")
    except (LookupError, UnicodeEncodeError, AttributeError):
        return False
    return True


def logo_art(console: Console) -> list[str]:
    """The logo lines the console is actually able to draw."""
    art = BLOCK_LOGO if _supports_blocks(console) else ASCII_LOGO
    return art.splitlines()


def _gradient_logo(lines: list[str], visible: list[str]) -> Text:
    """The classic look: one hue per line, down the banner ramp."""
    text = Text(justify="center")
    divisor = max(len(lines) - 1, 1)
    stops = banner_stops() if is_tripping() else BANNER_RAMP
    for i, line in enumerate(visible):
        text.append(line + ("\n" if i < len(visible) - 1 else ""),
                    style=f"bold {ramp_color(i / divisor, stops)}")
    return text


def _plasma_logo(visible: list[str], t: float) -> Text:
    """Light the art per character from a moving plasma field."""
    text = Text(justify="center")
    stops = heat_stops()
    for y, line in enumerate(visible):
        for x, char in enumerate(line):
            if char == " ":
                text.append(" ")
                continue
            # Stretch x: terminal cells are about twice as tall as they are
            # wide, so halving x keeps the field from looking squashed.
            level = plasma(x * 0.5, y * 1.6, t)
            text.append(char, style=f"bold {cycle_color(level, stops)}")
        if y < len(visible) - 1:
            text.append("\n")
    return text


def logo_text(console: Console, rows: int | None = None,
              t: float | None = None) -> Text:
    """The logo, gradient-lit normally and plasma-lit when tripping.

    ``rows`` clips the art to its first N lines, which is how the startup
    animation wipes it in. ``t`` pins the plasma field to a moment in time;
    omitted, it follows the shared animation clock.
    """
    lines = logo_art(console)
    visible = lines if rows is None else lines[:rows]
    if not is_tripping():
        return _gradient_logo(lines, visible)
    return _plasma_logo(visible, clock_phase(1.1) if t is None else t)


def sweep_bar(phase: int, width: int = 58, heads: int | None = None) -> Text:
    """A radar sweep: bright heads dragging fading comet tails.

    One head on the red palette, three spread evenly when tripping.
    """
    width = max(width, 10)
    if heads is None:
        heads = 3 if is_tripping() else 1
    stops = heat_stops()
    glow = role("glow")

    # Distance from each cell to the nearest head, so tails can overlap.
    spacing = width / heads
    distances = []
    for i in range(width):
        nearest = min(
            (phase + round(h * spacing) - i) % width for h in range(heads)
        )
        distances.append(nearest)

    text = Text(justify="center")
    for distance in distances:
        if distance == 0:
            text.append("◆", style=f"bold {glow}")
        elif distance < 10:
            heat = 1.0 - distance / 10
            text.append("━" if distance < 4 else "─",
                        style=ramp_color(heat, stops[1:]))
        else:
            text.append("─", style="rs.border.dim")
    return text


def ripple_rule(t: float, width: int = 58) -> Text:
    """A standing wave of half-height glyphs, scrolling its colours sideways."""
    width = max(width, 10)
    text = Text(justify="center")
    stops = heat_stops()
    for i in range(width):
        wave = math.sin(i * 0.32 + t * 2.0) * 0.5 + 0.5
        glyph = RIPPLE[min(int(wave * len(RIPPLE)), len(RIPPLE) - 1)]
        text.append(glyph, style=f"bold {cycle_color(i / 24.0 - t * 0.25, stops)}")
    return text


def hero(console: Console, phase: int = 0, version: str = "") -> RenderableType:
    """Full banner panel: logo, radar sweep, tagline."""
    tripping = is_tripping()
    width = min(console.width - 8, 58)
    now = clock_phase(1.0)

    if tripping:
        tag: RenderableType = Align.center(flowing_text(TAGLINE, offset=now * 0.5))
        subtitle_text = TRIP_SUBTITLE
    else:
        tag = Text(TAGLINE, justify="center", style=f"bold {role('bright')}")
        subtitle_text = SUBTITLE

    pieces: list[RenderableType] = [
        logo_text(console),
        Text(""),
        sweep_bar(phase, width),
    ]
    if tripping:
        pieces.append(ripple_rule(now, width))
    pieces.extend([
        Text(""),
        tag,
        Text(subtitle_text, justify="center", style="rs.muted"),
    ])

    # When tripping, the border breathes along the palette instead of sitting
    # on one colour.
    border = f"bold {cycle_color(pulse(0.25))}" if tripping else "rs.border"
    return Panel(
        Align.center(Group(*pieces)),
        border_style=border,
        padding=(1, 2),
        title=(flowing_text("◢ RedScan ◣", offset=now * 0.8) if tripping
               else Text("◢ RedScan ◣", style="rs.title")),
        subtitle=Text(f"v{version}", style="rs.muted") if version else None,
    )
