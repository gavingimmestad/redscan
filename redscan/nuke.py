"""The offline alarm: when a host stops answering, it gets a proper send-off.

The ping monitor calls :func:`detonate` the moment a target that *was* replying
goes dark. What follows is a warhead, one white-out, a shockwave that flattens
the skyline, a mushroom cloud that climbs and rolls, and then fallout.

None of it is canned. Every frame is computed from density fields, which lets it
keep the promises the rest of RedScan keeps:

* every glyph is printable ASCII, so it draws on any terminal and any code page
* every colour comes from the active palette, so it burns red by default and
  does something considerably stranger under ``--trip``
* a frame is a pure function of time, so the sequence scales to whatever
  terminal it lands in, can be tested without one, and drops frames rather than
  running long on a slow console
"""

from __future__ import annotations

import math
import random
import time
from functools import lru_cache

from rich.align import Align
from rich.console import Console, RenderableType
from rich.live import Live
from rich.text import Text

from .theme import blend, heat_stops, ramp_color, role

# Terminal cells are about twice as tall as they are wide. The cloud is worked
# out in "row units" and multiplied back up so that it comes out round.
ASPECT = 2.0

MIN_WIDTH = 46
MIN_HEIGHT = 18
MAX_WIDTH = 132
MAX_HEIGHT = 44

FPS = 24

# The timeline, in seconds. ``tau`` below always means "seconds since impact".
DROP = 0.5          # the warhead starts falling, still above the cloud deck
IMPACT = 2.8        # ...and lands
FLASH = 0.6         # the white-out fades over this long, once: it never strobes
SHOCK = 1.25        # the shockwave takes this long to leave the frame
CLIMB = 3.4         # the cloud reaches full height
DISPERSE = 3.5      # the cloud starts to come apart
REVEAL = 3.9        # the OFFLINE banner starts burning in
DURATION = IMPACT + 6.8

SIGN_OFF = "several packets were harmed in the making of this outage"

OFFLINE_ART = (
    r"  ___  _____ _____ _     ___ _   _ _____ ",
    r" / _ \|  ___|  ___| |   |_ _| \ | | ____|",
    r"| | | | |_  | |_  | |    | ||  \| |  _|  ",
    r"| |_| |  _| |  _| | |___ | || |\  | |___ ",
    r" \___/|_|   |_|   |_____|___|_| \_|_____|",
)

WARHEAD = (
    r"\_|_/",
    r" ||| ",
    r" /#\ ",
    r"|###|",
    r"|###|",
    r"|###|",
    r" \#/ ",
    r"  V  ",
)

SMALL_WARHEAD = (
    r"\|/",
    r"|#|",
    r"|#|",
    r" V ",
)

HOST = (
    r".-----.",
    r"|=== *|",
    r"|=== *|",
    r"'-----'",
)

_DENSITY = ".,:;=+*#%@"
_LEVELS = 24
_GLOW_LEVELS = 22
_WHITE = "#FFFFFF"
_BLACK = "#000000"

_NOISE_SIZE = 128
_NOISE_MASK = _NOISE_SIZE - 1


def _unit(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _ease_out(value: float) -> float:
    value = _unit(value)
    return 1.0 - (1.0 - value) ** 3


def _ease_in_out(value: float) -> float:
    value = _unit(value)
    return value * value * (3.0 - 2.0 * value)


def _temperature(tau: float) -> float:
    """How hot the whole cloud is, from white-hot at impact down to old smoke."""
    curve = ((0.0, 1.0), (1.0, 0.92), (2.2, 0.70), (3.6, 0.42), (5.0, 0.24), (7.0, 0.12))
    for (t0, v0), (t1, v1) in zip(curve, curve[1:]):
        if tau <= t1:
            return v0 + (v1 - v0) * _unit((tau - t0) / (t1 - t0))
    return curve[-1][1]


@lru_cache(maxsize=1)
def _noise_table() -> tuple[tuple[float, ...], ...]:
    """A tiling sheet of fractal value noise, built once and then only looked up.

    Interpolating real noise per cell per frame is far too slow in pure Python,
    and a terminal cell is coarse enough that a nearest-neighbour lookup into a
    smooth sheet is indistinguishable from it.
    """
    rng = random.Random(0x4E554B45)
    size = _NOISE_SIZE
    field = [[0.0] * size for _ in range(size)]
    amplitude = 1.0
    for cells in (8, 16, 32):
        lattice = [[rng.random() for _ in range(cells)] for _ in range(cells)]
        step = size / cells
        blends = []
        for i in range(size):
            position = i / step
            whole = int(position)
            part = position - whole
            blends.append((whole % cells, (whole + 1) % cells, part * part * (3 - 2 * part)))
        for y, (y0, y1, fy) in enumerate(blends):
            upper, lower, out = lattice[y0], lattice[y1], field[y]
            for x, (x0, x1, fx) in enumerate(blends):
                top = upper[x0] + (upper[x1] - upper[x0]) * fx
                bottom = lower[x0] + (lower[x1] - lower[x0]) * fx
                out[x] += (top + (bottom - top) * fy) * amplitude
        amplitude *= 0.55
    low = min(map(min, field))
    span = (max(map(max, field)) - low) or 1.0
    return tuple(tuple((v - low) / span for v in row) for row in field)


class _Canvas:
    """A grid of glyphs, each with a heat (its colour) and a glow (the sky behind)."""

    __slots__ = ("width", "height", "glyphs", "heat", "glow")

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.glyphs = [[" "] * width for _ in range(height)]
        self.heat = [[0] * width for _ in range(height)]
        self.glow = [[0] * width for _ in range(height)]

    def put(self, x: int, y: int, glyph: str, heat: float) -> None:
        if 0 <= y < self.height and 0 <= x < self.width:
            self.glyphs[y][x] = glyph
            self.heat[y][x] = int(_unit(heat) * (_LEVELS - 1))

    def write(self, x: int, y: int, message: str, heat: float) -> None:
        """Stamp a string, leaving the cells under its own spaces alone."""
        for offset, glyph in enumerate(message):
            if glyph != " ":
                self.put(x + offset, y, glyph, heat)


class Detonation:
    """One sized, seeded explosion. :meth:`render` draws it at any moment."""

    def __init__(self, width: int, height: int, label: str, glow: bool = True) -> None:
        self.width = max(width, 1)
        self.height = max(height, 1)
        self.label = label
        self.glow = glow
        self.ground = self.height - 5
        self.centre = self.width // 2
        # The largest cloud that fits both ways: full height is one scale, the
        # cap's full width is about 2.5 scales of columns.
        self.scale = max(min(self.ground - 1, self.width / (ASPECT * 1.32)), 4.0)
        self.reach = (self.width / 2 + 4) / ASPECT
        self.noise_rate = 64.0 / self.scale
        self.warhead = WARHEAD if self.scale >= 14 else SMALL_WARHEAD

        rng = random.Random(7)
        self.stars = [
            (rng.randrange(self.width), rng.randrange(max(self.ground - 4, 1)),
             rng.choice(".'.`*"), rng.uniform(0.04, 0.26))
            for _ in range(self.width * self.height // 45)
        ]
        self.skyline = self._build_skyline(rng)
        self.scorch = [rng.choice("_.,-~_._") for _ in range(self.width)]
        self.debris = [
            (rng.uniform(0.12, math.pi - 0.12), rng.uniform(0.35, 1.25),
             rng.uniform(0.7, 2.3), rng.choice("*+'.,`^~"))
            for _ in range(max(self.width // 2, 30))
        ]
        self.ash = [
            (rng.randrange(self.width), rng.uniform(1.4, 5.8), rng.uniform(1.6, 4.4),
             rng.uniform(0.0, math.tau), rng.choice(".,'`.:"))
            for _ in range(self.width * 3 // 4)
        ]

    # --- The set ---------------------------------------------------------------

    def _build_skyline(self, rng: random.Random) -> list[tuple[int, int, str]]:
        """Little buildings either side of ground zero, as (x, row, glyph) cells."""
        cells: list[tuple[int, int, str]] = []
        clearance = len(HOST[0]) // 2 + 3
        tallest = max(min(int(self.scale // 5), 5), 2)
        for direction in (-1, 1):
            distance = clearance + rng.randrange(0, 3)
            while distance < self.width // 2:
                wide = rng.randrange(4, 9)
                tall = rng.randrange(1, tallest + 1)
                left = (self.centre + distance if direction > 0
                        else self.centre - distance - wide)
                for row in range(tall):
                    y = self.ground - 1 - row
                    for col in range(wide):
                        edge = col in (0, wide - 1)
                        if row == tall - 1:
                            glyph = "." if edge else "-"
                        elif edge:
                            glyph = "|"
                        else:
                            glyph = rng.choice(":.: ")
                        cells.append((left + col, y, glyph))
                distance += wide + rng.randrange(0, 4)
        return cells

    def _paint_sky(self, canvas: _Canvas, tau: float) -> None:
        for x, y, glyph, heat in self.stars:
            canvas.put(x, y, glyph, heat)
        if not self.glow or tau < 0:
            return
        strength = _temperature(tau) * 1.05 * (1.0 - _unit((tau - 3.2) / 2.2))
        if strength <= 0.0:
            return
        scale = self.scale
        centre_y = self.ground - scale * (0.05 + 0.62 * _ease_in_out(tau / CLIMB))
        radius = scale * (0.9 + 1.0 * _ease_out(tau / 2.5))
        limit = radius * radius
        cx = self.centre
        for y in range(self.height):
            dy = y - centre_y
            row = canvas.glow[y]
            for x in range(self.width):
                dx = (x - cx) / ASPECT
                reach = (dx * dx + dy * dy) / limit
                if reach < 1.0:
                    fall = 1.0 - reach
                    row[x] = int(_unit(strength * fall * fall) * (_GLOW_LEVELS - 1) + 0.5)

    def _shock_front(self, tau: float) -> float:
        if tau < 0:
            return -1.0
        return self.reach * (1.0 - (1.0 - _unit(tau / SHOCK)) ** 2.4)

    def _paint_ground(self, canvas: _Canvas, tau: float) -> None:
        front = self._shock_front(tau)
        ground, cx = self.ground, self.centre
        temperature = _temperature(tau) if tau >= 0 else 0.0

        for x, y, glyph in self.skyline:
            if abs(x - cx) / ASPECT > front:
                canvas.put(x, y, glyph, 0.18)

        crater = 0
        if tau > 0.15:
            crater = max(int(self.scale * 0.42 * _ease_out((tau - 0.15) / 0.8)), 1)
        for x in range(self.width):
            distance = abs(x - cx)
            if crater and distance < crater:
                continue
            if distance / ASPECT <= front:
                near = 1.0 - _unit(distance / (self.scale * 1.6))
                canvas.put(x, ground, self.scorch[x], 0.12 + near * temperature * 0.75)
            else:
                canvas.put(x, ground, "_", 0.16)

        if crater:
            ember = 0.2 + temperature * 0.7
            canvas.put(cx - crater, ground, "\\", ember)
            canvas.put(cx + crater, ground, "/", ember)
            canvas.put(cx - crater + 1, ground + 1, "\\", ember)
            canvas.put(cx + crater - 1, ground + 1, "/", ember)
            for x in range(cx - crater + 2, cx + crater - 1):
                canvas.put(x, ground + 1, self.scorch[x % self.width], ember * 0.8)

    # --- Before ----------------------------------------------------------------

    def _paint_target(self, canvas: _Canvas, t: float) -> None:
        tall = len(HOST)
        left = self.centre - len(HOST[0]) // 2
        for row, line in enumerate(HOST):
            canvas.write(left, self.ground - tall + row, line, 0.45)
        tag = f"[ {self.label} ]"
        beat = 0.5 + 0.5 * math.sin(t * 9.0)
        for offset, glyph in enumerate(tag):
            canvas.put(self.centre - len(tag) // 2 + offset, self.ground - tall - 2, glyph,
                       0.55 + 0.4 * beat)

    def warhead_row(self, t: float) -> int:
        """The row of the warhead's tail: above the frame until it drops, then falling."""
        rows = len(self.warhead)
        landing = self.ground - len(HOST) - rows
        return int(-rows + (landing + rows) * _unit((t - DROP) / (IMPACT - DROP)) ** 1.7)

    def _paint_warhead(self, canvas: _Canvas, t: float) -> int:
        """The warhead and the trail it drags down the sky. Returns its tail row."""
        y = self.warhead_row(t)
        rows = len(self.warhead)
        for trail in range(0, y):
            canvas.put(self.centre, trail, ":" if trail % 2 else ".",
                       0.14 + 0.3 * trail / max(y, 1))
        # Speed lines either side, for as long as it is moving fast enough to earn them.
        wide = len(self.warhead[0]) // 2 + 2
        for side in (-wide, wide):
            for row in range(y - 3, y + rows - 1):
                if row >= 0 and (row + y) % 3:
                    canvas.put(self.centre + side, row, "'", 0.24)
        left = self.centre - len(self.warhead[0]) // 2
        for row, line in enumerate(self.warhead):
            canvas.write(left, y + row, line, 0.92)
        return y

    def _paint_overcast(self, canvas: _Canvas, t: float, tau: float, tail: int = -1) -> None:
        """The cloud deck the warhead drops out of, until the shockwave clears it.

        Painted over the warhead, so it stays hidden until it actually falls out
        of the bottom. Only the trail shows through, in the hole it punched.
        """
        table = _noise_table()
        mask = _NOISE_MASK
        deck = max(self.scale * 0.24, 2.0)
        drift = t * 1.6
        # The blast clears it from the middle out, and it is gone by the time the
        # shockwave leaves the frame, corners included.
        sweep = (self._shock_front(tau) / self.reach * 1.5) ** 2 if tau >= 0 else -1.0
        cx, ground, reach = self.centre, self.ground, self.reach
        for x in range(self.width):
            sample = int((x + drift) * 1.3) & mask
            depth = deck * (0.3 + 1.15 * table[5][sample])
            across = (x - cx) / ASPECT / reach
            for y in range(int(depth) + 1):
                if x == cx and y < tail:
                    continue
                if tau >= 0:
                    up = (ground - y) / (ground + 1)
                    if across * across + up * up <= sweep:
                        continue
                n = table[int(y * 5.0) & mask][sample]
                dens = 0.62 * (1.0 - y / (depth + 1.0)) + (n - 0.5) * 1.5
                glyph = _DENSITY[int(_unit(dens) * 6)]
                canvas.put(x, y, glyph, 0.05 + 0.3 * n * n)

    def _paint_prelude(self, canvas: _Canvas, t: float) -> None:
        self._paint_target(canvas, t)
        self._paint_overcast(canvas, t, -1.0, self._paint_warhead(canvas, t))
        line = f"> no reply from {self.label}. no reply. no reply. retaliating."
        shown = int(len(line) * _unit(t / (IMPACT * 0.8)))
        visible = line[:shown][:self.width - 2]
        canvas.write(1, self.height - 2, visible, 0.72)
        canvas.put(1 + len(visible), self.height - 2, "_" if int(t * 8) % 2 else " ", 0.9)

    # --- The cloud -------------------------------------------------------------

    def _erosion(self, tau: float) -> float:
        return _unit((tau - DISPERSE) / (DURATION - IMPACT - DISPERSE)) ** 1.5

    def _wind(self, tau: float) -> float:
        over = max(tau - DISPERSE * 0.75, 0.0)
        return over * over * self.scale * 0.03

    def _paint_base(self, canvas: _Canvas, tau: float) -> None:
        """The surge of dust that runs out along the ground."""
        table = _noise_table()
        scale, ground, cx = self.scale, self.ground, self.centre
        rate = self.noise_rate
        spread = min(scale * (0.1 + 0.95 * _ease_out(tau / 3.0)), self.reach)
        tall = scale * (0.07 + 0.14 * _ease_out(tau / 1.4))
        temperature = _temperature(tau)
        gone = self._erosion(tau)
        limit = 1.0 - gone * 0.95
        mask = _NOISE_MASK
        for y in range(max(int(ground - tall * 2.0), 0), ground):
            rise = ground - y
            for x in range(self.width):
                dx = (x - cx) / ASPECT
                ax = abs(dx)
                side = 0 if dx > 0 else 41
                # Each stretch of the wall has its own height, rolling outward.
                lump = table[side][int((ax - tau * 2.2) * rate * 0.6) & mask]
                local = tall * (0.45 + 1.25 * lump)
                q = (ax / spread) ** 2 + (rise / local) ** 2
                if q > 2.4:
                    continue
                n = table[int((rise * 1.3 + side) * rate) & mask][
                    int((ax - tau * 2.6) * rate) & mask]
                q += (n - 0.5) * (1.0 + gone)
                if q >= limit:
                    continue
                dens = 0.72 - q * 0.6 + (n - 0.5) * 1.5 - gone * 0.25
                glyph = _DENSITY[int(_unit(dens) * (len(_DENSITY) - 1))]
                canvas.put(x, y, glyph, temperature * (0.2 + 0.65 * (1 - q)) * (0.55 + n))

    def _paint_stem(self, canvas: _Canvas, tau: float, height: float) -> None:
        """The column of fire and dirt feeding the cap, climbing as you watch."""
        if height < 1.5:
            return
        table = _noise_table()
        scale, ground, cx = self.scale, self.ground, self.centre
        rate = self.noise_rate
        temperature = _temperature(tau)
        gone = self._erosion(tau)
        limit = 1.0 - gone * 0.98
        wind = self._wind(tau)
        grown = _ease_out(tau / 1.1)
        mask = _NOISE_MASK
        for y in range(max(int(ground - height), 0), ground):
            rise = ground - y
            u = rise / height
            half = grown * scale * (0.06 + 0.17 * (1 - u) ** 3 + 0.1 * u ** 4)
            if half <= 0.2:
                continue
            sway = (table[int(rise * rate * 0.5) & mask][int(tau * 7) & mask] - 0.5) * scale * 0.09
            middle = cx + (sway + wind * u * 0.6) * ASPECT
            span = int(half * 1.6 * ASPECT) + 1
            for x in range(max(int(middle) - span, 0), min(int(middle) + span, self.width - 1) + 1):
                dx = (x - middle) / ASPECT
                q = (dx / half) ** 2
                n = table[int((rise - tau * 7.5) * rate) & mask][int(dx * rate) & mask]
                q += (n - 0.5) * (1.3 + gone * 1.2)
                if q >= limit:
                    continue
                dens = 0.82 - q * 0.62 + (n - 0.5) * 1.7 - gone * 0.3
                glyph = _DENSITY[int(_unit(dens) * (len(_DENSITY) - 1))]
                canvas.put(x, y, glyph,
                           temperature * (0.3 + 0.9 * (1 - q) + 0.25 * (1 - u)) * (0.45 + n))

    def _paint_skirt(self, canvas: _Canvas, tau: float, height: float) -> None:
        """The condensation ring that hangs around the stem for a moment."""
        life = (tau - 1.3) / 2.5
        if not 0.0 <= life <= 1.0:
            return
        table = _noise_table()
        radius = self.scale * (0.12 + 0.3 * _ease_out(life))
        level = self.ground - height * 0.46
        rise = math.sin(life * math.pi)
        steps = int(radius * ASPECT * 4) + 8
        cx = self.centre
        for i in range(steps):
            angle = math.tau * i / steps
            n = table[int(angle * 10) & _NOISE_MASK][int(tau * 5) & _NOISE_MASK]
            if n < 0.2 + 0.55 * life or math.sin(angle) < 0:
                continue
            x = int(cx + math.cos(angle) * radius * ASPECT)
            y = int(level + math.sin(angle) * radius * 0.2)
            canvas.put(x, y, "~", 0.15 + 0.55 * rise)

    def _paint_cap(self, canvas: _Canvas, tau: float) -> None:
        """The head: a fireball that lifts off, widens and rolls under itself."""
        scale = self.scale
        burst = _ease_out(tau / 0.75)
        climb = _ease_in_out(tau / CLIMB)
        height = scale * (0.03 + 0.69 * climb)
        rx = scale * (0.30 * burst + 0.32 * climb)
        ry_top = scale * (0.31 * burst - 0.07 * climb)
        ry_low = scale * (0.27 * burst - 0.13 * climb)
        if rx < 0.5 or ry_top < 0.4 or ry_low < 0.3:
            return

        table = _noise_table()
        ground, cx = self.ground, self.centre
        rate = self.noise_rate * (1.35 - 0.4 * climb)
        temperature = _temperature(tau)
        gone = self._erosion(tau)
        limit = 1.0 - gone * 0.99
        turbulence = 0.8 + gone * 1.25
        roll = tau * 0.85
        light = 2.0
        mask = _NOISE_MASK
        put = canvas.put
        ramp_top = len(_DENSITY) - 1

        shift = self._wind(tau)
        top = int(ground - (height + ry_top * 1.5))
        bottom = int(ground - (height - ry_low * 1.5))
        reach = int(rx * 1.5 * ASPECT) + 1
        middle = int(cx + shift * ASPECT)
        for y in range(max(top, 0), min(bottom, ground - 1) + 1):
            rise = ground - y
            h = rise - height
            v = h / (ry_top if h >= 0 else ry_low)
            for x in range(max(middle - reach, 0), min(middle + reach, self.width - 1) + 1):
                dx = (x - cx) / ASPECT - shift
                ax = abs(dx)
                u = ax / rx
                sag = v + 0.38 * u * u
                q = u * u + sag * sag
                if q > 2.3:
                    continue
                side = 0 if dx >= 0 else 37
                nx = (ax - roll * v) * rate + side
                ny = (h - roll * (0.55 - u)) * rate + side
                n = table[int(ny) & mask][int(nx) & mask]
                q += (n - 0.5) * turbulence
                if q >= limit:
                    continue
                lit = n - table[int(ny + light) & mask][int(nx - light) & mask]
                dens = 1.12 - q * 0.82 + (n - 0.5) * 0.85 + lit * 2.2 - gone * 0.4
                glyph = _DENSITY[int(_unit(dens) * ramp_top)]
                heat = temperature * (0.24 + 0.92 * (1 - q) + 0.3 * max(-v, 0.0)) * (0.5 + n)
                put(x, y, glyph, heat)

    def _paint_shock(self, canvas: _Canvas, tau: float) -> None:
        """The blast front, drawn as a dome of glyphs following its own curve."""
        for delay, weight, thin in ((0.0, 1.0, False), (0.16, 0.5, True)):
            age = tau - delay
            if not 0.0 <= age < SHOCK:
                continue
            front = self._shock_front(age)
            heat = weight * (1.0 - age / SHOCK) ** 0.7
            steps = int(front * 5) + 10
            for i in range(steps + 1):
                angle = math.pi * i / steps
                x = int(self.centre + math.cos(angle) * front * ASPECT)
                y = int(self.ground - math.sin(angle) * front)
                if y >= self.ground:
                    continue
                if thin:
                    glyph = "."
                elif angle < 0.42:
                    glyph = ")"
                elif angle < 1.1:
                    glyph = "\\"
                elif angle < math.pi - 1.1:
                    glyph = "-"
                elif angle < math.pi - 0.42:
                    glyph = "/"
                else:
                    glyph = "("
                canvas.put(x, y, glyph, heat)

    def _paint_debris(self, canvas: _Canvas, tau: float) -> None:
        reach = self.scale * 1.5
        fall = self.scale * 1.15
        for angle, speed, life, glyph in self.debris:
            if tau >= life:
                continue
            x = self.centre + math.cos(angle) * speed * reach * tau * ASPECT
            y = self.ground - (math.sin(angle) * speed * reach * tau - fall * tau * tau)
            if y < self.ground:
                canvas.put(int(x), int(y), glyph, 0.95 * (1.0 - tau / life))

    def _paint_ash(self, canvas: _Canvas, tau: float) -> None:
        for x, start, speed, sway, glyph in self.ash:
            age = tau - start
            if age < 0:
                continue
            y = age * speed
            if y < self.ground:
                drift = math.sin(age * 2.0 + sway) * 1.6 + self._wind(tau) * 0.15
                canvas.put(int(x + drift), int(y), glyph, 0.34)

    # --- After -----------------------------------------------------------------

    def _paint_verdict(self, canvas: _Canvas, tau: float) -> None:
        progress = _unit((tau - REVEAL) / 1.1)
        if progress <= 0.0:
            return
        table = _noise_table()
        left = (self.width - len(OFFLINE_ART[0])) // 2
        top = max(int(self.ground - self.scale), 0)
        for row, line in enumerate(OFFLINE_ART):
            for col, glyph in enumerate(line):
                # Burns in unevenly, then settles as it cools.
                n = table[(row * 9) & _NOISE_MASK][(col * 5) & _NOISE_MASK]
                if progress * 1.15 < n:
                    continue
                x, y = left + col, top + row
                if glyph == " ":
                    canvas.put(x, y, " ", 0.0)
                    continue
                age = progress * 1.15 - n
                canvas.put(x, y, glyph, 1.0 - 0.32 * _unit(age * 2.0))

        line = f"{self.label} HAS LEFT THE NETWORK"[:self.width - 2]
        shown = int(len(line) * _unit((tau - REVEAL - 0.3) / 0.9))
        canvas.write((self.width - len(line)) // 2, self.height - 2, line[:shown], 0.8)
        if tau > REVEAL + 1.2 and len(SIGN_OFF) < self.width:
            shown = int(len(SIGN_OFF) * _unit((tau - REVEAL - 1.2) / 0.8))
            canvas.write((self.width - len(SIGN_OFF)) // 2, self.height - 1, SIGN_OFF[:shown], 0.3)

    # --- Compositing -----------------------------------------------------------

    def flash(self, t: float) -> float:
        """How white the frame is right now: 1 at impact, fading to 0 exactly once."""
        tau = t - IMPACT
        if not 0.0 <= tau < FLASH:
            return 0.0
        return (1.0 - tau / FLASH) ** 1.6

    def _shake(self, tau: float) -> tuple[int, int]:
        """The whole frame jolts at impact and settles over the next second."""
        if not 0.0 <= tau < 1.0:
            return 0, 0
        strength = (1.0 - tau) ** 2
        return (round(math.sin(tau * 71.0) * 3.0 * strength),
                round(math.sin(tau * 53.0 + 1.3) * 1.6 * strength))

    def backdrop(self, t: float) -> str:
        """The sky colour outside the scene, so the flash can fill the terminal."""
        return blend(blend(_BLACK, role("void"), 0.22), _WHITE, self.flash(t))

    def render(self, t: float) -> Text:
        """The whole scene at ``t`` seconds in, exactly ``width`` by ``height``."""
        canvas = _Canvas(self.width, self.height)
        tau = t - IMPACT
        self._paint_sky(canvas, tau)
        self._paint_ground(canvas, tau)
        if tau < 0:
            self._paint_prelude(canvas, t)
        else:
            height = self.scale * (0.03 + 0.69 * _ease_in_out(tau / CLIMB))
            self._paint_overcast(canvas, t, tau)
            self._paint_base(canvas, tau)
            self._paint_skirt(canvas, tau, height)
            self._paint_stem(canvas, tau, height)
            self._paint_cap(canvas, tau)
            self._paint_shock(canvas, tau)
            self._paint_debris(canvas, tau)
            self._paint_ash(canvas, tau)
            self._paint_verdict(canvas, tau)
        return self._compose(canvas, t, tau)

    def _compose(self, canvas: _Canvas, t: float, tau: float) -> Text:
        """Flatten the canvas to text, one styled run per stretch of alike cells."""
        flash = self.flash(t)
        stops = [*heat_stops(), _WHITE]
        foreground = [ramp_color(i / (_LEVELS - 1), stops) for i in range(_LEVELS)]
        sky = blend(_BLACK, role("void"), 0.22)
        fire = role("core")
        background = [
            blend(sky, fire, 0.46 * (i / (_GLOW_LEVELS - 1)) ** 1.9)
            for i in range(_GLOW_LEVELS)
        ]
        styles: dict[tuple[int, int], str] = {}

        def style_of(heat: int, glow: int) -> str:
            key = (heat, glow)
            style = styles.get(key)
            if style is None:
                ink = foreground[heat]
                if flash:
                    ink = blend(ink, _WHITE, flash)
                weight = "bold " if heat > _LEVELS * 0.4 else ""
                if self.glow:
                    paper = background[glow]
                    if flash:
                        paper = blend(paper, _WHITE, flash)
                    style = f"{weight}{ink} on {paper}"
                else:
                    style = f"{weight}{ink}"
                styles[key] = style
            return style

        offset_x, offset_y = self._shake(tau)
        table = _noise_table()
        width, height = canvas.width, canvas.height
        text = Text(no_wrap=True, end="")
        for y in range(height):
            source = y - offset_y
            if 0 <= source < height:
                glyphs, heats, glows = canvas.glyphs[source], canvas.heat[source], canvas.glow[source]
            else:
                glyphs, heats, glows = [" "] * width, [0] * width, [0] * width

            run: list[str] = []
            run_heat = run_glow = 0
            run_inked = False
            for x in range(width):
                cell = x - offset_x
                if 0 <= cell < width:
                    glyph, heat, glow = glyphs[cell], heats[cell], glows[cell]
                else:
                    glyph, heat, glow = " ", 0, 0
                if flash > 0.55 and glyph == " ":
                    # Without colour, the white-out still has to read as one.
                    if table[y & _NOISE_MASK][x & _NOISE_MASK] < flash * 1.1:
                        glyph, heat = "#", _LEVELS - 1
                inked = glyph != " "
                if run and glow == run_glow and (not inked or not run_inked or heat == run_heat):
                    run.append(glyph)
                    if inked and not run_inked:
                        run_heat, run_inked = heat, True
                    continue
                if run:
                    text.append("".join(run), style_of(run_heat, run_glow))
                run = [glyph]
                run_heat, run_glow, run_inked = heat, glow, inked
            if run:
                text.append("".join(run), style_of(run_heat, run_glow))
            if y < height - 1:
                text.append("\n")
        return text


def can_play(console: Console) -> bool:
    """Whether this console can show the sequence at all."""
    return (console.is_terminal and not console.is_dumb_terminal
            and console.width >= MIN_WIDTH and console.height >= MIN_HEIGHT)


def _fit(console: Console) -> tuple[int, int]:
    """The scene size for this console. A console with no alternate screen would
    scroll on a frame as tall as it is, so it gets one row less."""
    rows = console.height - 1 if console.legacy_windows else console.height
    return min(console.width, MAX_WIDTH), min(rows, MAX_HEIGHT)


def _scene_for(console: Console, label: str) -> Detonation:
    width, height = _fit(console)
    return Detonation(width, height, label,
                      glow=console.color_system in {"256", "truecolor"})


def scene_frame(scene: Detonation, t: float) -> RenderableType:
    """A scene frame centred in the terminal, with the sky filling the margins."""
    return Align(scene.render(t), "center", vertical="middle",
                 style=f"on {scene.backdrop(t)}" if scene.glow else None)


def detonate(console: Console, label: str) -> None:
    """Play the whole sequence on the alternate screen, then give the terminal back.

    Time-driven rather than frame-counted: a console that cannot keep up sees
    fewer frames, not a longer outage. Redrawing in place on the alternate screen
    also means no scrollback is touched and nothing flickers.
    """
    scene = _scene_for(console, label)
    with Live(console=console, screen=True, auto_refresh=False) as live:
        started = time.perf_counter()
        while True:
            t = time.perf_counter() - started
            if t >= DURATION:
                break
            if _fit(console) != (scene.width, scene.height):
                scene = _scene_for(console, label)
            live.update(scene_frame(scene, t), refresh=True)
            spare = 1.0 / FPS - (time.perf_counter() - started - t)
            if spare > 0:
                time.sleep(spare)
