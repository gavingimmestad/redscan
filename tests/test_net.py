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
