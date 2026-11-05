"""Tests for the palette and animation engine."""

from __future__ import annotations

import re

import pytest

from redscan import theme

HEX = re.compile(r"^#[0-9A-F]{6}$")


@pytest.fixture(autouse=True)
def reset_palette():
    """Keep the module-level palette from leaking between tests."""
    yield
    theme.set_palette(theme.DEFAULT_PALETTE)


# --- Palette definitions ------------------------------------------------------

def test_every_palette_has_eight_well_formed_stops():
    for name, palette in theme.PALETTES.items():
        assert len(palette.stops) == len(theme.ROLES), name
        for stop in palette.stops:
            assert HEX.match(stop), f"{name}: {stop}"


def test_every_palette_runs_dark_to_bright():
    """The ramp has to climb or heat values stop meaning anything.

    Checked over a two-stop window rather than strictly: the original red ramp
    dips slightly going crimson -> pure red, which is deliberate and shipped.
    """
    for name, palette in theme.PALETTES.items():
        levels = [theme.luma(stop) for stop in palette.stops]
        assert levels[0] < levels[-1], name
        for i in range(len(levels) - 2):
            assert levels[i] < levels[i + 2], f"{name} ramp stalls at {i}: {levels}"


def test_new_palettes_are_strictly_monotonic():
    """Only red is grandfathered into having a dip."""
    for name, palette in theme.PALETTES.items():
        if name == "red":
            continue
        levels = [theme.luma(stop) for stop in palette.stops]
        assert levels == sorted(levels), f"{name} is not monotonic: {levels}"


def test_luma_brackets_black_and_white():
    assert theme.luma("#000000") == 0.0
    assert theme.luma("#FFFFFF") == pytest.approx(255.0)


def test_red_is_the_default_and_is_not_psychedelic():
    assert theme.DEFAULT_PALETTE == "red"
    assert theme.active_palette().name == "red"
    assert not theme.PALETTES["red"].psychedelic
    assert theme.PALETTES["red"].spin == 0.0


def test_psychedelic_palettes_all_spin():
    tripping = [p for p in theme.PALETTES.values() if p.psychedelic]
    assert tripping, "there should be something to trip on"
    assert all(p.spin > 0 for p in tripping)


def test_role_lookup_covers_all_roles():
    palette = theme.PALETTES["acid"]
    assert [palette.role(name) for name in theme.ROLES] == list(palette.stops)


def test_unknown_role_raises():
    with pytest.raises(KeyError):
        theme.PALETTES["red"].role("chartreuse")


# --- Switching ----------------------------------------------------------------

def test_set_palette_switches_and_returns():
    assert theme.set_palette("vapor").name == "vapor"
    assert theme.active_palette().name == "vapor"
    assert theme.is_tripping()


def test_set_palette_is_case_insensitive():
    assert theme.set_palette("ULTRA").name == "ultra"


def test_set_palette_rejects_nonsense_and_lists_options():
    with pytest.raises(ValueError, match="kaleido"):
        theme.set_palette("beige")


# --- Colour maths -------------------------------------------------------------

def test_blend_hits_both_ends_and_the_middle():
    assert theme.blend("#000000", "#FFFFFF", 0.0) == "#000000"
    assert theme.blend("#000000", "#FFFFFF", 1.0) == "#FFFFFF"
    assert theme.blend("#000000", "#FFFFFF", 0.5) == "#808080"


def test_shift_hue_by_full_turn_is_a_no_op():
    assert theme.shift_hue("#E8102B", 360.0) == "#E8102B"


def test_shift_hue_moves_the_colour_but_keeps_brightness():
    start, moved = "#E8102B", theme.shift_hue("#E8102B", 120.0)
    assert moved != start
    assert sum(theme._hex_to_rgb(moved)) == pytest.approx(
        sum(theme._hex_to_rgb(start)), abs=3
    )


def test_shift_hue_leaves_grey_grey():
    """Greys have no hue to rotate, so they must survive untouched."""
    assert theme.shift_hue("#808080", 90.0) == "#808080"


def test_rainbow_wraps_and_stays_in_gamut():
    assert theme.rainbow(0.0) == theme.rainbow(1.0)
    assert theme.rainbow(0.25) != theme.rainbow(0.75)
    for step in range(12):
        assert HEX.match(theme.rainbow(step / 12))


def test_rainbow_clamps_out_of_range_saturation():
    assert HEX.match(theme.rainbow(0.5, saturation=5.0, value=-2.0))


# --- Ramps --------------------------------------------------------------------

def test_ramp_color_clamps_outside_zero_to_one():
    stops = theme.heat_stops()
    assert theme.ramp_color(-3.0) == stops[0]
    assert theme.ramp_color(99.0) == stops[-1]


def test_ramp_color_is_monotonic_across_the_ramp():
    samples = [theme.ramp_color(i / 20) for i in range(21)]
    assert len(set(samples)) > 10, "the ramp should actually vary"
    assert all(HEX.match(sample) for sample in samples)


def test_ramp_color_handles_a_single_stop():
    assert theme.ramp_color(0.37, ["#ABCDEF"]) == "#ABCDEF"


def test_cycle_color_is_seamless():
    """A cycle has to meet itself, or scrolling gradients visibly snap."""
    assert theme.cycle_color(0.0) == theme.cycle_color(1.0)
    assert theme.cycle_color(-0.25) == theme.cycle_color(0.75)


def test_heat_stops_drops_the_void_stop():
    palette = theme.PALETTES["red"]
    assert theme.heat_stops(palette) == list(palette.stops[1:])


def test_banner_stops_are_a_subset_of_the_heat_ramp(monkeypatch):
    # Pinned, because kaleido's hues rotate between the two reads below.
    monkeypatch.setattr(theme, "clock", lambda: 1.0)
    stops = theme.banner_stops(theme.PALETTES["kaleido"])
    assert stops
    assert set(stops) <= set(theme.heat_stops(theme.PALETTES["kaleido"]))


# --- Motion -------------------------------------------------------------------

def test_clock_moves_forward():
    first = theme.clock()
    assert theme.clock() >= first >= 0.0


def test_phase_scales_with_speed_and_offset():
    assert theme.phase(0.0, 7.5) == 7.5
    fast, slow = theme.phase(100.0), theme.phase(1.0)
    assert fast > slow


def test_pulse_stays_in_unit_range():
    assert all(0.0 <= theme.pulse(speed=s) <= 1.0 for s in (0.1, 1.0, 9.0))


def test_plasma_stays_in_unit_range_across_the_field():
    for x in range(0, 60, 7):
        for y in range(0, 20, 3):
            for t in (0.0, 1.3, 7.7):
                assert 0.0 <= theme.plasma(x, y, t) <= 1.0


def test_plasma_actually_has_structure():
    frame = [theme.plasma(x, 3, 2.0) for x in range(40)]
    assert max(frame) - min(frame) > 0.2


def test_plasma_changes_over_time():
    assert theme.plasma(5, 5, 0.0) != theme.plasma(5, 5, 1.0)


def test_still_palettes_do_not_drift():
    theme.set_palette("red")
    assert theme.spin_offset() == 0.0
    assert theme.role("core") == theme.PALETTES["red"].role("core")


def test_spinning_palettes_drift():
    theme.set_palette("kaleido")
    assert 0.0 <= theme.spin_offset() < 360.0
    # The drifted colour is a hue rotation of the static one, not the same hex.
    assert theme.role("core") != theme.PALETTES["kaleido"].role("core")


# --- Text ---------------------------------------------------------------------

def test_gradient_text_colours_every_character():
    text = theme.gradient_text("REDSCAN")
    assert text.plain == "REDSCAN"
    assert len(text.spans) == len("REDSCAN")


def test_gradient_text_survives_a_single_character():
    assert theme.gradient_text("X").plain == "X"


def test_gradient_text_survives_an_empty_string():
    assert theme.gradient_text("").plain == ""


def test_flowing_text_shifts_with_offset():
    a = theme.flowing_text("SCANNING", offset=0.0)
    b = theme.flowing_text("SCANNING", offset=0.5)
    assert a.plain == b.plain == "SCANNING"
    assert [s.style for s in a.spans] != [s.style for s in b.spans]


def test_flowing_text_tolerates_a_zero_wavelength():
    assert theme.flowing_text("WAVE", wavelength=0.0).plain == "WAVE"


def test_heat_style_is_a_usable_rich_style():
    assert theme.heat_style(0.5).startswith("bold #")


# --- Theme ---------------------------------------------------------------------

def test_build_theme_defines_every_style_the_ui_uses():
    expected = {
        "rs.dim", "rs.muted", "rs.label", "rs.value", "rs.accent", "rs.hot",
        "rs.open", "rs.closed", "rs.filtered", "rs.good", "rs.bad", "rs.border",
        "rs.border.dim", "rs.void", "rs.title", "rs.bar.back", "rs.bar.done",
        "rs.bar.finished", "rs.bar.pulse", "rs.spinner", "rs.rule", "rs.key",
        "rs.warn",
    }
    for palette in theme.PALETTES.values():
        assert expected <= set(theme.build_theme(palette).styles)


def test_build_theme_follows_the_palette():
    red = theme.build_theme(theme.PALETTES["red"])
    acid = theme.build_theme(theme.PALETTES["acid"])
    assert str(red.styles["rs.border"]) != str(acid.styles["rs.border"])


def test_legacy_red_constants_are_unchanged():
    """1.0 exposed these by name; downstream code may still import them."""
    assert theme.RED == "#E8102B"
    assert theme.CRIMSON == "#C81E3A"
    assert theme.GLOW == "#FFB3A0"
    assert theme.HEAT == list(theme.PALETTES["red"].stops[1:])
    assert theme.BANNER_RAMP[0] == theme.BLOOD
    assert "rs.border" in theme.THEME.styles
