"""The engine: name resolution, ICMP/TCP pinging and threaded port scanning."""

from __future__ import annotations

import ipaddress
import platform
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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

    target = Target(raw=host, ip=ip, hostname=None if host == ip else host)
    try:
        socket.setdefaulttimeout(1.5)
        target.reverse = socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror):
        target.reverse = None
    finally:
        socket.setdefaulttimeout(None)
    return target


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


_WIN_TIME = re.compile(r"time[=<]\s*(\d+)\s*ms", re.IGNORECASE)
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
            timeout=max(timeout_ms / 1000 + 2, 3),
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return Pong(seq, None, error="timed out")
    except (OSError, ValueError) as exc:
        return Pong(seq, None, error=str(exc))

    out = f"{proc.stdout}\n{proc.stderr}"
    lowered = out.lower()
    ttl_match = _TTL.search(out)
    ttl = int(ttl_match.group(1)) if ttl_match else None

    if proc.returncode != 0 or "unreachable" in lowered or "100% packet loss" in lowered:
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
    started = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
    except OSError as exc:
        return Pong(seq, None, error=getattr(exc, "strerror", None) or "closed")
    return Pong(seq, round((time.perf_counter() - started) * 1000, 2))


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


# --- Port scanning ------------------------------------------------------------

@dataclass(slots=True)
class PortResult:
    """The outcome of one TCP connect attempt."""

    port: int
    state: str              # open | closed | filtered
    service: str
    latency_ms: float | None = None
    banner: str | None = None

    @property
    def is_open(self) -> bool:
        return self.state == "open"


_BANNER_STRIP = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


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


def probe_port(ip: str, port: int, timeout: float = 0.6,
               grab: bool = True) -> PortResult:
    """Connect to one TCP port and classify the result."""
    name = service_name(port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    started = time.perf_counter()
    try:
        code = sock.connect_ex((ip, port))
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        if code == 0:
            banner = _grab_banner(sock, port) if grab else None
            return PortResult(port, "open", name, elapsed, banner)
        # ECONNREFUSED means something is home and said no; the rest is silence.
        refused = code in (10061, 111, 61)
        return PortResult(port, "closed" if refused else "filtered", name, elapsed)
    except OSError:
        return PortResult(port, "filtered", name)
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
    pool_size = max(1, min(workers, len(ports)))
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = [pool.submit(probe_port, ip, p, timeout, grab) for p in ports]
        for future in as_completed(futures):
            yield future.result()


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
    """Crude OS hint from the reply TTL - fun, not forensics."""
    if ttl is None:
        return "unknown"
    if ttl > 200:
        return "Cisco / network gear"
    if ttl > 100:
        return "Windows (TTL 128)"
    if ttl > 50:
        return "Linux / Unix (TTL 64)"
    return "many hops away"


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
