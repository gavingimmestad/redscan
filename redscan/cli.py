"""Command line and interactive front end for RedScan."""

from __future__ import annotations

import argparse
import csv
import json
import socket
import sys
import time
from dataclasses import asdict
from pathlib import Path

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import __version__
from .banner import hero, logo_text
from .net import (
    PingStats,
    Pong,

    Target,
    icmp_ping,
    local_ip,
    parse_ports,
    resolve,
    scan_ports,
    tcp_ping,
)
from .theme import EMBER, FLARE, GLOW, THEME, gradient_text
from .ui import (
    ScanView,
    make_progress,
    notice,
    ping_panel,
    ping_summary,
    scan_summary,
    target_panel,
)

MENU = [
    ("1", "Quick scan", "the 88 ports that matter, plus banner grabbing"),
    ("2", "Custom port scan", "pick your own ports or ranges"),
    ("3", "Full sweep", "all 65,535 TCP ports - bring a coffee"),
    ("4", "Ping monitor", "live ICMP latency, loss and jitter"),
    ("5", "TCP ping", "latency to one port when ICMP is blocked"),
    ("6", "Recon combo", "ping, then scan the top ports in one pass"),
    ("7", "My network", "this machine's addresses"),
    ("q", "Quit", "close the console"),
]


def build_console() -> Console:
    """A console that knows how to render red on every Windows terminal."""
    if sys.platform == "win32":  # make box drawing survive legacy code pages
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
            # Classic conhost starts with VT off, which drops Rich to 16 colours.
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # VT processing
        except Exception:  # pragma: no cover - cosmetic only
            pass
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # pragma: no cover - cosmetic only
                pass
    return Console(theme=THEME, highlight=False, soft_wrap=False)


# --- Animated intro -----------------------------------------------------------

BOOT_STEPS = [
    "warming up the sockets",
    "loading 88 service fingerprints",
    "arming the thread pool",
    "calibrating the radar",
]


def boot(console: Console, animate: bool = True) -> None:
    """The gratuitous startup animation. Worth every millisecond."""
    console.print()
    if not animate:
        console.print(hero(console, phase=0, version=__version__))
        return

    lines = logo_text(console).plain.splitlines()
    with Live(console=console, refresh_per_second=30, transient=True) as live:
        for rows in range(1, len(lines) + 1):  # logo wipes in line by line
            live.update(Align.center(logo_text(console, rows=rows)))
            time.sleep(0.055)
        for phase in range(0, 26, 2):          # then the radar spins up
            live.update(hero(console, phase=phase, version=__version__))
            time.sleep(0.035)
    console.print(hero(console, phase=18, version=__version__))

    with Live(console=console, refresh_per_second=24, transient=True) as live:
        for i, step in enumerate(BOOT_STEPS, start=1):
            dots = Text("".join("·" for _ in range(i * 3)), style="rs.dim")
            live.update(Align.center(Text.assemble(
                (f"  {step} ", f"bold {EMBER}"), dots, ("  ok", f"bold {FLARE}"))))
            time.sleep(0.12)
    console.print()


# --- Actions ------------------------------------------------------------------

def do_scan(console: Console, target: Target, ports: list[int], workers: int,
            timeout: float, grab: bool) -> ScanView:
    """Run a port scan behind the live dashboard."""
    view = ScanView(target=target, total=len(ports), grab=grab)
    progress = make_progress()
    task = progress.add_task("sweeping ports", total=len(ports))
    last_draw = 0.0

    with Live(view.render(progress, console.width), console=console,
              refresh_per_second=15, transient=True) as live:
        for result in scan_ports(target.ip, ports, workers=workers,
                                 timeout=timeout, grab=grab):
            view.record(result)
            progress.advance(task)
            now = time.perf_counter()
            # Redrawing on every one of 65k results would out-run the terminal.
            if result.is_open or now - last_draw > 0.07:
                live.update(view.render(progress, console.width))
                last_draw = now
        live.update(view.render(progress, console.width))

    console.print(scan_summary(target, view))
    return view


def do_ping(console: Console, target: Target, count: int, interval: float,
            timeout_ms: int, port: int | None = None) -> tuple[PingStats, list[Pong]]:
    """Ping in a loop behind the live monitor. ``count <= 0`` runs forever."""
    stats = PingStats()
    history: list[Pong] = []
    endless = count <= 0
    if endless:
        notice(console, "continuous mode - press Ctrl+C to stop", "rs.warn")

    try:
        with Live(ping_panel(target, stats, history, console.width), console=console,
                  refresh_per_second=12, transient=True) as live:
            seq = 0
            while endless or seq < count:
                seq += 1
                pong = (tcp_ping(target.ip, port, seq, timeout_ms / 1000)
                        if port else icmp_ping(target.ip, seq, timeout_ms))
                stats.add(pong)
                history.append(pong)
                live.update(ping_panel(target, stats, history, console.width))
                if endless or seq < count:
                    time.sleep(interval)
    except KeyboardInterrupt:
        notice(console, "interrupted", "rs.warn")

    console.print(ping_summary(target, stats, history))
    return stats, history


def do_network_info(console: Console) -> None:
    """Show what this machine looks like from the inside."""
    rows = [
        ("hostname", socket.gethostname()),
        ("lan address", local_ip()),
        ("loopback", "127.0.0.1"),
    ]
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        if addresses:
            rows.append(("all addresses", ", ".join(addresses)))
    except OSError:
        pass

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="rs.label")
    table.add_column(style="rs.value")
    for label, value in rows:
        table.add_row(label, Text(str(value), style=f"bold {GLOW}"))
    console.print(Panel(
        table,
        title=Text("◤ THIS MACHINE ◥", style="rs.title"),
        border_style="rs.border",
        padding=(1, 2),
    ))


# --- Export -------------------------------------------------------------------

def export_results(console: Console, target: Target, view: ScanView,
                   json_path: str | None, csv_path: str | None) -> None:
    """Write scan results to disk in whichever formats were requested."""
    if json_path:
        payload = {
            "target": asdict(target),
            "scanned": view.done,
            "queued": view.total,
            "elapsed_seconds": round(view.elapsed, 3),
            "counts": {
                "open": len(view.open_ports),
                "closed": view.closed,
                "filtered": view.filtered,
            },
            "open_ports": [asdict(p) for p in sorted(view.open_ports,
                                                     key=lambda r: r.port)],
        }
        path = Path(json_path)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        notice(console, f"json written to {path.resolve()}", "rs.good")

    if csv_path:
        path = Path(csv_path)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["port", "state", "service", "latency_ms", "banner"])
            for result in sorted(view.open_ports, key=lambda r: r.port):
                writer.writerow([result.port, result.state, result.service,
                                 result.latency_ms, result.banner or ""])
        notice(console, f"csv written to {path.resolve()}", "rs.good")


# --- Interactive menu ---------------------------------------------------------

def show_menu(console: Console) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="rs.key", width=3)
    table.add_column(style=f"bold {GLOW}", width=18)
    table.add_column(style="rs.muted")
    for key, label, blurb in MENU:
        table.add_row(f"[{key}]", label, blurb)
    console.print(Panel(
        table,
        title=Text("◤ CHOOSE YOUR WEAPON ◥", style="rs.title"),
        border_style="rs.border",
        padding=(1, 2),
    ))


def ask(console: Console, prompt: str, default: str = "") -> str:
    """Prompt with the red theme; empty input falls back to ``default``."""
    suffix = f" [rs.muted]({default})[/]" if default else ""
    try:
        answer = console.input(f"[rs.key]▸[/] [rs.label]{prompt}[/]{suffix} [rs.hot]»[/] ")
    except (EOFError, KeyboardInterrupt):
        return ""
    return answer.strip() or default


def resolve_or_report(console: Console, host: str) -> Target | None:
    try:
        with console.status(Text(f"resolving {host}", style="rs.hot"),
                            spinner="dots12", spinner_style="rs.spinner"):
            return resolve(host)
    except ValueError as exc:
        notice(console, str(exc), "rs.bad")
        return None


def interactive(console: Console, args: argparse.Namespace) -> int:
    """Menu loop for when RedScan is launched without arguments."""
    while True:
        show_menu(console)
        choice = ask(console, "selection", "1").lower()
        console.print()

        if choice in {"q", "quit", "exit"}:
            console.print(Align.center(gradient_text("stay curious. stay authorised.")))
            console.print()
            return 0

        if choice == "7":
            do_network_info(console)
            console.print(Rule(style="rs.rule"))
            continue

        if choice not in {"1", "2", "3", "4", "5", "6"}:
            notice(console, f"unknown selection {choice!r}", "rs.bad")
            continue

        host = ask(console, "target host or ip", "127.0.0.1")
        if not host:
            continue
        target = resolve_or_report(console, host)
        if target is None:
            continue
        console.print(target_panel(target))

        if choice in {"1", "6"}:
            ports = parse_ports("top")
        elif choice == "2":
            spec = ask(console, "ports (e.g. 22,80,443 or 1-1024)", "1-1024")
            try:
                ports = parse_ports(spec)
            except ValueError as exc:
                notice(console, str(exc), "rs.bad")
                continue
        elif choice == "3":
            notice(console, "full sweep: 65,535 ports - this takes a while", "rs.warn")
            ports = parse_ports("full")
        else:
            ports = []

        if choice in {"4", "6"}:
            count = 0 if choice == "4" else 4
            do_ping(console, target, count, args.interval, args.timeout_ms)
        if choice == "5":
            port_text = ask(console, "tcp port to ping", "443")
            try:
                tcp_port = int(port_text)
            except ValueError:
                notice(console, f"not a port: {port_text!r}", "rs.bad")
                continue
            do_ping(console, target, args.count or 4, args.interval,
                    args.timeout_ms, port=tcp_port)

        if ports:
            view = do_scan(console, target, ports, args.workers,
                           args.timeout, not args.no_banner)
            export_results(console, target, view, args.json, args.csv)

        console.print(Rule(style="rs.rule"))


# --- Argument parsing ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redscan",
        description="RedScan - an unreasonably good looking port scanner and IP pinger.",
        epilog="Run with no arguments for the interactive menu.",
    )
    parser.add_argument("target", nargs="?", help="hostname or IP address")
    parser.add_argument("-p", "--ports", default="top",
                        help="ports: 'top', 'full', '22,80,443' or '1-1024'")
    parser.add_argument("-P", "--ping", action="store_true",
                        help="ping the target before scanning")
    parser.add_argument("--ping-only", action="store_true",
                        help="ping and skip the port scan")
    parser.add_argument("-c", "--count", type=int, default=4,
                        help="ping count, 0 for continuous (default: 4)")
    parser.add_argument("-i", "--interval", type=float, default=1.0,
                        help="seconds between pings (default: 1.0)")
    parser.add_argument("--tcp-ping", type=int, metavar="PORT",
                        help="measure latency to a TCP port instead of ICMP")
    parser.add_argument("-w", "--workers", type=int, default=400,
                        help="concurrent socket workers (default: 400)")
    parser.add_argument("-t", "--timeout", type=float, default=0.6,
                        help="per-port connect timeout in seconds (default: 0.6)")
    parser.add_argument("--timeout-ms", type=int, default=1200,
                        help="ping timeout in milliseconds (default: 1200)")
    parser.add_argument("--no-banner", action="store_true",
                        help="skip service banner grabbing")
    parser.add_argument("--no-anim", action="store_true",
                        help="skip the startup animation")
    parser.add_argument("--json", metavar="FILE", help="write results to JSON")
    parser.add_argument("--csv", metavar="FILE", help="write open ports to CSV")
    parser.add_argument("--net-info", action="store_true",
                        help="show this machine's addresses and exit")
    parser.add_argument("-V", "--version", action="version",
                        version=f"RedScan {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    console = build_console()
    launched_bare = not argv

    boot(console, animate=not args.no_anim)

    try:
        if args.net_info:
            do_network_info(console)
            return 0

        if not args.target:
            return interactive(console, args)

        target = resolve_or_report(console, args.target)
        if target is None:
            return 2
        console.print(target_panel(target))

        if args.ping or args.ping_only or args.tcp_ping:
            do_ping(console, target, args.count, args.interval, args.timeout_ms,
                    port=args.tcp_ping)
        if args.ping_only:
            return 0

        try:
            ports = parse_ports(args.ports)
        except ValueError as exc:
            notice(console, str(exc), "rs.bad")
            return 2

        view = do_scan(console, target, ports, args.workers, args.timeout,
                       not args.no_banner)
        export_results(console, target, view, args.json, args.csv)
        return 0
    except KeyboardInterrupt:
        notice(console, "aborted", "rs.warn")
        return 130
    finally:
        if launched_bare and sys.stdin and sys.stdin.isatty():
            try:
                console.input("\n[rs.muted]press enter to close[/] ")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
