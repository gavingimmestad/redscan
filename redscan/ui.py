"""Everything that puts red pixels on the terminal."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .banner import sweep_bar
from .net import PingStats, Pong, PortResult, Target, guess_os
from .theme import BLOOD, CRIMSON, EMBER, FLARE, GLOW, RED, gradient_text, ramp_color

SPARKS = "▁▂▃▄▅▆▇█"
BLOCKS = " ░▒▓█"


def make_progress(description: str = "scanning") -> Progress:
    """A progress bar that looks like it is on fire."""
    return Progress(
        SpinnerColumn("dots12", style="rs.spinner"),
        TextColumn("[rs.label]{task.description}"),
        BarColumn(
            bar_width=None,
            style="rs.bar.back",
            complete_style="rs.bar.done",
            finished_style="rs.bar.finished",
            pulse_style="rs.bar.pulse",
        ),
        TaskProgressColumn(text_format="[rs.hot]{task.percentage:>5.1f}%"),
        MofNCompleteColumn(),
        TextColumn("[rs.dim]|"),
        TimeElapsedColumn(),
        TextColumn("[rs.dim]eta"),
        TimeRemainingColumn(),
        expand=True,
    )


def sparkline(values: list[float], width: int = 48,
              ceiling: float | None = None) -> Text:
    """Latency history as a heat-coloured sparkline."""
    text = Text()
    if not values:
        return Text("no samples yet", style="rs.muted")
    window = values[-width:]
    top = ceiling or max(window) or 1.0
    for value in window:
        fraction = min(value / top, 1.0)
        glyph = SPARKS[min(int(fraction * (len(SPARKS) - 1)), len(SPARKS) - 1)]
        text.append(glyph, style=f"bold {ramp_color(fraction)}")
    return text


def coverage_strip(done: int, total: int, open_count: int, width: int = 56) -> Text:
    """A bar that fills with embers as the scan burns through the port list."""
    width = max(width, 10)
    filled = int(width * (done / total)) if total else 0
    text = Text()
    for i in range(width):
        if i < filled:
            text.append("█", style=ramp_color(i / max(width - 1, 1),
                                              [BLOOD, CRIMSON, RED, EMBER, FLARE]))
        elif i == filled:
            text.append("▓", style=f"bold {GLOW}")
        else:
            text.append("░", style="rs.border.dim")
    text.append(f"  {open_count} open", style="rs.open" if open_count else "rs.muted")
    return text


def kv_table(rows: list[tuple[str, RenderableType]]) -> Table:
    """A borderless label/value grid."""
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="rs.label", no_wrap=True)
    table.add_column(style="rs.value")
    for label, value in rows:
        table.add_row(label, value)
    return table


def target_panel(target: Target, extra: list[tuple[str, RenderableType]] | None = None,
                 title: str = "TARGET LOCKED") -> Panel:
    """Identity card for the host under the microscope."""
    rows: list[tuple[str, RenderableType]] = [
        ("input", Text(target.raw, style=f"bold {GLOW}")),
        ("address", Text(target.ip, style=f"bold {EMBER}")),
    ]
    if target.reverse:
        rows.append(("reverse dns", Text(target.reverse, style="rs.value")))
    rows.append((
        "scope",
        Text("private / LAN" if target.is_private else "public / internet",
             style="rs.hot"),
    ))
    rows.extend(extra or [])
    return Panel(
        kv_table(rows),
        title=Text(f"◤ {title} ◥", style="rs.title"),
        border_style="rs.border",
        box=box.HEAVY,
        padding=(0, 1),
    )


def port_table(results: list[PortResult], limit: int = 18,
               show_banner: bool = True) -> Table:
    """The open-ports table, hottest service first."""
    table = Table(
        box=box.SIMPLE_HEAD,
        border_style="rs.border.dim",
        header_style=f"bold {CRIMSON}",
        expand=True,
        pad_edge=False,
    )
    table.add_column("PORT", justify="right", style=f"bold {FLARE}",
                     width=6, no_wrap=True)
    table.add_column("STATE", width=6, no_wrap=True)
    table.add_column("SERVICE", style="rs.value", width=15, no_wrap=True)
    table.add_column("RTT", justify="right", style="rs.muted", width=8, no_wrap=True)
    if show_banner:
        table.add_column("BANNER", style="rs.muted", overflow="ellipsis",
                         no_wrap=True, ratio=1, min_width=12)

    shown = sorted(results, key=lambda r: r.port)[:limit]
    for result in shown:
        latency = f"{result.latency_ms:.0f} ms" if result.latency_ms is not None else "-"
        row = [
            str(result.port),
            Text("OPEN", style="rs.open"),
            result.service,
            latency,
        ]
        if show_banner:
            row.append(result.banner or "—")
        table.add_row(*row)

    if not shown:
        filler = ["—", Text("waiting", style="rs.muted"), "—", "—"]
        if show_banner:
            filler.append("—")
        table.add_row(*filler)
    elif len(results) > limit:
        more = f"+{len(results) - limit} more"
        row = [Text(more, style="rs.hot"), "", "", ""]
        if show_banner:
            row.append("")
        table.add_row(*row)
    return table


def counters(open_n: int, closed_n: int, filtered_n: int, elapsed: float,
             done: int) -> Columns:
    """Four little stat tiles along the bottom."""
    rate = done / elapsed if elapsed > 0 else 0.0
    tiles = [
        ("OPEN", str(open_n), "rs.open"),
        ("CLOSED", str(closed_n), "rs.closed"),
        ("FILTERED", str(filtered_n), "rs.filtered"),
        ("PORTS/SEC", f"{rate:,.0f}", "rs.hot"),
        ("ELAPSED", f"{elapsed:,.1f}s", "rs.value"),
    ]
    panels = [
        Panel(
            Align.center(Text(value, style=style)),
            title=Text(label, style="rs.muted"),
            border_style="rs.border.dim",
            box=box.ROUNDED,
            padding=(0, 1),
            width=16,
        )
        for label, value, style in tiles
    ]
    return Columns(panels, align="center", expand=True)


@dataclass
class ScanView:
    """Mutable state behind the live scan dashboard."""

    target: Target
    total: int
    grab: bool = True
    started: float = field(default_factory=time.perf_counter)
    open_ports: list[PortResult] = field(default_factory=list)
    closed: int = 0
    filtered: int = 0
    done: int = 0
    phase: int = 0

    def record(self, result: PortResult) -> None:
        self.done += 1
        if result.is_open:
            self.open_ports.append(result)
        elif result.state == "closed":
            self.closed += 1
        else:
            self.filtered += 1

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def render(self, progress: Progress, width: int = 80) -> RenderableType:
        self.phase += 1
        strip_width = max(min(width - 22, 64), 12)
        header = Panel(
            Group(
                Align.center(gradient_text("R E D S C A N   ::   L I V E   S W E E P")),
                Align.center(sweep_bar(self.phase, strip_width)),
            ),
            border_style="rs.border",
            box=box.HEAVY,
            padding=(0, 1),
        )
        body = Panel(
            Group(
                progress,
                Text(""),
                Align.center(coverage_strip(self.done, self.total,
                                            len(self.open_ports), strip_width)),
            ),
            title=Text("◤ PROGRESS ◥", style="rs.title"),
            border_style="rs.border",
            box=box.HEAVY,
            padding=(0, 1),
        )
        hits = Panel(
            port_table(self.open_ports, show_banner=self.grab and width > 90),
            title=Text("◤ OPEN PORTS ◥", style="rs.title"),
            border_style="rs.border",
            box=box.HEAVY,
            padding=(0, 1),
        )
        return Group(
            header,
            target_panel(self.target, [("ports queued", Text(f"{self.total:,}",
                                                             style="rs.hot"))]),
            body,
            hits,
            counters(len(self.open_ports), self.closed, self.filtered,
                     self.elapsed, self.done),
        )


def ping_panel(target: Target, stats: PingStats, history: list[Pong],
               width: int = 80) -> RenderableType:
    """Live ping dashboard: reply log, sparkline and rolling statistics."""
    table = Table(
        box=box.SIMPLE_HEAD,
        border_style="rs.border.dim",
        header_style=f"bold {CRIMSON}",
        expand=True,
        pad_edge=False,
    )
    table.add_column("SEQ", justify="right", width=6, style="rs.muted")
    table.add_column("REPLY", width=10)
    table.add_column("LATENCY", justify="right", width=11, style="rs.value")
    table.add_column("TTL", justify="right", width=6, style="rs.muted")
    table.add_column("TREND", overflow="crop")

    ceiling = max(stats.samples, default=1.0) or 1.0
    for pong in history[-12:]:
        if pong.latency_ms is None:
            table.add_row(str(pong.seq), Text("✖ LOST", style="rs.bad"),
                          Text(pong.error or "no reply", style="rs.bad"), "—", "")
            continue
        fraction = min(pong.latency_ms / ceiling, 1.0)
        bar_len = max(int(fraction * 24), 1)
        bar = Text("▰" * bar_len, style=f"bold {ramp_color(fraction)}")
        table.add_row(
            str(pong.seq),
            Text("✔ REPLY", style="rs.good"),
            f"{pong.latency_ms:.1f} ms",
            str(pong.ttl or "—"),
            bar,
        )

    last_ttl = next((p.ttl for p in reversed(history) if p.ttl), None)
    stat_rows: list[tuple[str, RenderableType]] = [
        ("sent / recv", Text(f"{stats.sent} / {stats.received}", style="rs.value")),
        ("packet loss", Text(
            f"{stats.loss_pct:.0f}%",
            style="rs.bad" if stats.loss_pct > 0 else "rs.good")),
        ("best", Text(f"{stats.best:.1f} ms" if stats.best is not None else "—",
                      style="rs.good")),
        ("average", Text(f"{stats.avg:.1f} ms" if stats.avg is not None else "—",
                         style="rs.value")),
        ("worst", Text(f"{stats.worst:.1f} ms" if stats.worst is not None else "—",
                       style="rs.hot")),
        ("jitter", Text(f"{stats.jitter:.1f} ms" if stats.jitter is not None else "—",
                        style="rs.value")),
        ("os guess", Text(guess_os(last_ttl), style="rs.muted")),
    ]

    graph = Panel(
        Group(
            sparkline(stats.samples, width=max(width - 12, 20)),
            Text(f"peak {ceiling:.1f} ms", style="rs.muted"),
        ),
        title=Text("◤ LATENCY ◥", style="rs.title"),
        border_style="rs.border",
        box=box.HEAVY,
        padding=(0, 1),
    )

    return Group(
        Panel(
            Align.center(gradient_text("R E D S C A N   ::   P I N G   M O N I T O R")),
            border_style="rs.border",
            box=box.HEAVY,
            padding=(0, 1),
        ),
        target_panel(target, title="PINGING"),
        Panel(
            table,
            title=Text("◤ REPLIES ◥", style="rs.title"),
            border_style="rs.border",
            box=box.HEAVY,
            padding=(0, 1),
        ),
        graph,
        Panel(
            kv_table(stat_rows),
            title=Text("◤ STATISTICS ◥", style="rs.title"),
            border_style="rs.border",
            box=box.HEAVY,
            padding=(0, 1),
        ),
    )


def scan_summary(target: Target, view: ScanView) -> RenderableType:
    """The after-action report."""
    open_ports = sorted(view.open_ports, key=lambda r: r.port)
    verdict = (
        gradient_text(f"{len(open_ports)} OPEN PORT{'S' if len(open_ports) != 1 else ''} FOUND")
        if open_ports
        else Text("NO OPEN PORTS - the host is quiet or shielded", style="rs.muted")
    )
    rows: list[tuple[str, RenderableType]] = [
        ("target", Text(target.label, style=f"bold {EMBER}")),
        ("ports probed", Text(f"{view.done:,} of {view.total:,}", style="rs.value")),
        ("open", Text(str(len(open_ports)), style="rs.open")),
        ("closed", Text(f"{view.closed:,}", style="rs.closed")),
        ("filtered", Text(f"{view.filtered:,}", style="rs.filtered")),
        ("duration", Text(f"{view.elapsed:,.2f}s", style="rs.value")),
        ("throughput", Text(f"{view.done / max(view.elapsed, 0.001):,.0f} ports/sec",
                            style="rs.hot")),
    ]
    pieces: list[RenderableType] = [
        Align.center(verdict),
        Rule(style="rs.rule"),
        kv_table(rows),
    ]
    if open_ports:
        pieces.extend([
            Rule(style="rs.rule"),
            port_table(open_ports, limit=100),
        ])
    return Panel(
        Group(*pieces),
        title=Text("◤ SCAN COMPLETE ◥", style="rs.title"),
        border_style="rs.border",
        box=box.DOUBLE,
        padding=(1, 2),
    )


def ping_summary(target: Target, stats: PingStats, history: list[Pong]) -> RenderableType:
    """Closing statistics for a ping run."""
    alive = stats.received > 0
    verdict = (
        gradient_text(f"HOST IS UP - {stats.received}/{stats.sent} replies")
        if alive
        else Text("HOST DID NOT ANSWER", style="rs.bad")
    )
    last_ttl = next((p.ttl for p in reversed(history) if p.ttl), None)
    rows: list[tuple[str, RenderableType]] = [
        ("target", Text(target.label, style=f"bold {EMBER}")),
        ("transmitted", Text(str(stats.sent), style="rs.value")),
        ("received", Text(str(stats.received), style="rs.good")),
        ("lost", Text(f"{stats.lost} ({stats.loss_pct:.0f}%)",
                      style="rs.bad" if stats.lost else "rs.good")),
        ("best / avg / worst", Text(
            f"{stats.best:.1f} / {stats.avg:.1f} / {stats.worst:.1f} ms"
            if stats.samples else "—", style="rs.value")),
        ("jitter", Text(f"{stats.jitter:.1f} ms" if stats.jitter is not None else "—",
                        style="rs.value")),
        ("ttl / os guess", Text(f"{last_ttl or '—'}  ·  {guess_os(last_ttl)}",
                                style="rs.muted")),
    ]
    return Panel(
        Group(
            Align.center(verdict),
            Rule(style="rs.rule"),
            kv_table(rows),
            Rule(style="rs.rule"),
            Align.center(sparkline(stats.samples, width=60)),
        ),
        title=Text("◤ PING COMPLETE ◥", style="rs.title"),
        border_style="rs.border",
        box=box.DOUBLE,
        padding=(1, 2),
    )


def notice(console: Console, message: str, style: str = "rs.warn") -> None:
    """One-line aside, indented to line up with the panels."""
    console.print(Padding(Text(f"▸ {message}", style=style), (0, 0, 0, 2)))
