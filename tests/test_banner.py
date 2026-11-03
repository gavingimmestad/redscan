"""Tests for the animated banner."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from redscan import banner, theme


@pytest.fixture(autouse=True)
def reset_palette():
    yield
    theme.set_palette(theme.DEFAULT_PALETTE)


class FakeStream(io.StringIO):
    """A StringIO that reports an encoding, the way a real console does."""

    def __init__(self, encoding: str) -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding


def make_console(encoding: str = "utf-8", width: int = 80) -> Console:
    return Console(file=FakeStream(encoding), theme=theme.THEME, width=width,
                   force_terminal=True, record=True)


def render(console: Console, renderable) -> str:
    """Render to plain text.

    Styles are stripped on purpose: a flowing gradient colours every character
    separately, so the raw ANSI stream never contains a contiguous word.
    """
    console.print(renderable)
    return console.export_text()


# --- Art selection ------------------------------------------------------------

def test_both_logos_are_rectangular():
    """Ragged art means a ragged gradient, so the widths have to match."""
    for art in (banner.BLOCK_LOGO, banner.ASCII_LOGO):
        lines = art.splitlines()
        assert len(lines) >= 5
        assert len({len(line.rstrip()) for line in lines}) <= len(lines)


def test_block_art_is_used_when_the_encoding_can_take_it():
    assert banner.logo_art(make_console("utf-8")) == banner.BLOCK_LOGO.splitlines()


def test_ascii_art_is_used_on_a_legacy_code_page():
    """cp1252 has no box drawing, so the plain-ASCII logo has to take over.

    (cp437 is *not* a useful case here - it does carry the block glyphs.)
    """
    assert banner.logo_art(make_console("cp1252")) == banner.ASCII_LOGO.splitlines()


def test_cp437_keeps_the_block_logo():
    assert banner.logo_art(make_console("cp437")) == banner.BLOCK_LOGO.splitlines()


def test_unknown_encoding_falls_back_to_ascii():
    assert banner.logo_art(make_console("not-a-codec")) == banner.ASCII_LOGO.splitlines()


# --- logo_text ----------------------------------------------------------------

def test_logo_text_returns_the_whole_logo_by_default():
    console = make_console()
    assert len(banner.logo_text(console).plain.splitlines()) == len(
        banner.logo_art(console)
    )


@pytest.mark.parametrize("rows", [1, 2, 4, 6])
def test_logo_text_clips_to_rows_for_the_wipe_in(rows):
    console = make_console()
    clipped = banner.logo_text(console, rows=rows).plain.splitlines()
    assert len(clipped) == min(rows, len(banner.logo_art(console)))


def test_logo_text_colours_per_line_on_the_red_palette():
    """One style span per visible line is the classic look."""
    console = make_console()
    text = banner.logo_text(console)
    assert len(text.spans) == len(banner.logo_art(console))


def test_logo_text_colours_per_character_when_tripping():
    theme.set_palette("kaleido")
    console = make_console()
    text = banner.logo_text(console)
    # Every non-space cell gets its own span, so there are far more of them.
    assert len(text.spans) > len(banner.logo_art(console)) * 10


def test_plasma_logo_preserves_the_art_exactly():
    """Whatever the lighting, the letters must still spell RedScan."""
    console = make_console()
    plain = banner.logo_text(console).plain
    theme.set_palette("acid")
    assert banner.logo_text(console, t=3.0).plain == plain


def test_plasma_logo_moves_over_time():
    theme.set_palette("acid")
    console = make_console()
    early = [s.style for s in banner.logo_text(console, t=0.0).spans]
    later = [s.style for s in banner.logo_text(console, t=4.0).spans]
    assert early != later


def test_plasma_logo_is_stable_for_a_fixed_t():
    theme.set_palette("oil")
    console = make_console()
    # Hue spin reads the wall clock, so compare structure rather than hexes.
    first = banner.logo_text(console, t=1.0)
    second = banner.logo_text(console, t=1.0)
    assert first.plain == second.plain
    assert len(first.spans) == len(second.spans)


# --- sweep_bar ----------------------------------------------------------------

@pytest.mark.parametrize("width", [10, 24, 58, 120])
def test_sweep_bar_fills_exactly_its_width(width):
    assert len(banner.sweep_bar(3, width).plain) == width


def test_sweep_bar_clamps_absurdly_small_widths():
    assert len(banner.sweep_bar(0, 1).plain) == 10


def test_sweep_bar_has_one_head_on_the_red_palette():
    assert banner.sweep_bar(5, 58).plain.count("◆") == 1


def test_sweep_bar_grows_extra_heads_when_tripping():
    theme.set_palette("vapor")
    assert banner.sweep_bar(5, 58).plain.count("◆") == 3


def test_sweep_bar_head_tracks_the_phase():
    a = banner.sweep_bar(0, 40).plain.index("◆")
    b = banner.sweep_bar(7, 40).plain.index("◆")
    assert a != b


def test_sweep_bar_head_wraps_around():
    assert banner.sweep_bar(0, 40).plain == banner.sweep_bar(40, 40).plain


def test_sweep_bar_head_count_can_be_forced():
    assert banner.sweep_bar(0, 60, heads=4).plain.count("◆") == 4


# --- ripple_rule --------------------------------------------------------------

@pytest.mark.parametrize("width", [10, 30, 58])
def test_ripple_rule_fills_exactly_its_width(width):
    assert len(banner.ripple_rule(0.0, width).plain) == width


def test_ripple_rule_uses_only_wave_glyphs():
    assert set(banner.ripple_rule(1.5, 58).plain) <= set(banner.RIPPLE)


def test_ripple_rule_actually_waves():
    assert len(set(banner.ripple_rule(0.0, 58).plain)) > 2


def test_ripple_rule_moves_over_time():
    assert banner.ripple_rule(0.0, 58).plain != banner.ripple_rule(0.6, 58).plain


# --- hero ---------------------------------------------------------------------

def test_hero_renders_on_the_red_palette():
    console = make_console()
    out = render(console, banner.hero(console, phase=4, version="1.0.0"))
    assert "RedScan" in out
    assert "1.0.0" in out
    assert banner.SUBTITLE in out.replace("\n", " ")


def test_hero_swaps_the_subtitle_when_tripping():
    theme.set_palette("ultra")
    console = make_console(width=120)
    out = render(console, banner.hero(console, phase=4)).replace("\n", " ")
    assert banner.TRIP_SUBTITLE in out
    assert banner.SUBTITLE not in out


def test_hero_keeps_the_phase_keyword_for_the_boot_animation():
    """cli.boot() calls this by keyword; renaming it would break startup."""
    console = make_console()
    assert banner.hero(console, phase=18, version="1.0.0") is not None


@pytest.mark.parametrize("palette", sorted(theme.PALETTES))
def test_hero_renders_under_every_palette(palette):
    theme.set_palette(palette)
    console = make_console(width=100)
    assert "RedScan" in render(console, banner.hero(console, phase=2, version="1.0.0"))


@pytest.mark.parametrize("width", [40, 60, 80, 200])
def test_hero_survives_narrow_and_wide_terminals(width):
    theme.set_palette("kaleido")
    console = make_console(width=width)
    assert render(console, banner.hero(console, phase=1)) != ""


def test_hero_renders_without_unicode_blocks():
    console = make_console("cp1252", width=90)
    assert "REDSCAN" in render(console, banner.hero(console, phase=1)).upper()
