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
