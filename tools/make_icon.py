"""Generate assets/redscan.ico - a red radar sweep, drawn with pure Python.

No image libraries: the icon is rendered once at 4x supersample and box-filtered
down into every size Windows asks for, then written as a multi-image .ico.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER = 512          # master render is 512x512, downsampled per size
SWEEP_DEGREES = 70.0  # width of the glowing radar wedge

BACKDROP_IN = (0x3A, 0x06, 0x0C)
BACKDROP_OUT = (0x12, 0x02, 0x04)
BLOOD = (0x8B, 0x00, 0x00)
CRIMSON = (0xC8, 0x1E, 0x3A)
RED = (0xE8, 0x10, 0x2B)
EMBER = (0xFF, 0x3B, 0x2E)
FLARE = (0xFF, 0x6B, 0x4A)
GLOW = (0xFF, 0xD6, 0xC8)

RINGS = (0.34, 0.60, 0.84)
BLIPS = ((0.46, 18.0), (0.70, 52.0), (0.26, 150.0), (0.78, 212.0), (0.55, 300.0))


def _mix(bottom: tuple[int, int, int], top: tuple[int, int, int],
         alpha: float) -> tuple[int, int, int]:
    alpha = min(max(alpha, 0.0), 1.0)
    return tuple(round(b + (t - b) * alpha) for b, t in zip(bottom, top))


def _sample(x: float, y: float) -> tuple[int, int, int, int]:
    """Colour one point of the icon. x and y run -1..1 from the centre."""
    radius = math.hypot(x, y)
    if radius > 1.0:
        return (0, 0, 0, 0)

    alpha = 255
    if radius > 0.985:                      # feather the outer edge
        alpha = round(255 * (1.0 - (radius - 0.985) / 0.015))

    colour = _mix(BACKDROP_IN, BACKDROP_OUT, radius)

    # Radar sweep: a wedge trailing counter-clockwise from due north.
    angle = (math.degrees(math.atan2(y, x)) + 90.0) % 360.0
    if angle <= SWEEP_DEGREES and radius < 0.93:
        intensity = (1.0 - angle / SWEEP_DEGREES) ** 1.6
        colour = _mix(colour, _mix(EMBER, FLARE, intensity), 0.18 + 0.72 * intensity)

    # Concentric range rings.
    for ring in RINGS:
        if abs(radius - ring) < 0.016:
            edge = 1.0 - abs(radius - ring) / 0.016
            colour = _mix(colour, CRIMSON, 0.85 * edge)

    # Crosshair.
    if radius < 0.90:
        for offset in (abs(x), abs(y)):
            if offset < 0.014:
                colour = _mix(colour, BLOOD, 0.9 * (1.0 - offset / 0.014))

    # Contacts on the scope.
    for blip_r, blip_deg in BLIPS:
        bx = blip_r * math.cos(math.radians(blip_deg - 90.0))
        by = blip_r * math.sin(math.radians(blip_deg - 90.0))
        distance = math.hypot(x - bx, y - by)
        if distance < 0.055:
            colour = _mix(colour, GLOW, (1.0 - distance / 0.055) ** 0.7)

    # Bright rim and hot centre.
    if radius > 0.90:
        colour = _mix(colour, RED, min((radius - 0.90) / 0.05, 1.0))
    if radius < 0.045:
        colour = _mix(colour, GLOW, 1.0 - radius / 0.045)

    return (colour[0], colour[1], colour[2], alpha)


def render_master(size: int = MASTER) -> list[list[tuple[int, int, int, int]]]:
    rows = []
    for py in range(size):
        y = (py + 0.5) / size * 2.0 - 1.0
        row = []
        for px in range(size):
            x = (px + 0.5) / size * 2.0 - 1.0
            row.append(_sample(x, y))
        rows.append(row)
    return rows


def downsample(master: list[list[tuple[int, int, int, int]]],
               size: int) -> list[list[tuple[int, int, int, int]]]:
    """Box filter the master render down to ``size`` - this is the antialiasing."""
    source = len(master)
    step = source / size
    out = []
    for py in range(size):
        y0, y1 = int(py * step), max(int((py + 1) * step), int(py * step) + 1)
        row = []
        for px in range(size):
            x0, x1 = int(px * step), max(int((px + 1) * step), int(px * step) + 1)
            r = g = b = a = 0
            count = 0
            for sy in range(y0, y1):
                line = master[sy]
                for sx in range(x0, x1):
                    pr, pg, pb, pa = line[sx]
                    # Weight colour by alpha so transparent pixels stay clean.
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
                    count += 1
            if a:
                row.append((r // a, g // a, b // a, a // count))
            else:
                row.append((0, 0, 0, 0))
        out.append(row)
    return out


def bmp_payload(image: list[list[tuple[int, int, int, int]]]) -> bytes:
    """A 32bpp BITMAPINFOHEADER DIB, bottom-up, with the AND mask appended."""
    size = len(image)
    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0,
    )
    pixels = bytearray()
    for row in reversed(image):
        for r, g, b, a in row:
            pixels += bytes((b, g, r, a))
    mask_stride = ((size + 31) // 32) * 4
    mask = bytes(mask_stride * size)
    return header + bytes(pixels) + mask


def write_ico(path: Path, images: list[list[list[tuple[int, int, int, int]]]]) -> None:
    payloads = [bmp_payload(img) for img in images]
    offset = 6 + 16 * len(payloads)
    out = bytearray(struct.pack("<HHH", 0, 1, len(payloads)))
    for img, payload in zip(images, payloads):
        size = len(img)
        out += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size, 0 if size >= 256 else size,
            0, 0, 1, 32, len(payload), offset,
        )
        offset += len(payload)
    for payload in payloads:
        out += payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def main() -> None:
    target = Path(__file__).resolve().parent.parent / "assets" / "redscan.ico"
    print(f"rendering {MASTER}x{MASTER} master...")
    master = render_master()
    images = []
    for size in SIZES:
        print(f"  downsampling to {size}x{size}")
        images.append(downsample(master, size))
    write_ico(target, images)
    print(f"wrote {target} ({target.stat().st_size:,} bytes, {len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
