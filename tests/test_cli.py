"""Tests for argument parsing, palette selection and the menu wiring."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from redscan import cli, theme


@pytest.fixture(autouse=True)
def reset_palette():
    cli._theme_depth = 0
    yield
    theme.set_palette(theme.DEFAULT_PALETTE)
    cli._theme_depth = 0


def make_console(width: int = 100) -> Console:
    return Console(file=io.StringIO(), theme=theme.THEME, width=width,
                   force_terminal=True, record=True)


def parse(*argv: str):
    return cli.build_parser().parse_args(list(argv))


# --- Palette flags ------------------------------------------------------------

def test_no_flags_means_red():
    assert cli.resolve_palette(parse()) == "red"


def test_palette_flag_is_honoured():
    assert cli.resolve_palette(parse("--palette", "vapor")) == "vapor"


def test_trip_picks_something_psychedelic():
    assert cli.resolve_palette(parse("--trip")) in cli.PSYCHEDELIC


def test_trip_never_picks_red():
    """--trip has one job."""
    for _ in range(40):
        assert cli.resolve_palette(parse("--trip")) != "red"


def test_explicit_palette_beats_trip():
    assert cli.resolve_palette(parse("--trip", "--palette", "oil")) == "oil"


def test_palette_flag_rejects_nonsense():
    with pytest.raises(SystemExit):
        parse("--palette", "beige")


def test_psychedelic_list_matches_the_palettes_marked_as_such():
    assert cli.PSYCHEDELIC == sorted(
        name for name, p in theme.PALETTES.items() if p.psychedelic
    )
    assert "red" not in cli.PSYCHEDELIC


# --- apply_palette ------------------------------------------------------------

def test_apply_palette_switches_the_active_palette():
    console = make_console()
    assert cli.apply_palette(console, "ultra").name == "ultra"
    assert theme.active_palette().name == "ultra"


def test_apply_palette_repaints_the_console_theme():
    console = make_console()
    before = console.get_style("rs.border")
    cli.apply_palette(console, "oil")
    assert console.get_style("rs.border") != before


def test_apply_palette_does_not_stack_themes_forever():
    """Pressing 8 repeatedly must not grow the console's theme stack."""
    console = make_console()
    for name in ("acid", "vapor", "oil", "ultra", "kaleido", "red"):
        cli.apply_palette(console, name)
    assert cli._theme_depth == 1


def test_apply_palette_can_return_to_red():
    console = make_console()
    cli.apply_palette(console, "kaleido")
    assert cli.apply_palette(console, "red").name == "red"
    assert not theme.is_tripping()


# --- Swatches and the gallery -------------------------------------------------

@pytest.mark.parametrize("width", [1, 8, 22, 60])
def test_swatch_is_exactly_its_width(width):
    assert len(cli.swatch(theme.PALETTES["acid"], width).plain) == width


def test_swatch_colours_every_cell():
    chip = cli.swatch(theme.PALETTES["kaleido"], 22)
    assert len(chip.spans) == 22
    assert len({str(s.style) for s in chip.spans}) > 5


def test_gallery_lists_every_palette_with_its_blurb():
    console = make_console(120)
    console.print(cli.palette_gallery())
    out = console.export_text().replace("\n", " ")
    for name, palette in theme.PALETTES.items():
        assert name in out
        assert palette.blurb.split(":")[0][:20] in out


def test_gallery_marks_the_psychedelic_ones():
    console = make_console(120)
    console.print(cli.palette_gallery())
    out = console.export_text()
    assert out.count("*") >= len(cli.PSYCHEDELIC)


# --- Parser -------------------------------------------------------------------

def test_defaults_are_unchanged_from_1_0():
    args = parse()
    assert args.ports == "top"
    assert args.count == 4
    assert args.interval == 1.0
    assert args.workers == 400
    assert args.timeout == 0.6
    assert args.timeout_ms == 1200
    assert args.target is None
    assert not args.no_anim


def test_existing_flags_still_parse():
    args = parse("example.test", "-p", "1-1024", "-P", "-c", "9", "-w", "64",
                 "--no-banner", "--no-anim", "--json", "out.json", "--csv", "out.csv")
    assert args.target == "example.test"
    assert args.ports == "1-1024"
    assert args.ping
    assert args.count == 9
    assert args.workers == 64
    assert args.no_banner
    assert args.no_anim
    assert args.json == "out.json"
    assert args.csv == "out.csv"


def test_list_palettes_is_a_flag():
    assert parse("--list-palettes").list_palettes


def test_version_exits_cleanly():
    with pytest.raises(SystemExit) as exc:
        parse("--version")
    assert exc.value.code == 0


# --- main ---------------------------------------------------------------------

def test_list_palettes_prints_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    assert cli.main(["--list-palettes"]) == 0


def test_list_palettes_skips_the_boot_animation(monkeypatch):
    """Listing colours should be instant, not sit through the intro."""
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    monkeypatch.setattr(cli, "boot", lambda *a, **k: pytest.fail("boot ran"))
    assert cli.main(["--list-palettes"]) == 0


def test_main_applies_the_requested_palette(monkeypatch):
    monkeypatch.setattr(cli, "build_console",
                        lambda palette="red": (theme.set_palette(palette),
                                               make_console(120))[1])
    monkeypatch.setattr(cli, "boot", lambda *a, **k: None)
    monkeypatch.setattr(cli, "do_network_info", lambda console: None)
    assert cli.main(["--palette", "vapor", "--net-info"]) == 0
    assert theme.active_palette().name == "vapor"


def test_bad_port_spec_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    monkeypatch.setattr(cli, "boot", lambda *a, **k: None)
    monkeypatch.setattr(cli, "resolve_or_report",
                        lambda console, host: cli.Target(raw=host, ip="127.0.0.1"))
    assert cli.main(["127.0.0.1", "-p", "not-ports"]) == 2


def test_unresolvable_target_returns_two(monkeypatch):
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    monkeypatch.setattr(cli, "boot", lambda *a, **k: None)
    monkeypatch.setattr(cli, "resolve_or_report", lambda console, host: None)
    assert cli.main(["nope.invalid"]) == 2


# --- Menu ---------------------------------------------------------------------

def test_menu_has_a_repaint_entry():
    keys = {key for key, _, _ in cli.MENU}
    assert "8" in keys
    assert "q" in keys


def test_menu_repaint_blurb_lists_the_palettes():
    blurb = next(b for key, _, b in cli.MENU if key == "8")
    for name in theme.PALETTES:
        assert name in blurb


def test_show_menu_renders_every_entry():
    console = make_console(120)
    cli.show_menu(console)
    out = console.export_text()
    for _, label, _ in cli.MENU:
        assert label in out


def test_choose_palette_repaints_from_input(monkeypatch):
    console = make_console(120)
    monkeypatch.setattr(cli, "ask", lambda *a, **k: "acid")
    cli.choose_palette(console)
    assert theme.active_palette().name == "acid"


def test_choose_palette_accepts_random(monkeypatch):
    console = make_console(120)
    monkeypatch.setattr(cli, "ask", lambda *a, **k: "random")
    cli.choose_palette(console)
    assert theme.active_palette().name in cli.PSYCHEDELIC


def test_choose_palette_rejects_an_unknown_name(monkeypatch):
    console = make_console(120)
    monkeypatch.setattr(cli, "ask", lambda *a, **k: "beige")
    cli.choose_palette(console)
    assert theme.active_palette().name == "red"
    assert "unknown palette" in console.export_text()


def test_choose_palette_on_empty_input_changes_nothing(monkeypatch):
    console = make_console(120)
    monkeypatch.setattr(cli, "ask", lambda *a, **k: "")
    cli.choose_palette(console)
    assert theme.active_palette().name == "red"


# --- Boot ---------------------------------------------------------------------

def test_boot_without_animation_prints_one_banner(monkeypatch):
    console = make_console(100)
    cli.boot(console, animate=False)
    assert "RedScan" in console.export_text()


def test_boot_step_lists_are_the_same_length():
    """The two lists are swapped in place, so they have to line up."""
    assert len(cli.BOOT_STEPS) == len(cli.TRIP_BOOT_STEPS)


def test_boot_animation_runs_and_is_bounded(monkeypatch):
    """Guard the frame budget: the intro should not become a feature film."""
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(seconds))
    theme.set_palette("kaleido")
    cli.boot(make_console(100), animate=True)
    assert slept
    assert sum(slept) < 4.0
