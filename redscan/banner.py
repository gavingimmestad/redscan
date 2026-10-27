"""The obligatory oversized ASCII banner, in flavours of red."""

from __future__ import annotations

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from .theme import BANNER_RAMP, CRIMSON, EMBER, FLARE, GLOW, ramp_color

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


def _supports_blocks(console: Console) -> bool:
    """True when the active encoding can actually render block characters."""
    try:
        "█╗╚═".encode(console.file.encoding or "utf-8")
    except (LookupError, UnicodeEncodeError, AttributeError):
        return False
    return True


def logo_text(console: Console, rows: int | None = None) -> Text:
    """The logo with a top-to-bottom red gradient, one hue per line."""
    art = BLOCK_LOGO if _supports_blocks(console) else ASCII_LOGO
    lines = art.splitlines()
    visible = lines if rows is None else lines[:rows]
    text = Text(justify="center")
    divisor = max(len(lines) - 1, 1)
    for i, line in enumerate(visible):
        text.append(line + ("\n" if i < len(visible) - 1 else ""),
                    style=f"bold {ramp_color(i / divisor, BANNER_RAMP)}")
    return text


def sweep_bar(phase: int, width: int = 58) -> Text:
    """A radar sweep: a bright head dragging a fading red comet tail."""
    cells = ["─"] * width
    text = Text(justify="center")
    head = phase % width
    for i, cell in enumerate(cells):
        distance = (head - i) % width
        if distance == 0:
            text.append("◆", style=f"bold {GLOW}")
        elif distance < 10:
            glow = 1.0 - distance / 10
            text.append("━" if distance < 4 else cell,
                        style=ramp_color(glow, [CRIMSON, EMBER, FLARE]))
        else:
            text.append(cell, style="rs.border.dim")
    return text


def hero(console: Console, phase: int = 0, version: str = "") -> RenderableType:
    """Full banner panel: logo, radar sweep, tagline."""
    tag = Text(TAGLINE, justify="center", style=f"bold {EMBER}")
    tag.stylize(f"dim {CRIMSON}", 0, 0)
    pieces = [
        logo_text(console),
        Text(""),
        sweep_bar(phase, min(console.width - 8, 58)),
        Text(""),
        tag,
        Text(SUBTITLE, justify="center", style="rs.muted"),
    ]
    return Panel(
        Align.center(Group(*pieces)),
        border_style="rs.border",
        padding=(1, 2),
        title=Text("◢ RedScan ◣", style="rs.title"),
        subtitle=Text(f"v{version}", style="rs.muted") if version else None,
    )
