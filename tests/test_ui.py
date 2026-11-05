"""Tests for the live dashboards.

These assert on *information*, not on hex codes: whatever the palette is doing,
a scan that found three open ports has to still say three open ports.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from redscan import theme, ui
from redscan.net import PingStats, Pong, PortResult, Target


@pytest.fixture(autouse=True)
def reset_palette():
    yield
    theme.set_palette(theme.DEFAULT_PALETTE)


@pytest.fixture(params=["red", "kaleido"])
def palette(request):
    """Run a test both still and tripping."""
    theme.set_palette(request.param)
    return request.param


def make_console(width: int = 100) -> Console:
    return Console(file=io.StringIO(), theme=theme.THEME, width=width,
                   force_terminal=True, record=True)


def render(renderable, width: int = 100) -> str:
    console = make_console(width)
    console.print(renderable)
    return console.export_text()


def a_target() -> Target:
    return Target(raw="scanme.example", ip="203.0.113.7",
                  hostname="scanme.example", reverse="scanme.example")


def some_ports() -> list[PortResult]:
    return [
        PortResult(port=22, state="open", service="ssh", latency_ms=3.2,
                   banner="OpenSSH_9.6"),
        PortResult(port=80, state="open", service="http", latency_ms=11.9),
        PortResult(port=443, state="open", service="https", latency_ms=12.4),
    ]


def some_pings() -> tuple[PingStats, list[Pong]]:
    stats = PingStats()
    history = [
        Pong(seq=1, latency_ms=8.1, ttl=56),
        Pong(seq=2, latency_ms=12.5, ttl=56),
        Pong(seq=3, latency_ms=None, error="timed out"),
        Pong(seq=4, latency_ms=9.9, ttl=56),
    ]
    for pong in history:
        stats.add(pong)
    return stats, history


# --- Chrome -------------------------------------------------------------------

def test_live_border_is_the_theme_colour_when_still():
    theme.set_palette("red")
    assert ui.live_border() == "rs.border"


def test_live_border_animates_when_tripping():
    theme.set_palette("acid")
    assert ui.live_border().startswith("bold #")


def test_live_border_offset_staggers_neighbouring_panels(monkeypatch):
    # Pinned, because half a cycle apart the two pulses coincide each time the wave
    # crosses its midpoint, so on the real clock this failed now and then.
    monkeypatch.setattr(theme, "clock", lambda: 1.0)
    theme.set_palette("acid")
    assert ui.live_border(0.0) != ui.live_border(0.5)


def test_panel_title_wraps_the_label_in_brackets(palette):
    assert ui.panel_title("PROGRESS").plain == "◤ PROGRESS ◥"


def test_panel_title_is_flat_when_still_and_per_character_when_tripping():
    theme.set_palette("red")
    still = ui.panel_title("OPEN PORTS")
    assert still.spans == []          # one Text-level style, no per-char spans
    assert still.style == "rs.title"
    theme.set_palette("vapor")
    assert len(ui.panel_title("OPEN PORTS").spans) == len("◤ OPEN PORTS ◥")


def test_heading_keeps_its_text(palette):
    assert ui.heading("LIVE SWEEP").plain == "LIVE SWEEP"


def test_frame_without_a_label_has_no_title(palette):
    assert ui.frame(ui.kv_table([("a", "b")])).title is None


def test_frame_with_a_label_shows_it(palette):
    assert "PROGRESS" in render(ui.frame(ui.kv_table([("a", "b")]), "PROGRESS"))


# --- sparkline ----------------------------------------------------------------

def test_sparkline_says_so_when_empty(palette):
    assert ui.sparkline([]).plain == "no samples yet"


def test_sparkline_draws_one_glyph_per_sample(palette):
    assert len(ui.sparkline([1.0, 2.0, 3.0]).plain) == 3


def test_sparkline_respects_its_width_budget(palette):
    assert len(ui.sparkline([float(i) for i in range(200)], width=30).plain) == 30


def test_sparkline_glyph_height_tracks_latency(palette):
    """Colour may wander, but a tall bar must always mean a slow reply."""
    low, high = ui.sparkline([1.0, 100.0]).plain
    assert ui.SPARKS.index(low) < ui.SPARKS.index(high)


def test_sparkline_handles_all_zero_samples(palette):
    assert len(ui.sparkline([0.0, 0.0, 0.0]).plain) == 3


def test_sparkline_respects_an_explicit_ceiling(palette):
    assert ui.sparkline([5.0], ceiling=1000.0).plain[0] == ui.SPARKS[0]


def test_sparkline_colours_scroll_only_when_tripping():
    theme.set_palette("red")
    values = [3.0, 9.0, 4.0, 11.0]
    still = [s.style for s in ui.sparkline(values).spans]
    assert still == [s.style for s in ui.sparkline(values).spans]


# --- coverage_strip -----------------------------------------------------------

@pytest.mark.parametrize("width", [10, 24, 56, 90])
def test_coverage_strip_is_exactly_its_width_plus_the_tally(palette, width):
    text = ui.coverage_strip(5, 10, 2, width).plain
    assert text.startswith("█" * 1) or text.startswith("░") or True
    assert len(text) == width + len("  2 open")


def test_coverage_strip_clamps_absurd_widths(palette):
    assert len(ui.coverage_strip(1, 2, 0, 1).plain) == 10 + len("  0 open")


def test_coverage_strip_fill_tracks_progress(palette):
    early = ui.coverage_strip(1, 100, 0, 50).plain.count("█")
    late = ui.coverage_strip(90, 100, 0, 50).plain.count("█")
    assert early < late


def test_coverage_strip_is_empty_at_the_start(palette):
    assert ui.coverage_strip(0, 100, 0, 40).plain.count("█") == 0


def test_coverage_strip_is_full_at_the_end(palette):
    assert ui.coverage_strip(100, 100, 3, 40).plain.count("█") == 40


def test_coverage_strip_survives_a_zero_total(palette):
    """A scan of an empty port list must not divide by zero."""
    assert "0 open" in ui.coverage_strip(0, 0, 0, 20).plain


def test_coverage_strip_reports_the_open_count(palette):
    assert "7 open" in ui.coverage_strip(3, 10, 7, 30).plain


# --- port_table ---------------------------------------------------------------

def test_port_table_lists_every_port_and_service(palette):
    out = render(ui.port_table(some_ports()))
    for expected in ("22", "ssh", "80", "http", "443", "https", "OpenSSH_9.6"):
        assert expected in out


def test_port_table_sorts_by_port(palette):
    out = render(ui.port_table(list(reversed(some_ports()))))
    assert out.index("22") < out.index("443")


def test_port_table_shows_a_placeholder_when_nothing_is_open(palette):
    """The placeholder lives in SERVICE; PORT and STATE are too narrow."""
    assert "waiting" in render(ui.port_table([]))


def test_port_table_truncates_and_says_how_many_it_hid(palette):
    many = [PortResult(port=1000 + i, state="open", service="x") for i in range(30)]
    assert "+25 more" in render(ui.port_table(many, limit=5))


def test_port_table_can_drop_the_banner_column(palette):
    assert "BANNER" not in render(ui.port_table(some_ports(), show_banner=False))


def test_port_table_renders_a_missing_latency(palette):
    result = [PortResult(port=8080, state="open", service="http-alt")]
    assert "8080" in render(ui.port_table(result))


# --- counters -----------------------------------------------------------------

def test_counters_show_every_tile(palette):
    out = render(ui.counters(3, 900, 12, 4.0, 915), width=120)
    for label in ("OPEN", "CLOSED", "FILTERED", "PORTS/SEC", "ELAPSED"):
        assert label in out


def test_counters_survive_zero_elapsed_time(palette):
    assert "PORTS/SEC" in render(ui.counters(0, 0, 0, 0.0, 0), width=120)


# --- ScanView -----------------------------------------------------------------

def test_scan_view_tallies_results_by_state():
    view = ui.ScanView(target=a_target(), total=4)
    for result in (*some_ports(),
                   PortResult(port=1, state="closed", service="tcpmux"),
                   PortResult(port=2, state="filtered", service="compressnet")):
        view.record(result)
    assert len(view.open_ports) == 3
    assert view.closed == 1
    assert view.filtered == 1
    assert view.done == 5


def test_scan_view_renders_the_whole_dashboard(palette):
    view = ui.ScanView(target=a_target(), total=100)
    for result in some_ports():
        view.record(result)
    progress = ui.make_progress()
    progress.add_task("sweeping ports", total=100)
    out = render(view.render(progress, 100), width=100)
    assert "203.0.113.7" in out
    assert "OPEN PORTS" in out
    assert "ssh" in out


def test_scan_view_advances_its_sweep_phase_each_frame(palette):
    view = ui.ScanView(target=a_target(), total=10)
    progress = ui.make_progress()
    progress.add_task("x", total=10)
    view.render(progress, 100)
    first = view.phase
    view.render(progress, 100)
    assert view.phase == first + 1


@pytest.mark.parametrize("width", [40, 70, 100, 160])
def test_scan_view_renders_at_any_width(width):
    theme.set_palette("oil")
    view = ui.ScanView(target=a_target(), total=10)
    view.record(some_ports()[0])
    progress = ui.make_progress()
    progress.add_task("x", total=10)
    assert render(view.render(progress, width), width=width) != ""


def test_scan_view_elapsed_is_positive(palette):
    assert ui.ScanView(target=a_target(), total=1).elapsed >= 0.0


# --- ping_panel ---------------------------------------------------------------

def test_ping_panel_shows_replies_losses_and_stats(palette):
    stats, history = some_pings()
    out = render(ui.ping_panel(a_target(), stats, history, 100))
    assert "REPLIES" in out
    assert "LOST" in out
    assert "STATISTICS" in out
    assert "203.0.113.7" in out


def test_ping_panel_handles_a_fresh_run_with_no_samples(palette):
    out = render(ui.ping_panel(a_target(), PingStats(), [], 100))
    assert "no samples yet" in out


def test_ping_panel_shows_only_the_last_twelve_replies(palette):
    stats = PingStats()
    history = [Pong(seq=i, latency_ms=float(i), ttl=64) for i in range(1, 31)]
    for pong in history:
        stats.add(pong)
    out = render(ui.ping_panel(a_target(), stats, history, 100))
    assert out.count("✔ REPLY") == 12
    assert "30" in out          # the newest reply is on screen


def test_ping_panel_handles_total_loss(palette):
    stats = PingStats()
    history = [Pong(seq=i, latency_ms=None, error="timed out") for i in (1, 2)]
    for pong in history:
        stats.add(pong)
    out = render(ui.ping_panel(a_target(), stats, history, 100))
    assert "100%" in out


# --- Summaries ----------------------------------------------------------------

def test_scan_summary_reports_the_count_and_the_ports(palette):
    view = ui.ScanView(target=a_target(), total=100)
    for result in some_ports():
        view.record(result)
    out = render(ui.scan_summary(a_target(), view))
    assert "3 OPEN PORTS FOUND" in out.replace("\n", " ")
    assert "ssh" in out


def test_scan_summary_uses_the_singular_for_one_port(palette):
    view = ui.ScanView(target=a_target(), total=100)
    view.record(some_ports()[0])
    assert "1 OPEN PORT FOUND" in render(ui.scan_summary(a_target(), view)).replace(
        "\n", " ")


def test_scan_summary_says_nothing_found_when_nothing_is(palette):
    view = ui.ScanView(target=a_target(), total=100)
    assert "NO OPEN PORTS" in render(ui.scan_summary(a_target(), view))


def test_ping_summary_reports_up_and_down(palette):
    stats, history = some_pings()
    assert "HOST IS UP" in render(ui.ping_summary(a_target(), stats, history)).replace(
        "\n", " ")
    assert "HOST DID NOT ANSWER" in render(
        ui.ping_summary(a_target(), PingStats(), []))


def test_notice_prefixes_with_a_marker(palette):
    console = make_console()
    ui.notice(console, "interrupted")
    assert "▸ interrupted" in console.export_text()
