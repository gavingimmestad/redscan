"""Tests for the offline alarm: when it fires, what it draws, how it cleans up."""

from __future__ import annotations

import io
import string
import time

import pytest
from rich.console import Console

from redscan import cli, nuke, theme
from redscan.net import OFFLINE_AFTER, OutageWatch, Pong, Target
from redscan.nuke import DROP, DURATION, FLASH, IMPACT, Detonation

UP = Pong(1, 12.0, ttl=64)
DOWN = Pong(1, None, error="no reply")
PRINTABLE = set(string.printable) - set("\t\r\x0b\x0c")


@pytest.fixture(autouse=True)
def reset_palette():
    yield
    theme.set_palette(theme.DEFAULT_PALETTE)


@pytest.fixture
def fake_clock(monkeypatch):
    """Time only passes when something sleeps, in big steps so the tests stay quick."""
    clock = {"now": 0.0}
    monkeypatch.setattr(time, "perf_counter", lambda: clock["now"])
    monkeypatch.setattr(time, "sleep", lambda seconds: clock.update(now=clock["now"] + 0.2))
    return clock


def make_console(width: int = 100, height: int = 34, terminal: bool = True) -> Console:
    return Console(file=io.StringIO(), theme=theme.THEME, width=width, height=height,
                   force_terminal=terminal, color_system="truecolor", _environ={},
                   legacy_windows=False)


def feed(watch: OutageWatch, *pongs: Pong) -> list[str | None]:
    return [watch.observe(pong) for pong in pongs]


# --- When it counts as offline ------------------------------------------------

def test_a_host_going_quiet_fires_once_at_the_threshold():
    events = feed(OutageWatch(), UP, *[DOWN] * OFFLINE_AFTER)
    assert events == [None] * OFFLINE_AFTER + ["offline"]


def test_staying_down_does_not_fire_again():
    events = feed(OutageWatch(), UP, *[DOWN] * (OFFLINE_AFTER + 20))
    assert events.count("offline") == 1


def test_a_single_lost_packet_is_not_an_outage():
    assert set(feed(OutageWatch(), UP, DOWN, UP, DOWN, UP)) == {None}


def test_losses_that_stop_short_of_the_threshold_do_not_accumulate():
    short = [DOWN] * (OFFLINE_AFTER - 1)
    assert set(feed(OutageWatch(), UP, *short, UP, *short, UP)) == {None}


def test_a_host_that_never_answered_never_went_anywhere():
    """That is usually a firewall eating ICMP, and no reason to reach for the button."""
    assert set(feed(OutageWatch(), *[DOWN] * 50)) == {None}


def test_recovery_is_reported_and_rearms_the_alarm():
    events = feed(OutageWatch(), UP, *[DOWN] * OFFLINE_AFTER, UP, *[DOWN] * OFFLINE_AFTER)
    assert events.count("online") == 1
    assert events.count("offline") == 2


def test_downtime_counts_from_the_first_miss():
    watch = OutageWatch()
    feed(watch, UP, DOWN)
    started = watch.since
    feed(watch, DOWN, DOWN)
    assert watch.since == started
    assert watch.downtime >= 0


# --- What it draws ------------------------------------------------------------

MOMENTS = [i / 6 for i in range(int(DURATION * 6) + 1)]


@pytest.mark.parametrize("width,height", [(46, 18), (80, 24), (100, 34), (132, 44), (47, 31)])
def test_every_frame_is_exactly_the_right_shape(width, height):
    scene = Detonation(width, height, "192.168.1.50")
    for t in MOMENTS:
        lines = scene.render(t).plain.split("\n")
        assert len(lines) == height
        assert {len(line) for line in lines} == {width}


@pytest.mark.parametrize("palette", sorted(theme.PALETTES))
def test_it_really_is_ascii_under_every_palette(palette):
    theme.set_palette(palette)
    scene = Detonation(80, 24, "192.168.1.50")
    for t in MOMENTS:
        assert set(scene.render(t).plain) <= PRINTABLE


def test_a_frame_is_a_pure_function_of_time():
    a = Detonation(80, 24, "10.0.0.1").render(IMPACT + 2.0)
    b = Detonation(80, 24, "10.0.0.1").render(IMPACT + 2.0)
    assert a.plain == b.plain
    assert a.spans == b.spans


def test_the_target_is_named_before_and_after():
    scene = Detonation(100, 34, "192.168.1.50")
    assert "[ 192.168.1.50 ]" in scene.render(IMPACT - 0.5).plain
    assert "192.168.1.50 HAS LEFT THE NETWORK" in scene.render(DURATION - 0.01).plain


def tip_row(scene: Detonation, t: float) -> int | None:
    """Where the warhead's point is in the drawn frame, or None if it cannot be seen."""
    lines = scene.render(t).plain.split("\n")
    return next((i for i, line in enumerate(lines) if line[scene.centre] == "V"), None)


@pytest.mark.parametrize("width,height", [(46, 18), (80, 24), (100, 34), (132, 44)])
def test_the_bomb_comes_out_of_the_sky(width, height):
    scene = Detonation(width, height, "192.168.1.50")
    # It starts entirely above the frame, and is still up there when it starts to fall.
    assert tip_row(scene, 0.0) is None
    assert scene.warhead_row(0.0) == scene.warhead_row(DROP) == -len(scene.warhead)
    # Then it falls, only ever downwards, all the way to the host.
    moments = [DROP + (IMPACT - DROP) * i / 40 for i in range(41)]
    rows = [scene.warhead_row(t) for t in moments]
    assert rows == sorted(rows)
    assert rows[-1] + len(scene.warhead) == scene.ground - len(nuke.HOST)
    # It is on screen for long enough to actually be watched.
    seen = [t for t in moments if tip_row(scene, t) is not None]
    assert seen and IMPACT - min(seen) > 0.5


def test_the_cloud_deck_hides_the_bomb_until_it_drops_out():
    scene = Detonation(100, 34, "192.168.1.50")
    # Its tip has crossed into the frame, but is still inside the deck.
    inside = next(t / 100 for t in range(int(DROP * 100), int(IMPACT * 100))
                  if scene.warhead_row(t / 100) + len(scene.warhead) >= 2)
    assert tip_row(scene, inside) is None
    assert tip_row(scene, IMPACT - 0.01) is not None


def test_the_trail_shows_through_the_hole_the_bomb_punched():
    scene = Detonation(100, 34, "192.168.1.50")
    lines = scene.render(IMPACT - 0.2).plain.split("\n")
    assert lines[0][scene.centre] in ".:"
    assert lines[1][scene.centre] in ".:"


def test_the_shockwave_clears_the_cloud_deck():
    scene = Detonation(100, 34, "192.168.1.50")

    def top_row_cells(t: float) -> int:
        return sum(glyph != " " for glyph in scene.render(t).plain.split("\n")[0])

    assert top_row_cells(IMPACT - 0.5) > scene.width * 0.9
    assert top_row_cells(IMPACT + nuke.SHOCK + 0.1) < scene.width * 0.2


def test_short_terminals_get_a_smaller_bomb():
    assert Detonation(46, 18, "x").warhead is nuke.SMALL_WARHEAD
    assert Detonation(100, 34, "x").warhead is nuke.WARHEAD


def test_the_cloud_ends_up_shaped_like_a_mushroom():
    """A cap much wider than the stem under it, above the middle of the frame."""
    scene = Detonation(100, 34, "192.168.1.50")
    lines = scene.render(IMPACT + nuke.CLIMB).plain.split("\n")

    def solid_cells(line: str) -> int:
        return sum(glyph in "=+*#%@" for glyph in line)

    widths = [solid_cells(line) for line in lines[:scene.ground - 6]]
    widest = widths.index(max(widths))
    assert widest < scene.ground / 2
    waist = min(widths[widest + 6:])
    assert 0 < waist < max(widths) / 4


def test_the_skyline_is_flattened_by_the_shockwave():
    scene = Detonation(100, 34, "192.168.1.50")
    rows = scene.ground - 1

    def buildings(t: float) -> int:
        line = scene.render(t).plain.split("\n")[rows]
        return line.count("|")

    assert buildings(IMPACT - 0.5) > 0
    assert buildings(IMPACT + nuke.SHOCK + 0.1) == 0


def test_the_flash_only_happens_once():
    """A photosensitive viewer should get one fade, never a strobe."""
    scene = Detonation(80, 24, "x")
    samples = [scene.flash(i / 100) for i in range(int(DURATION * 100))]
    assert max(samples) == pytest.approx(1.0, abs=0.05)
    peak = samples.index(max(samples))
    after = samples[peak:]
    assert all(later <= earlier for earlier, later in zip(after, after[1:]))
    assert not any(samples[:peak])
    assert scene.flash(IMPACT + FLASH + 0.01) == 0.0


def test_without_colour_depth_the_sky_stays_out_of_it():
    scene = Detonation(80, 24, "x", glow=False)
    for t in MOMENTS:
        assert all(" on " not in str(span.style) for span in scene.render(t).spans)


def test_the_cloud_is_painted_from_the_active_palette():
    def colours(palette: str) -> set[str]:
        theme.set_palette(palette)
        return {str(span.style) for span in Detonation(80, 24, "x").render(IMPACT + 2.0).spans}

    assert colours("red") != colours("vapor")


# --- Whether it can play ------------------------------------------------------

def test_can_play_needs_a_real_terminal_of_a_decent_size():
    assert nuke.can_play(make_console())
    assert not nuke.can_play(make_console(terminal=False))
    assert not nuke.can_play(make_console(width=nuke.MIN_WIDTH - 1))
    assert not nuke.can_play(make_console(height=nuke.MIN_HEIGHT - 1))


def test_detonate_ends_on_time_and_leaves_the_alternate_screen(fake_clock):
    """Time-driven, so it must finish even if every frame took a real while."""
    console = make_console(80, 24)
    nuke.detonate(console, "192.168.1.50")
    output = console.file.getvalue()
    assert fake_clock["now"] == pytest.approx(DURATION, abs=0.25)
    assert "\x1b[?1049h" in output
    assert output.rindex("\x1b[?1049l") > output.rindex("\x1b[?1049h")
    assert not console.is_alt_screen


def test_detonate_survives_the_terminal_being_resized(monkeypatch):
    clock = {"now": 0.0}
    console = make_console(100, 30)
    monkeypatch.setattr(nuke.time, "perf_counter", lambda: clock["now"])

    def slow_sleep(seconds: float) -> None:
        clock["now"] += seconds
        console.width = 60 if clock["now"] > 3 else 100

    monkeypatch.setattr(nuke.time, "sleep", slow_sleep)
    nuke.detonate(console, "x")
    assert clock["now"] >= DURATION - 0.1
    # A drag can land on any size at all, however silly, and that must not kill the monitor.
    for width, height in ((1, 1), (2, 4), (5, 5), (8, 6), (13, 2), (45, 17), (200, 3)):
        scene = Detonation(width, height, "192.168.1.50")
        for t in MOMENTS:
            lines = scene.render(t).plain.split("\n")
            assert len(lines) == height and {len(line) for line in lines} == {width}


def test_a_console_with_no_alternate_screen_gets_a_frame_that_cannot_scroll():
    console = make_console(100, 30)
    console.legacy_windows = True
    assert nuke._scene_for(console, "x").height == 29


def test_scene_is_capped_on_an_enormous_terminal():
    scene = nuke._scene_for(make_console(400, 120), "x")
    assert (scene.width, scene.height) == (nuke.MAX_WIDTH, nuke.MAX_HEIGHT)


# --- The ping monitor wiring --------------------------------------------------

TARGET = Target(raw="10.0.0.5", ip="10.0.0.5")


def script_pings(monkeypatch, *replies: Pong) -> None:
    queue = iter(replies)
    monkeypatch.setattr(cli, "icmp_ping", lambda ip, seq, timeout: next(queue))


def spy_detonate(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(cli, "detonate", lambda console, label: calls.append(label))
    return calls


def test_ping_monitor_detonates_when_a_live_host_goes_dark(monkeypatch):
    calls = spy_detonate(monkeypatch)
    script_pings(monkeypatch, UP, DOWN, DOWN, DOWN)
    stats, history = cli.do_ping(make_console(), TARGET, 4, 0.0, 100)
    assert calls == ["10.0.0.5"]
    assert stats.sent == 4 and len(history) == 4


def test_ping_monitor_stays_calm_when_the_host_never_answered(monkeypatch):
    calls = spy_detonate(monkeypatch)
    script_pings(monkeypatch, DOWN, DOWN, DOWN, DOWN, DOWN)
    cli.do_ping(make_console(), TARGET, 5, 0.0, 100)
    assert calls == []


def test_ping_monitor_stays_calm_over_ordinary_packet_loss(monkeypatch):
    calls = spy_detonate(monkeypatch)
    script_pings(monkeypatch, UP, DOWN, DOWN, UP, DOWN, UP)
    cli.do_ping(make_console(), TARGET, 6, 0.0, 100)
    assert calls == []


def test_a_flapping_host_gets_one_detonation_per_outage(monkeypatch):
    calls = spy_detonate(monkeypatch)
    script_pings(monkeypatch, UP, DOWN, DOWN, DOWN, DOWN, UP, DOWN, DOWN, DOWN)
    console = make_console(width=120)
    cli.do_ping(console, TARGET, 9, 0.0, 100)
    assert len(calls) == 2
    assert "is back after" in console.file.getvalue()


def test_no_anim_logs_the_outage_without_the_show(monkeypatch):
    calls = spy_detonate(monkeypatch)
    script_pings(monkeypatch, UP, DOWN, DOWN, DOWN)
    console = make_console(width=120)
    cli.do_ping(console, TARGET, 4, 0.0, 100, animate=False)
    assert calls == []
    assert "10.0.0.5 went offline" in console.file.getvalue()


def test_a_piped_run_logs_the_outage_without_the_show(monkeypatch):
    calls = spy_detonate(monkeypatch)
    script_pings(monkeypatch, UP, DOWN, DOWN, DOWN)
    console = make_console(width=120, terminal=False)
    cli.do_ping(console, TARGET, 4, 0.0, 100)
    assert calls == []
    assert "went offline" in console.file.getvalue()


def test_tcp_ping_names_the_port_it_lost(monkeypatch):
    calls = spy_detonate(monkeypatch)
    queue = iter([UP, DOWN, DOWN, DOWN])
    monkeypatch.setattr(cli, "tcp_ping", lambda ip, port, seq, timeout: next(queue))
    cli.do_ping(make_console(), TARGET, 4, 0.0, 100, port=443)
    assert calls == ["10.0.0.5:443"]


def test_the_dashboard_comes_back_after_the_show(monkeypatch, fake_clock):
    """The real detonation, nested inside the real dashboard."""
    script_pings(monkeypatch, UP, DOWN, DOWN, DOWN, UP)
    console = make_console(120, 50)
    stats, _ = cli.do_ping(console, TARGET, 5, 0.0, 100)
    output = console.file.getvalue()
    assert stats.sent == 5
    assert output.count("\x1b[?1049h") == 1
    assert "REPLIES" in output[output.rindex("\x1b[?1049l"):]
    assert not console.is_alt_screen


def test_ctrl_c_during_the_show_still_reports_the_run(monkeypatch):
    def interrupted(console, label):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "detonate", interrupted)
    script_pings(monkeypatch, UP, DOWN, DOWN, DOWN)
    console = make_console(width=120, height=60)
    stats, _ = cli.do_ping(console, TARGET, 4, 0.0, 100)
    assert stats.sent == 4
    assert "interrupted" in console.file.getvalue()


def test_main_passes_no_anim_through_to_the_monitor(monkeypatch):
    seen = {}

    def fake_ping(console, target, count, interval, timeout_ms, port=None, animate=True):
        seen["animate"] = animate

    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    monkeypatch.setattr(cli, "boot", lambda *a, **k: None)
    monkeypatch.setattr(cli, "resolve_or_report", lambda console, host: TARGET)
    monkeypatch.setattr(cli, "do_ping", fake_ping)
    assert cli.main(["10.0.0.5", "--ping-only", "--no-anim"]) == 0
    assert seen["animate"] is False
    assert cli.main(["10.0.0.5", "--ping-only"]) == 0
    assert seen["animate"] is True


# --- The --nuke preview -------------------------------------------------------

def test_preview_touches_no_network_and_skips_the_boot(monkeypatch):
    calls = spy_detonate(monkeypatch)
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    monkeypatch.setattr(cli, "boot", lambda *a, **k: pytest.fail("boot ran"))
    monkeypatch.setattr(cli, "resolve", lambda host: pytest.fail("resolved a host"))
    assert cli.main(["--nuke"]) == 0
    assert calls == [cli.DEMO_TARGET]


def test_preview_uses_the_target_it_is_given(monkeypatch):
    calls = spy_detonate(monkeypatch)
    monkeypatch.setattr(cli, "build_console", lambda palette="red": make_console(120))
    assert cli.main(["--nuke", "printer.lan"]) == 0
    assert calls == ["printer.lan"]


def test_preview_says_so_when_it_cannot_play(monkeypatch):
    calls = spy_detonate(monkeypatch)
    console = make_console(120, terminal=False)
    monkeypatch.setattr(cli, "build_console", lambda palette="red": console)
    assert cli.main(["--nuke"]) == 1
    assert calls == []
    assert "too small or not interactive" in console.file.getvalue()
