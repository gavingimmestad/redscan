"""The engine: name resolution, ICMP/TCP pinging and threaded port scanning."""

from __future__ import annotations

import contextlib
import email
import functools
import ipaddress
import os
import platform
import re
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Iterable, Iterator

IS_WINDOWS = platform.system().lower().startswith("win")
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

# A hand-picked list of the ports that actually tell you something.
TOP_PORTS: tuple[int, ...] = (
    21, 22, 23, 25, 53, 67, 69, 80, 81, 88, 110, 111, 123, 135, 137, 139, 143,
    161, 389, 443, 445, 465, 514, 515, 548, 587, 631, 636, 873, 902, 989, 990,
    993, 995, 1080, 1194, 1433, 1521, 1723, 1883, 2049, 2082, 2083, 2086, 2181,
    2375, 2376, 3000, 3128, 3268, 3306, 3389, 4444, 4567, 5000, 5060, 5432,
    5555, 5601, 5672, 5900, 5985, 5986, 6000, 6379, 6667, 7001, 8000, 8008,
    8080, 8081, 8086, 8088, 8123, 8443, 8888, 9000, 9090, 9092, 9200, 9300,
    10000, 11211, 15672, 27017, 27018, 32400, 50000,
)

SERVICES: dict[int, str] = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "domain", 67: "dhcp",
    69: "tftp", 80: "http", 81: "http-alt", 88: "kerberos", 110: "pop3",
    111: "rpcbind", 123: "ntp", 135: "msrpc", 137: "netbios-ns",
    139: "netbios-ssn", 143: "imap", 161: "snmp", 389: "ldap", 443: "https",
    445: "smb", 465: "smtps", 514: "syslog", 515: "printer", 548: "afp",
    587: "submission", 631: "ipp", 636: "ldaps", 873: "rsync", 902: "vmware",
    989: "ftps-data", 990: "ftps", 993: "imaps", 995: "pop3s",
    1080: "socks", 1194: "openvpn", 1433: "mssql", 1521: "oracle",
    1723: "pptp", 1883: "mqtt", 2049: "nfs", 2082: "cpanel", 2083: "cpanel-s",
    2086: "whm", 2181: "zookeeper", 2375: "docker", 2376: "docker-s",
    3000: "dev-http", 3128: "squid", 3268: "globalcat", 3306: "mysql",
    3389: "rdp", 4444: "krb524", 4567: "galera", 5000: "upnp",
    5060: "sip", 5432: "postgres", 5555: "adb", 5601: "kibana",
    5672: "amqp", 5900: "vnc", 5985: "winrm", 5986: "winrm-s", 6000: "x11",
    6379: "redis", 6667: "irc", 7001: "weblogic", 8000: "http-alt",
    8008: "http-alt", 8080: "http-proxy", 8081: "http-alt", 8086: "influxdb",
    8088: "http-alt", 8123: "home-assistant", 8443: "https-alt",
    8888: "http-alt", 9000: "sonarqube", 9090: "prometheus", 9092: "kafka",
    9200: "elasticsearch", 9300: "elastic-nodes", 10000: "webmin",
    11211: "memcached", 15672: "rabbitmq-ui", 27017: "mongodb",
    27018: "mongodb-shard", 32400: "plex", 50000: "sap",
}

HTTP_PORTS = {80, 81, 443, 591, 2082, 2083, 2086, 3000, 5000, 5601, 8000,
              8008, 8080, 8081, 8086, 8088, 8123, 8443, 8888, 9000, 9090,
              9200, 10000, 15672, 32400}

# Ports that expect a TLS hello straight away (no STARTTLS dance first).
TLS_PORTS = {465, 636, 853, 990, 992, 993, 995, 2376, 3269, 5061, 5671, 5986,
             6443, 6697, 8883, 9443}
LEGACY_TLS = ("SSLv3", "TLSv1", "TLSv1.1")


def service_name(port: int) -> str:
    """Friendly service label for a port, falling back to the OS database."""
    if port in SERVICES:
        return SERVICES[port]
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        return "unknown"


@dataclass(slots=True)
class Target:
    """A resolved scan target."""

    raw: str
    ip: str
    hostname: str | None = None
    reverse: str | None = None

    @property
    def label(self) -> str:
        name = self.reverse or self.hostname
        return f"{self.ip} ({name})" if name and name != self.ip else self.ip

    @property
    def is_private(self) -> bool:
        try:
            return ipaddress.ip_address(self.ip).is_private
        except ValueError:
            return False


def resolve(host: str) -> Target:
    """Resolve a hostname or IP into a Target, with reverse DNS when possible."""
    host = host.strip()
    if not host:
        raise ValueError("empty target")
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve {host!r}: {exc.strerror or exc}") from exc
    except UnicodeError as exc:  # empty or over-long labels fail in the idna codec
        raise ValueError(f"could not resolve {host!r}: not a valid hostname") from exc

    return Target(raw=host, ip=ip, hostname=None if host == ip else host,
                  reverse=_reverse_dns(ip))


def _reverse_dns(ip: str, deadline: float = 1.5) -> str | None:
    """The PTR name for an address, if it turns up in time.

    gethostbyaddr ignores socket timeouts and can sit for several seconds on an address
    with no PTR record, so a deadline has to come from outside the call.
    """
    found: list[str] = []

    def look_up() -> None:
        try:
            found.append(socket.gethostbyaddr(ip)[0])
        except OSError:
            pass

    worker = threading.Thread(target=look_up, daemon=True)
    worker.start()
    worker.join(deadline)
    return found[0] if found else None


# --- Pinging ------------------------------------------------------------------

@dataclass(slots=True)
class Pong:
    """One ping reply (or the lack of one)."""

    seq: int
    latency_ms: float | None
    ttl: int | None = None
    error: str | None = None

    @property
    def alive(self) -> bool:
        return self.latency_ms is not None


# Windows translates everything here but "TTL=": Zeit=12ms in German, temps=12 ms in
# French. So take the number right before the TTL, not the one after the word for time.
_WIN_TIME = re.compile(r"[=<]\s*(\d+)\s*[^\s=<]*\s+TTL=", re.IGNORECASE)
_NIX_TIME = re.compile(r"time\s*[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)
_TTL = re.compile(r"ttl[=:]\s*(\d+)", re.IGNORECASE)


def _ping_command(ip: str, timeout_ms: int) -> list[str]:
    if IS_WINDOWS:
        return ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    wait = max(1, round(timeout_ms / 1000))
    # -W is seconds on Linux; macOS spells the overall deadline -t.
    if platform.system().lower() == "darwin":
        return ["ping", "-c", "1", "-t", str(wait), ip]
    return ["ping", "-c", "1", "-W", str(wait), ip]


def icmp_ping(ip: str, seq: int = 0, timeout_ms: int = 1200) -> Pong:
    """One ICMP echo via the system ping binary (no raw sockets, no admin)."""
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            _ping_command(ip, timeout_ms),
            capture_output=True,
            text=True,
            errors="replace",  # ping writes in the console's code page, not ours
            timeout=max(timeout_ms / 1000 + 2, 3),
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return Pong(seq, None, error="timed out")
    except (OSError, ValueError) as exc:
        return Pong(seq, None, error=str(exc))

    out = f"{proc.stdout}\n{proc.stderr}"
    ttl_match = _TTL.search(out)
    ttl = int(ttl_match.group(1)) if ttl_match else None

    # Only a real echo reply carries a TTL, in every language ping speaks. Windows exits
    # 0 on "Destination host unreachable", and that sentence is translated too, so on a
    # German machine a dead host looked alive and the offline alarm could never fire.
    if proc.returncode != 0 or ttl_match is None:
        return Pong(seq, None, ttl=ttl, error="no reply")

    match = _WIN_TIME.search(out) if IS_WINDOWS else _NIX_TIME.search(out)
    if match:
        latency = float(match.group(1))
        # Windows prints "time<1ms" for sub-millisecond replies.
        if latency == 0:
            latency = 0.4
        return Pong(seq, latency, ttl=ttl)

    elapsed = (time.perf_counter() - started) * 1000
    return Pong(seq, round(elapsed, 2), ttl=ttl)


def tcp_ping(ip: str, port: int, seq: int = 0, timeout: float = 1.2) -> Pong:
    """Latency to an open TCP port - works where ICMP is firewalled off."""
    sock = _tcp_socket(timeout)
    started = time.perf_counter()
    try:
        code = sock.connect_ex((ip, port))
    except OSError:
        return Pong(seq, None, error="no reply")
    finally:
        sock.close()
    if code == 0:
        return Pong(seq, round((time.perf_counter() - started) * 1000, 2))
    return Pong(seq, None, error="refused" if code in _REFUSED else "no reply")


@dataclass(slots=True)
class PingStats:
    """Rolling statistics over a ping run."""

    sent: int = 0
    received: int = 0
    samples: list[float] = field(default_factory=list)

    def add(self, pong: Pong) -> None:
        self.sent += 1
        if pong.latency_ms is not None:
            self.received += 1
            self.samples.append(pong.latency_ms)

    @property
    def lost(self) -> int:
        return self.sent - self.received

    @property
    def loss_pct(self) -> float:
        return (self.lost / self.sent * 100) if self.sent else 0.0

    @property
    def best(self) -> float | None:
        return min(self.samples) if self.samples else None

    @property
    def worst(self) -> float | None:
        return max(self.samples) if self.samples else None

    @property
    def avg(self) -> float | None:
        return sum(self.samples) / len(self.samples) if self.samples else None

    @property
    def jitter(self) -> float | None:
        """Mean absolute deviation between consecutive replies."""
        if len(self.samples) < 2:
            return None
        deltas = [abs(b - a) for a, b in zip(self.samples, self.samples[1:])]
        return sum(deltas) / len(deltas)


OFFLINE_AFTER = 3  # unanswered pings in a row before a host counts as offline


@dataclass(slots=True)
class OutageWatch:
    """Decides when a target that *was* answering has actually gone offline.

    One lost ping is weather, so it takes several in a row. And a target that has
    never answered has not gone anywhere: far more often than not that is a
    firewall eating ICMP, not an outage.
    """

    threshold: int = OFFLINE_AFTER
    seen_alive: bool = False
    misses: int = 0
    down: bool = False
    since: float = 0.0

    def observe(self, pong: Pong) -> str | None:
        """Feed in one reply. Returns "offline" or "online" as the state flips."""
        if pong.alive:
            recovered = self.down
            self.seen_alive, self.misses, self.down = True, 0, False
            return "online" if recovered else None
        if not self.seen_alive or self.down:
            return None
        if self.misses == 0:
            self.since = time.perf_counter()
        self.misses += 1
        if self.misses < self.threshold:
            return None
        self.down = True
        return "offline"

    @property
    def downtime(self) -> float:
        """Seconds since the first unanswered ping of the current outage."""
        return time.perf_counter() - self.since


# --- Port scanning ------------------------------------------------------------

@dataclass(slots=True)
class PortResult:
    """The outcome of one TCP connect attempt."""

    port: int
    state: str              # open | closed | filtered
    service: str
    latency_ms: float | None = None
    banner: str | None = None
    tls: dict | None = None     # filled in by inspect_ports
    http: dict | None = None    # filled in by inspect_ports

    @property
    def is_open(self) -> bool:
        return self.state == "open"


# A banner is whatever the far end felt like sending, so nothing that can steer a
# terminal or disguise text gets through: C0 and C1 controls (0x9b is a one-byte CSI),
# zero-width and bidi overrides, and the replacement characters binary decodes into.
_BANNER_STRIP = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\ufffd]"
)


def _grab_banner(sock: socket.socket, port: int) -> str | None:
    """Best-effort service fingerprint from an already-open socket."""
    try:
        sock.settimeout(1.0)
        if port in HTTP_PORTS:
            sock.sendall(b"HEAD / HTTP/1.0\r\nHost: scan\r\nUser-Agent: RedScan\r\n\r\n")
        raw = sock.recv(512)
    except OSError:
        return None
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    cleaned = _BANNER_STRIP.sub("", first).strip()
    return cleaned[:96] or None


_REFUSED = (10061, 111, 61)  # ECONNREFUSED on Windows, Linux and macOS

# SIO_TCP_INITIAL_RTO, with TCP_INITIAL_RTO_PARAMETERS set to "leave the RTT alone,
# send no SYN retransmissions". The struct is four bytes once padded.
_SIO_TCP_INITIAL_RTO = 0x98000011
_NO_SYN_RETRIES = struct.pack("<HBx", 0xFFFF, 0xFE)


def _disable_syn_retries(sock: socket.socket) -> None:
    """Make Windows report a refused connection the moment it is refused.

    Windows answers an RST by trying again, twice, half a second apart, so a closed
    port takes two seconds to say no. Any shorter timeout, the default included,
    called every closed port "filtered". A silent host still times out exactly as
    before, and a lost SYN would not have been retried inside the default timeout
    anyway. Best effort: if the call is unsupported we just wait like everyone else.
    """
    try:
        wsa_ioctl, returned = _wsa_ioctl()
        wsa_ioctl(sock.fileno(), _SIO_TCP_INITIAL_RTO, _NO_SYN_RETRIES, len(_NO_SYN_RETRIES),
                  None, 0, returned(), None, None)
    except (ImportError, AttributeError, OSError):
        pass


@lru_cache(maxsize=1)
def _wsa_ioctl():
    """WSAIoctl from Winsock, and a maker for the out-parameter it insists on."""
    import ctypes
    from ctypes import wintypes

    function = ctypes.WinDLL("ws2_32").WSAIoctl
    function.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                         ctypes.c_void_p, ctypes.c_void_p]
    return function, lambda: ctypes.byref(wintypes.DWORD())


def _tcp_socket(timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if IS_WINDOWS:
        _disable_syn_retries(sock)
    sock.settimeout(timeout)
    return sock


def probe_port(ip: str, port: int, timeout: float = 0.6,
               grab: bool = True) -> PortResult:
    """Connect to one TCP port and classify the result."""
    sock = _tcp_socket(timeout)
    started = time.perf_counter()
    try:
        code = sock.connect_ex((ip, port))
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        if code == 0:
            banner = _grab_banner(sock, port) if grab else None
            return PortResult(port, "open", service_name(port), elapsed, banner)
        # ECONNREFUSED means something is home and said no; the rest is silence.
        # Only open ports ever show a service name, and asking the OS for one costs more
        # than the probe itself on Windows, so the rest just get the built-in table.
        return PortResult(port, "closed" if code in _REFUSED else "filtered",
                          SERVICES.get(port, "unknown"), elapsed)
    except OSError:
        return PortResult(port, "filtered", SERVICES.get(port, "unknown"))
    finally:
        try:
            sock.close()
        except OSError:
            pass


def scan_ports(ip: str, ports: Iterable[int], workers: int = 400,
               timeout: float = 0.6, grab: bool = True) -> Iterator[PortResult]:
    """Scan ports concurrently, yielding each result the moment it lands."""
    ports = list(ports)
    if not ports:
        return
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(ports))))
    try:
        futures = [pool.submit(probe_port, ip, p, timeout, grab) for p in ports]
        for future in as_completed(futures):
            yield future.result()
    finally:
        # Abandoned early, drop the queue. Waiting for it here is what used to leave
        # Ctrl+C sitting for minutes on a full sweep, finishing the scan it was stopping.
        pool.shutdown(wait=False, cancel_futures=True)


# --- Service inspection -------------------------------------------------------
#
# Scanning only connects and listens. This part talks, so it sticks to ports
# where a TLS hello or an HTTP request is what the service expects: a raw print
# server on 9100 would put our handshake on paper.

def _clean(text: str, limit: int = 96) -> str:
    """Remote text made safe to print: no control characters, one line, bounded."""
    return " ".join(_BANNER_STRIP.sub("", text).split())[:limit]


@functools.cache
def _tls_context(kind: str) -> ssl.SSLContext:
    """A client context that will talk to anything, however old.

    ``any`` and ``legacy`` only observe; ``trust`` also verifies the chain and
    ``name`` the hostname on top of that.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.set_ciphers("DEFAULT:@SECLEVEL=0")   # OpenSSL 3 refuses TLS 1.0/1.1 above level 0
    with warnings.catch_warnings():          # Python deprecates the versions we came to find
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        if kind == "legacy":
            ctx.maximum_version = ssl.TLSVersion.TLSv1_1
    ctx.check_hostname = kind == "name"
    if kind in ("trust", "name"):
        ctx.load_default_certs()
    else:
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _tls_connect(ip: str, port: int, kind: str, hostname: str | None,
                 timeout: float) -> ssl.SSLSocket:
    """Connect and handshake. The timeout bounds each step, not each packet."""
    context = _tls_context(kind)             # before the socket: a failure here leaks nothing
    return context.wrap_socket(socket.create_connection((ip, port), timeout=timeout),
                               server_hostname=hostname)


def _decode_cert(der: bytes) -> dict:
    """Parse a DER certificate with the interpreter's own OpenSSL."""
    # shortcut: ssl._ssl._test_decode_cert is private CPython API and only reads
    # files; switch to the `cryptography` package if a future Python drops it.
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(ssl.DER_cert_to_PEM_cert(der))
        return ssl._ssl._test_decode_cert(path)
    except Exception:                        # no decoder or a mangled cert: carry on without
        return {}
    finally:
        os.unlink(path)


def _cert_name(rdns: tuple | None, *prefer: str) -> str | None:
    """The first of the ``prefer`` attributes present in an X.509 name."""
    fields = {key: value for rdn in rdns or () for key, value in rdn}
    name = next((fields[key] for key in prefer if fields.get(key)), None)
    return _clean(name) if name else None


def _tls_facts(tls: ssl.SSLSocket) -> dict:
    """Protocol, cipher and certificate details from a completed handshake."""
    version = tls.version()
    cipher, _, bits = tls.cipher()
    # The parsed certificate is only handed over after a verified handshake.
    cert = tls.getpeercert() or _decode_cert(tls.getpeercert(True) or b"")
    facts = {
        "version": version,
        "cipher": cipher,
        "bits": bits,
        # Newer certificates carry no common name at all, only alternative names.
        "subject": (_cert_name(cert.get("subject"), "commonName", "organizationName")
                    or next((_clean(v) for _, v in cert.get("subjectAltName", ())), None)),
        "issuer": _cert_name(cert.get("issuer"), "organizationName", "commonName"),
        "expires": None,
        "days_left": None,
        "trusted": True,        # inspect_port overrules this when verification fails
        "trust_error": None,
        "verify_code": None,    # OpenSSL X509_V_ERR_* number behind trust_error
        "legacy": version if version in LEGACY_TLS else None,
    }
    with contextlib.suppress(KeyError, ValueError, OverflowError):
        expires = ssl.cert_time_to_seconds(cert["notAfter"])
        # Not time.gmtime: Windows gives up on dates past the year 3000.
        facts["expires"] = (datetime(1970, 1, 1) + timedelta(seconds=expires)).date().isoformat()
        facts["days_left"] = int((expires - time.time()) // 86400)
    return facts


_HTTP_KEPT = ("server", "x-powered-by", "location", "strict-transport-security",
              "content-security-policy", "x-content-type-options", "x-frame-options")


def http_probe(sock: socket.socket, host: str, timeout: float = 3.0) -> dict | None:
    """``HEAD /`` on a connected socket: the status and the headers worth judging."""
    deadline = time.monotonic() + timeout
    raw = b""
    try:
        sock.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: RedScan\r\n"
                     "Accept: */*\r\nConnection: close\r\n\r\n".encode("ascii", "replace"))
        # Stop at the end of the headers, 16 KB, the deadline or the first byte
        # that is not HTTP - whichever a slow or chatty server reaches first.
        while (b"\r\n\r\n" not in raw and len(raw) < 16384
               and b"HTTP/".startswith(raw[:5]) and time.monotonic() < deadline):
            sock.settimeout(max(deadline - time.monotonic(), 0.01))
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    except OSError:
        pass                                 # judge whatever arrived before it went wrong
    head = raw.partition(b"\r\n\r\n")[0].decode("latin-1")
    status = re.match(r"HTTP/\d\.\d (\d{3})", head)
    if not status:
        return None
    headers = email.message_from_string(head.partition("\r\n")[2])
    return {
        "status": int(status[1]),
        "headers": {name: _clean(headers[name], 200) for name in _HTTP_KEPT if headers[name]},
    }


def inspect_port(ip: str, result: PortResult, hostname: str | None = None,
                 timeout: float = 3.0) -> None:
    """Fill in ``result.tls`` and ``result.http`` for one open port."""
    host = hostname or ip
    verdict: dict = {}
    # Handshake the way a careful client would. Only when the certificate fails
    # that do we come back without verification to see what was presented.
    for kind in ("name" if hostname else "trust", "any"):
        try:
            with _tls_connect(ip, result.port, kind, hostname, timeout) as tls:
                result.tls = _tls_facts(tls) | verdict
                result.http = http_probe(tls, host, timeout)
            break
        except ssl.SSLCertVerificationError as exc:
            verdict = {"trusted": False, "trust_error": exc.verify_message,
                       "verify_code": exc.verify_code}
        except (OSError, ValueError):        # no TLS here; it may still be plain HTTP
            if not verdict and (result.port in HTTP_PORTS
                                or (result.banner or "").startswith("HTTP/")):
                with contextlib.suppress(OSError):
                    with socket.create_connection((ip, result.port), timeout=timeout) as sock:
                        result.http = http_probe(sock, host, timeout)
            return

    # And does it still shake hands when offered nothing newer than TLS 1.1?
    if result.tls and not result.tls["legacy"]:
        with contextlib.suppress(OSError, ValueError):
            with _tls_connect(ip, result.port, "legacy", hostname, timeout) as old:
                result.tls["legacy"] = old.version()


def inspect_ports(target: Target, results: Iterable[PortResult],
                  timeout: float = 3.0) -> None:
    """Inspect, concurrently, every open port we know how to talk to."""
    speakable = [r for r in results if r.is_open and (
        r.port in HTTP_PORTS or r.port in TLS_PORTS
        or (r.banner or "").startswith("HTTP/"))]
    if not speakable:
        return
    with contextlib.suppress(ValueError):    # warm the cache here: catch_warnings is not thread-safe
        for kind in ("any", "trust", "name", "legacy"):
            _tls_context(kind)
    with ThreadPoolExecutor(max_workers=min(16, len(speakable))) as pool:
        try:
            list(pool.map(lambda r: inspect_port(target.ip, r, target.hostname, timeout),
                          speakable))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


def parse_ports(spec: str) -> list[int]:
    """Parse '22,80,1000-2000,top' into a sorted list of unique ports."""
    spec = (spec or "").strip().lower()
    if not spec or spec in {"top", "default"}:
        return list(TOP_PORTS)
    if spec in {"full", "all"}:
        return list(range(1, 65536))
    found: set[int] = set()
    for chunk in re.split(r"[,\s]+", spec):
        if not chunk:
            continue
        if chunk in {"top", "default"}:
            found.update(TOP_PORTS)
            continue
        if chunk in {"full", "all"}:
            found.update(range(1, 65536))
            continue
        if "-" in chunk:
            lo_s, _, hi_s = chunk.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError as exc:
                raise ValueError(f"bad port range: {chunk!r}") from exc
            if lo > hi:
                lo, hi = hi, lo
            if not (1 <= lo <= 65535 and 1 <= hi <= 65535):
                raise ValueError(f"ports must be 1-65535: {chunk!r}")
            found.update(range(lo, hi + 1))
        else:
            try:
                port = int(chunk)
            except ValueError as exc:
                raise ValueError(f"bad port: {chunk!r}") from exc
            if not 1 <= port <= 65535:
                raise ValueError(f"ports must be 1-65535: {port}")
            found.add(port)
    return sorted(found)


def guess_os(ttl: int | None) -> str:
    """Crude OS hint from the reply TTL - fun, not forensics.

    Hosts start a packet at 64, 128 or 255 and every router knocks one off, so
    the nearest starting value above the TTL we saw gives both a family guess
    and a hop count.
    """
    if ttl is None:
        return "unknown"
    for initial, family in ((64, "Linux / Unix"), (128, "Windows"),
                            (255, "network gear")):
        if ttl <= initial:
            hops = initial - ttl
            return f"{family}, ~{hops} hop{'' if hops == 1 else 's'} away"
    return "unknown"


def local_ip() -> str:
    """The LAN address this machine would use to reach the outside world."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
