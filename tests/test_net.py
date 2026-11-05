"""Tests for the engine: real sockets on loopback, and canned output from ping."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from contextlib import contextmanager

import pytest

from redscan import net


def free_port() -> int:
    """A port nothing is listening on, because we just stopped listening on it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def service(payload: bytes | None = b"", wait_for_request: bool = False):
    """A real listening service on loopback. ``payload=None`` stays silent."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(16)

    def run() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                try:
                    if wait_for_request:
                        conn.settimeout(1)
                        conn.recv(512)
                    if payload:
                        conn.sendall(payload)
                    time.sleep(0.05 if payload else 1.2)
                except OSError:
                    pass

    threading.Thread(target=run, daemon=True).start()
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


# --- Banners ------------------------------------------------------------------

@pytest.mark.parametrize("sent,shown", [
    (b"SSH-2.0-OpenSSH_9.6\r\n", "SSH-2.0-OpenSSH_9.6"),
    (b"\r\n\r\n220 ready\r\nsecond line", "220 ready"),
    (b"220 \x1b[31mred\x1b[0m", "220 [31mred[0m"),
    (b"220 \xc2\x9b31m tail", "220 31m tail"),
    (b"220 \xe2\x80\xaeesrever", "220 esrever"),
    (b"220 \xe2\x80\x8bhidden", "220 hidden"),
    (b"\x00\x01\x02\xff\xfe\x80\x81", None),
    ("x".encode() * 400, "x" * 96),
])
def test_only_clean_text_survives_from_a_banner(sent, shown):
    with service(sent) as port:
        result = net.probe_port("127.0.0.1", port, timeout=1.0)
    assert result.state == "open"
    assert result.banner == shown


# --- Probing ------------------------------------------------------------------

def test_a_closed_port_is_reported_as_closed_and_quickly():
    """Windows used to retry for two seconds, past the default timeout: "filtered"."""
    started = time.perf_counter()
    result = net.probe_port("127.0.0.1", free_port())
    assert result.state == "closed"
    assert time.perf_counter() - started < 0.5


def test_only_open_ports_pay_for_an_os_service_lookup(monkeypatch):
    """getservbyport costs more than the probe itself on Windows, on all 65,535 ports."""
    asked = []
    monkeypatch.setattr(net, "service_name", lambda port: asked.append(port) or "looked-up")
    closed = free_port()
    assert net.probe_port("127.0.0.1", closed).service != "looked-up"
    assert asked == []
    with service(b"x") as port:
        assert net.probe_port("127.0.0.1", port).service == "looked-up"
    assert asked == [port]


def test_a_listening_port_is_open_with_its_banner():
    with service(b"220 hello\r\n") as port:
        result = net.probe_port("127.0.0.1", port)
    assert result.is_open and result.banner == "220 hello" and result.latency_ms is not None


def test_a_silent_service_is_open_with_no_banner():
    with service(None) as port:
        result = net.probe_port("127.0.0.1", port)
    assert result.is_open and result.banner is None


def test_grabbing_can_be_turned_off():
    with service(b"220 hello\r\n") as port:
        assert net.probe_port("127.0.0.1", port, grab=False).banner is None


def test_http_ports_are_asked_a_question_before_listening(monkeypatch):
    with service(b"HTTP/1.1 200 OK\r\nServer: x\r\n\r\n", wait_for_request=True) as port:
        monkeypatch.setattr(net, "HTTP_PORTS", {port})
        assert net.probe_port("127.0.0.1", port).banner == "HTTP/1.1 200 OK"


def test_tcp_ping_measures_an_open_port():
    with service(b"x") as port:
        pong = net.tcp_ping("127.0.0.1", port, seq=7)
    assert pong.alive and pong.seq == 7


def test_tcp_ping_says_refused_plainly_and_quickly():
    """The monitor's latency column is eleven wide, so no paragraph from the OS."""
    started = time.perf_counter()
    pong = net.tcp_ping("127.0.0.1", free_port())
    assert not pong.alive and pong.error == "refused"
    assert time.perf_counter() - started < 0.5


# --- Scanning -----------------------------------------------------------------

def test_a_scan_reports_every_port_exactly_once():
    with service(b"x") as open_port:
        closed = free_port()
        results = list(net.scan_ports("127.0.0.1", [open_port, closed], workers=4, timeout=0.5))
    assert {r.port: r.state for r in results} == {open_port: "open", closed: "closed"}


def test_an_empty_port_list_scans_nothing():
    assert list(net.scan_ports("127.0.0.1", [])) == []


def test_abandoning_a_scan_does_not_wait_for_the_queue(monkeypatch):
    """This is Ctrl+C on a full sweep, which used to sit there finishing the scan."""
    ran = []

    def slow_probe(ip, port, timeout, grab):
        ran.append(port)
        time.sleep(0.02)
        return net.PortResult(port, "filtered", "x")

    monkeypatch.setattr(net, "probe_port", slow_probe)
    scan = net.scan_ports("127.0.0.1", range(1, 5001), workers=2)
    next(scan)
    started = time.perf_counter()
    scan.close()
    assert time.perf_counter() - started < 0.5
    time.sleep(0.1)
    assert len(ran) < 100


# --- Reading ping's output ----------------------------------------------------

def canned_ping(monkeypatch, output: str, code: int = 0, windows: bool = True) -> dict:
    monkeypatch.setattr(net, "IS_WINDOWS", windows)
    captured: dict = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, code, output, "")

    monkeypatch.setattr(net.subprocess, "run", run)
    return captured


@pytest.mark.parametrize("line,latency,ttl", [
    ("Reply from 8.8.8.8: bytes=32 time=12ms TTL=117", 12.0, 117),
    ("Reply from 127.0.0.1: bytes=32 time<1ms TTL=128", 1.0, 128),
    ("Antwort von 8.8.8.8: Bytes=32 Zeit=12ms TTL=117", 12.0, 117),
    ("R\u00e9ponse de 8.8.8.8\u00a0: octets=32 temps=12 ms TTL=117", 12.0, 117),
    ("Respuesta desde 8.8.8.8: bytes=32 tiempo=12ms TTL=117", 12.0, 117),
    ("\u041e\u0442\u0432\u0435\u0442 \u043e\u0442 8.8.8.8: \u0447\u0438\u0441\u043b\u043e \u0431\u0430\u0439\u0442=32 \u0432\u0440\u0435\u043c\u044f=12\u043c\u0441 TTL=117", 12.0, 117),
    ("\u6765\u81ea 8.8.8.8 \u7684\u56de\u590d: \u5b57\u8282=32 \u65f6\u95f4=12ms TTL=117", 12.0, 117),
])
def test_a_windows_reply_is_read_in_any_language(monkeypatch, line, latency, ttl):
    canned_ping(monkeypatch, f"\nPinging 8.8.8.8 with 32 bytes of data:\n{line}\n")
    pong = net.icmp_ping("8.8.8.8", seq=3)
    assert (pong.alive, pong.latency_ms, pong.ttl, pong.seq) == (True, latency, ttl, 3)


@pytest.mark.parametrize("line", [
    "Reply from 10.0.0.1: Destination host unreachable.",
    "Antwort von 10.0.0.1: Zielhost nicht erreichbar.",
    "R\u00e9ponse de 10.0.0.1\u00a0: H\u00f4te de destination inaccessible.",
    "Reply from 10.0.0.1: TTL expired in transit.",
])
def test_windows_exit_zero_without_an_echo_reply_is_not_alive(monkeypatch, line):
    """Windows exits 0 on these, and they are translated. Only the TTL can be trusted."""
    canned_ping(monkeypatch, line, code=0)
    assert not net.icmp_ping("10.0.0.1").alive


def test_a_timed_out_ping_is_not_alive(monkeypatch):
    canned_ping(monkeypatch, "Request timed out.\n", code=1)
    pong = net.icmp_ping("10.0.0.1")
    assert not pong.alive and pong.error == "no reply"


def test_a_linux_reply_is_read(monkeypatch):
    canned_ping(monkeypatch, "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=12.3 ms\n\n"
                             "1 packets transmitted, 1 received, 0% packet loss\n", windows=False)
    pong = net.icmp_ping("8.8.8.8")
    assert (pong.latency_ms, pong.ttl) == (12.3, 117)


def test_a_linux_host_that_stays_silent_is_not_alive(monkeypatch):
    canned_ping(monkeypatch, "1 packets transmitted, 0 received, 100% packet loss\n",
                code=1, windows=False)
    assert not net.icmp_ping("8.8.8.8").alive


def test_undecodable_ping_output_cannot_fail_the_reply(monkeypatch):
    captured = canned_ping(monkeypatch, "Reply: bytes=32 time=1ms TTL=5")
    net.icmp_ping("8.8.8.8")
    assert captured["errors"] == "replace"


def test_the_real_ping_binary_reaches_loopback():
    pong = net.icmp_ping("127.0.0.1", timeout_ms=2000)
    assert pong.alive and pong.ttl


# --- Names --------------------------------------------------------------------

def test_an_address_resolves_to_itself():
    target = net.resolve("127.0.0.1")
    assert target.ip == "127.0.0.1" and target.hostname is None and target.is_private


def test_a_name_is_kept_alongside_its_address():
    target = net.resolve("localhost")
    assert target.ip == "127.0.0.1" and target.hostname == "localhost"


@pytest.mark.parametrize("host", ["", "   ", "a..b", "a" * 70 + ".com", "does-not-exist.invalid"])
def test_a_bad_name_is_a_readable_error(host):
    with pytest.raises(ValueError):
        net.resolve(host)


def test_a_slow_reverse_lookup_cannot_hold_up_the_start(monkeypatch):
    """gethostbyaddr ignores socket timeouts: this sat for 5s on any address with no PTR."""
    answered = threading.Event()

    def slow(ip):
        answered.wait(5)
        return ("late.example", [], [ip])

    monkeypatch.setattr(net.socket, "gethostbyaddr", slow)
    started = time.perf_counter()
    assert net._reverse_dns("10.0.0.5", deadline=0.1) is None
    assert time.perf_counter() - started < 1.0
    answered.set()


def test_a_reverse_lookup_that_answers_in_time_is_used(monkeypatch):
    monkeypatch.setattr(net.socket, "gethostbyaddr", lambda ip: ("router.lan", [], [ip]))
    assert net._reverse_dns("10.0.0.5") == "router.lan"


def test_a_failed_reverse_lookup_is_just_no_name(monkeypatch):
    def nope(ip):
        raise socket.herror(1, "not found")

    monkeypatch.setattr(net.socket, "gethostbyaddr", nope)
    assert net._reverse_dns("10.0.0.5") is None


# --- Port specs and the OS guess ----------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("22", [22]),
    ("22,80,443", [22, 80, 443]),
    ("80,22,80", [22, 80]),
    ("1-3", [1, 2, 3]),
    ("3-1", [1, 2, 3]),
    ("22 80", [22, 80]),
    ("1-2,5", [1, 2, 5]),
])
def test_port_specs_parse(spec, expected):
    assert net.parse_ports(spec) == expected


def test_top_and_full_are_what_they_say():
    assert net.parse_ports("top") == list(net.TOP_PORTS) and len(net.TOP_PORTS) == 88
    assert net.parse_ports("") == list(net.TOP_PORTS)
    assert net.parse_ports("full") == list(range(1, 65536))
    assert 22 in net.parse_ports("top,60000") and 60000 in net.parse_ports("top,60000")


@pytest.mark.parametrize("spec", ["abc", "0", "65536", "70000", "1-70000", "0-5", "5-", "-5", "1-2-3"])
def test_impossible_port_specs_are_refused(spec):
    with pytest.raises(ValueError):
        net.parse_ports(spec)


@pytest.mark.parametrize("ttl,expected", [
    (None, "unknown"), (64, "Linux / Unix, ~0 hops away"), (63, "Linux / Unix, ~1 hop away"),
    (128, "Windows, ~0 hops away"), (117, "Windows, ~11 hops away"),
    (255, "network gear, ~0 hops away"),
])
def test_the_os_guess(ttl, expected):
    assert net.guess_os(ttl) == expected
