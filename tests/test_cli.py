"""Tests for argument parsing, palette selection and the menu wiring."""

from __future__ import annotations

import csv
import io
import json

import pytest
from rich.console import Console

from redscan import cli, theme
from redscan.net import PortResult, Target
from redscan.ui import ScanView

TARGET = Target(raw="10.0.0.5", ip="10.0.0.5")


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


# --- Ctrl+C -------------------------------------------------------------------

def interrupted_scan(ip, ports, workers=0, timeout=0.0, grab=True):
    yield PortResult(22, "open", "ssh", 1.0, "SSH-2.0-x")
    raise KeyboardInterrupt


def test_ctrl_c_during_a_scan_keeps_what_it_found(monkeypatch):
    monkeypatch.setattr(cli, "scan_ports", interrupted_scan)
    console = make_console(120)
    view = cli.do_scan(console, TARGET, [22, 80, 443], 10, 0.1, True)
    text = console.export_text()
    assert (view.done, view.total) == (1, 3)
    assert "interrupted" in text
    assert "SCAN INTERRUPTED" in text and "SSH-2.0-x" in text and "1 of 3" in text


def test_ctrl_c_with_nothing_found_does_not_claim_the_host_is_quiet(monkeypatch):
    def nothing_yet(ip, ports, workers=0, timeout=0.0, grab=True):
        yield PortResult(80, "filtered", "http")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "scan_ports", nothing_yet)
    console = make_console(120)
    cli.do_scan(console, TARGET, [80, 81], 10, 0.1, True)
    text = console.export_text()
    assert "cut short" in text and "quiet or shielded" not in text


def test_a_finished_scan_is_still_called_complete(monkeypatch):
    def complete(ip, ports, workers=0, timeout=0.0, grab=True):
        yield PortResult(80, "closed", "http")

    monkeypatch.setattr(cli, "scan_ports", complete)
    console = make_console(120)
    cli.do_scan(console, TARGET, [80], 10, 0.1, True)
    assert "SCAN COMPLETE" in console.export_text()


def test_an_interrupted_scan_says_so_in_its_exit_code(monkeypatch):
    partial = ScanView(target=TARGET, total=3)
    partial.record(PortResult(22, "open", "ssh", 1.0))
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    monkeypatch.setattr(cli, "boot", lambda *a, **k: None)
    monkeypatch.setattr(cli, "resolve_or_report", lambda console, host: TARGET)
    monkeypatch.setattr(cli, "do_scan", lambda *a, **k: partial)
    assert cli.main(["10.0.0.5", "-p", "22,80,443"]) == 130


@pytest.mark.parametrize("hook,argv", [("boot", ["--net-info"]), ("detonate", ["--nuke"])])
def test_ctrl_c_in_an_animation_exits_cleanly_with_no_traceback(monkeypatch, hook, argv):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    console = make_console(120)
    monkeypatch.setattr(cli, "build_console", lambda palette="red": console)
    monkeypatch.setattr(cli, "can_play", lambda console: True)
    monkeypatch.setattr(cli, hook, interrupt)
    assert cli.main(argv) == 130
    assert "aborted" in console.export_text()


# --- The menu when input stops ------------------------------------------------

def feed(console, monkeypatch, *answers):
    """Answer prompts in turn, then behave like a closed stdin."""
    queue = iter(answers)

    def fake_input(*args, **kwargs):
        try:
            answer = next(queue)
        except StopIteration:
            raise EOFError from None
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(console, "input", fake_input)


def test_the_menu_leaves_when_input_ends(monkeypatch):
    """It used to re-ask forever, flat out: 4 MB of menus in fifteen seconds."""
    console = make_console(120)
    feed(console, monkeypatch)
    assert cli.interactive(console, parse()) == 0


def test_ctrl_c_at_the_menu_leaves(monkeypatch):
    console = make_console(120)
    feed(console, monkeypatch, KeyboardInterrupt())
    assert cli.interactive(console, parse()) == 0


def test_enter_at_the_menu_still_means_the_default(monkeypatch):
    console = make_console(120)
    feed(console, monkeypatch, "", "")  # quick scan, then the host prompt ends input
    seen = []
    monkeypatch.setattr(cli, "resolve_or_report", lambda c, h: seen.append(h) or None)
    assert cli.interactive(console, parse()) == 0
    assert seen == ["127.0.0.1"]


def test_input_ending_at_the_ports_prompt_does_not_start_a_scan(monkeypatch):
    """An empty answer parses as 'top', so this used to launch the wrong scan."""
    console = make_console(120)
    feed(console, monkeypatch, "2", "127.0.0.1")
    monkeypatch.setattr(cli, "resolve_or_report", lambda c, h: TARGET)
    monkeypatch.setattr(cli, "do_scan", lambda *a, **k: pytest.fail("scanned"))
    assert cli.interactive(console, parse()) == 0


@pytest.mark.parametrize("answer", ["abc", "0", "-5", "65536", "99999"])
def test_the_menu_refuses_an_impossible_tcp_port(monkeypatch, answer):
    console = make_console(120)
    feed(console, monkeypatch, "5", "127.0.0.1", answer)
    monkeypatch.setattr(cli, "resolve_or_report", lambda c, h: TARGET)
    monkeypatch.setattr(cli, "do_ping", lambda *a, **k: pytest.fail("pinged"))
    assert cli.interactive(console, parse()) == 0
    assert "not a port" in console.export_text()


# --- Flags that cannot mean anything ------------------------------------------

@pytest.mark.parametrize("argv", [
    ["--tcp-ping", "0"], ["--tcp-ping", "-5"], ["--tcp-ping", "65536"], ["--tcp-ping", "abc"],
    ["-i", "-1"], ["-i", "nan"], ["-i", "inf"], ["-i", "abc"],
    ["-t", "0"], ["-t", "-1"], ["-t", "nan"], ["-t", "inf"], ["-t", "1e300"],
    ["--timeout-ms", "0"], ["--timeout-ms", "-5"], ["--timeout-ms", "abc"],
    ["-w", "0"], ["-w", "-5"], ["-w", "abc"],
])
def test_impossible_flag_values_are_refused_up_front(argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        parse(*argv)
    assert stopped.value.code == 2
    assert "expected" in capsys.readouterr().err or "invalid" in capsys.readouterr().err


@pytest.mark.parametrize("argv,attribute,value", [
    (["-i", "0"], "interval", 0.0),
    (["-i", "0.25"], "interval", 0.25),
    (["-t", "0.05"], "timeout", 0.05),
    (["-w", "1"], "workers", 1),
    (["--timeout-ms", "1"], "timeout_ms", 1),
    (["--tcp-ping", "65535"], "tcp_ping", 65535),
    (["--tcp-ping", "1"], "tcp_ping", 1),
])
def test_sensible_flag_values_still_work(argv, attribute, value):
    assert getattr(parse(*argv), attribute) == value


# --- Exports ------------------------------------------------------------------

def finished_view(*banners: str) -> ScanView:
    view = ScanView(target=TARGET, total=len(banners) + 1)
    for port, banner in enumerate(banners, start=1000):
        view.record(PortResult(port, "open", "x", 1.5, banner))
    view.record(PortResult(9, "closed", "discard"))
    return view


def test_exports_hold_what_the_scan_found(tmp_path):
    json_path, csv_path = tmp_path / "out.json", tmp_path / "out.csv"
    console = make_console(160)
    assert cli.export_results(console, TARGET, finished_view('smtp, "quoted"', "ssh"),
                              str(json_path), str(csv_path))
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["counts"] == {"open": 2, "closed": 1, "filtered": 0}
    assert (data["scanned"], data["queued"]) == (3, 3)
    assert [p["port"] for p in data["open_ports"]] == [1000, 1001]
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    assert [(r["port"], r["banner"]) for r in rows] == [("1000", 'smtp, "quoted"'), ("1001", "ssh")]


@pytest.mark.parametrize("banner", ["=cmd()", "+1+1", "-2+3", "@SUM(A1)", "\tx"])
def test_a_banner_cannot_become_a_spreadsheet_formula(tmp_path, banner):
    csv_path = tmp_path / "out.csv"
    cli.export_results(make_console(160), TARGET, finished_view(banner), None, str(csv_path))
    row = next(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
    assert row["banner"] == "'" + banner


def test_the_json_keeps_a_banner_exactly_as_sent(tmp_path):
    json_path = tmp_path / "out.json"
    cli.export_results(make_console(160), TARGET, finished_view("=cmd()"), str(json_path), None)
    assert json.loads(json_path.read_text(encoding="utf-8"))["open_ports"][0]["banner"] == "=cmd()"


def test_an_unwritable_path_is_reported_not_raised(tmp_path):
    console = make_console(200)
    missing = tmp_path / "no" / "such" / "folder"
    assert not cli.export_results(console, TARGET, finished_view("x"),
                                  str(missing / "a.json"), str(tmp_path))
    text = console.export_text()
    assert text.count("could not write") == 2


def test_one_bad_export_does_not_stop_the_other(tmp_path):
    csv_path = tmp_path / "out.csv"
    console = make_console(200)
    assert not cli.export_results(console, TARGET, finished_view("x"),
                                  str(tmp_path / "no" / "a.json"), str(csv_path))
    assert csv_path.exists()


def test_a_failed_export_shows_in_the_exit_code(monkeypatch, tmp_path):
    complete = finished_view("x")
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(200))
    monkeypatch.setattr(cli, "boot", lambda *a, **k: None)
    monkeypatch.setattr(cli, "resolve_or_report", lambda console, host: TARGET)
    monkeypatch.setattr(cli, "do_scan", lambda *a, **k: complete)
    assert cli.main(["10.0.0.5", "-p", "22", "--csv", str(tmp_path)]) == 1
    assert cli.main(["10.0.0.5", "-p", "22", "--csv", str(tmp_path / "ok.csv")]) == 0
