"""Render the ping monitor losing a host, and the consequences, without a terminal.

    python tools/make_demo.py --gif assets/nuke.gif      # the README animation
    python tools/make_demo.py --sheet sheet.png          # contact sheet of key frames

Pillow is the only dependency, and only this script needs it.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.console import Console, RenderableType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redscan.net import PingStats, Pong, Target  # noqa: E402
from redscan.nuke import DURATION, FLASH, IMPACT, Detonation  # noqa: E402
from redscan.theme import build_theme, set_palette  # noqa: E402
from redscan.ui import ping_panel  # noqa: E402

FONTS = ("consola.ttf", "CascadiaMono.ttf", "DejaVuSansMono.ttf", "Menlo.ttc")
BOLD_FONTS = ("consolab.ttf", "CascadiaMono.ttf", "DejaVuSansMono-Bold.ttf", "Menlo.ttc")
SYMBOL_FONTS = ("seguisym.ttf", "DejaVuSansMono.ttf", "Apple Symbols.ttf")
SYMBOLS = set("✔✖▰◤◥◢◣▸⬇▁▂▃▄▅▆▇")

# The last moments of a host: a few healthy replies, then three that never come.
PING_SCRIPT = (12.4, 11.8, 14.2, 12.9, 11.6, None, None, None)
PING_STEP = 0.42  # seconds per reply; the real monitor takes one, but nobody needs to wait


def load_font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise SystemExit("no suitable font found")


class Painter:
    """Turns anything Rich can render into an image, cell by cell."""

    def __init__(self, width: int, height: int, font_size: int = 14) -> None:
        self.width, self.height = width, height
        self.font = load_font(FONTS, font_size)
        self.bold = load_font(BOLD_FONTS, font_size)
        self.symbols = load_font(SYMBOL_FONTS, round(font_size * 0.9))
        self.cell_w = round(self.font.getlength("M"))
        ascent, descent = self.font.getmetrics()
        self.cell_h = ascent + descent + 1
        self.console = Console(file=io.StringIO(), width=width, height=height,
                               force_terminal=True, color_system="truecolor",
                               legacy_windows=False, theme=build_theme())

    def paint_renderable(self, renderable: RenderableType, backdrop: str) -> Image.Image:
        size = (self.width * self.cell_w, self.height * self.cell_h)
        image = Image.new("RGB", size, backdrop)
        draw = ImageDraw.Draw(image)
        options = self.console.options.update(width=self.width)
        lines = self.console.render_lines(renderable, options, pad=False)
        for row, segments in enumerate(lines[:self.height]):
            col = 0
            for segment in segments:
                if segment.control:
                    continue
                style = segment.style
                ink = paper = None
                if style is not None:
                    if style.color is not None:
                        ink = "#%02x%02x%02x" % tuple(style.color.get_truecolor())
                    if style.bgcolor is not None:
                        paper = "#%02x%02x%02x" % tuple(style.bgcolor.get_truecolor())
                base = self.bold if style is not None and style.bold else self.font
                for glyph in segment.text:
                    x, y = col * self.cell_w, row * self.cell_h
                    if paper:
                        draw.rectangle((x, y, x + self.cell_w - 1, y + self.cell_h - 1), fill=paper)
                    if glyph != " ":
                        draw.text((x, y), glyph, fill=ink or "#cccccc",
                                  font=self.symbols if glyph in SYMBOLS else base)
                    col += 1
        return image

    def paint(self, scene: Detonation, t: float) -> Image.Image:
        return self.paint_renderable(scene.render(t), scene.backdrop(t))


def ping_story(painter: Painter, label: str) -> list[tuple[Image.Image, float]]:
    """The dashboard reply by reply, as (image, seconds on screen)."""
    target = Target(raw=label, ip=label)
    stats, history, story = PingStats(), [], []
    backdrop = "#0c0c0c"
    for seq, latency in enumerate(PING_SCRIPT, start=1):
        pong = (Pong(seq, latency, ttl=64) if latency is not None
                else Pong(seq, None, error="no reply"))
        stats.add(pong)
        history.append(pong)
        panel = ping_panel(target, stats, history, painter.width)
        story.append((painter.paint_renderable(panel, backdrop), PING_STEP))
    return story


def detonation_frames(painter: Painter, scene: Detonation, fps: int) -> list[Image.Image]:
    return [painter.paint(scene, i / fps) for i in range(int(DURATION * fps) + 1)]


def contact_sheet(painter: Painter, scene: Detonation, moments: list[float],
                  columns: int, path: Path) -> None:
    frames = [painter.paint(scene, t) for t in moments]
    w, h = frames[0].size
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (w * columns + 8 * (columns - 1), h * rows + 8 * (rows - 1)), "#303030")
    for i, frame in enumerate(frames):
        sheet.paste(frame, ((w + 8) * (i % columns), (h + 8) * (i // columns)))
    sheet.save(path)


def gif(painter: Painter, scene: Detonation, label: str, fps: int, path: Path,
        hold_ms: int = 1800) -> None:
    story = ping_story(painter, label)
    blast = detonation_frames(painter, scene, fps)
    images = [image for image, _ in story] + blast
    durations = [round(seconds * 1000) for _, seconds in story]
    durations += [round(1000 / fps)] * len(blast)
    durations[-1] = hold_ms  # let the verdict land before it loops

    # One palette for the whole film: per-frame tables are bigger and make the sky
    # shimmer. The flash needs its own share, or it fades out in visible bands.
    flash = [frame for i, frame in enumerate(blast) if IMPACT <= i / fps < IMPACT + FLASH]
    sample = images[::max(len(images) // 14, 1)] + flash
    w, h = images[0].size
    montage = Image.new("RGB", (w * len(sample), h))
    for i, image in enumerate(sample):
        montage.paste(image, (w * i, 0))
    palette = montage.quantize(colors=224, method=Image.Quantize.MEDIANCUT,
                               dither=Image.Dither.NONE)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    frames[0].save(path, save_all=True, append_images=frames[1:], loop=0,
                   duration=durations, disposal=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=100)
    parser.add_argument("--height", type=int, default=34)
    parser.add_argument("--label", default="192.168.1.50")
    parser.add_argument("--palette", default="red")
    parser.add_argument("--font-size", type=int, default=14)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--at", help="comma-separated moments for the sheet, in seconds after impact")
    parser.add_argument("--sheet", type=Path)
    parser.add_argument("--gif", type=Path)
    args = parser.parse_args()

    set_palette(args.palette)
    scene = Detonation(args.width, args.height, args.label)
    painter = Painter(args.width, args.height, args.font_size)
    if args.sheet:
        taus = ([float(v) for v in args.at.split(",")] if args.at else
                [-2.2, -1.2, -0.3, 0.02, 0.5, 0.9, 1.5, 2.6, 3.6, 4.6, 5.6, 6.7])
        contact_sheet(painter, scene, [IMPACT + tau for tau in taus], args.columns, args.sheet)
    if args.gif:
        gif(painter, scene, args.label, args.fps, args.gif)


if __name__ == "__main__":
    main()
