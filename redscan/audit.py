"""Turns open ports into findings: what is exposed, why it matters, how to fix it.

Severity depends on where the target sits - SMB on a LAN is routine, SMB on the
internet is an incident - so every rule carries a rating for each. Port rules
go by the service that normally lives on a port; they flag exposure, they do
not prove a vulnerability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Iterator

from .net import PortResult, Target

SEVERITIES = ("info", "low", "medium", "high", "critical")   # mildest first


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth fixing, pinned to a port."""

    id: str
    severity: str
    port: int
    title: str
    detail: str
    fix: str
    attack: str | None = None    # MITRE ATT&CK technique
    cwe: str | None = None


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    title: str
    lan: str                     # severity on a private address
    wan: str                     # severity on a public one
    fix: str
    attack: str | None = None
    cwe: str | None = None
    why: str = ""

    def at(self, port: int, public: bool, detail: str = "") -> Finding:
        return Finding(self.id, self.wan if public else self.lan, port, self.title,
                       detail or self.why, self.fix, self.attack, self.cwe)


# --- Exposure: what a port being reachable at all says ------------------------

_EXPOSED: dict[tuple[int, ...], Rule] = {
    (23,): Rule(
        "exposed-telnet", "Telnet exposed", "high", "critical",
        "Disable Telnet and use SSH.", "T1040", "CWE-319",
        "Logins and sessions cross the network in cleartext."),
    (21,): Rule(
        "exposed-ftp", "FTP exposed", "medium", "high",
        "Move to SFTP or FTPS and disable anonymous logins.", "T1040", "CWE-319",
        "Credentials and files are transferred in cleartext."),
    (110, 143): Rule(
        "cleartext-mail", "Cleartext mail retrieval exposed", "low", "medium",
        "Serve mail on 993/995 only, or refuse logins before STARTTLS.",
        "T1040", "CWE-319",
        "POP3 and IMAP on these ports only encrypt if the client insists on STARTTLS."),
    (389,): Rule(
        "exposed-ldap", "LDAP exposed", "low", "high",
        "Require LDAPS or StartTLS plus LDAP signing; keep it off the internet.",
        "T1040", "CWE-319",
        "Simple binds on 389 send directory passwords in cleartext."),
    (139, 445): Rule(
        "exposed-smb", "SMB file sharing exposed", "low", "critical",
        "Block 139/445 at the perimeter, disable SMBv1 and require SMB signing.",
        "T1021.002", "CWE-284",
        "The classic path for ransomware spread, relay attacks and worms such as WannaCry."),
    (135,): Rule(
        "exposed-msrpc", "Windows RPC endpoint mapper exposed", "low", "high",
        "Limit 135 and the dynamic RPC range to management hosts.",
        "T1021.003", "CWE-284",
        "It maps every RPC service on the host and fronts remote DCOM and WMI execution."),
    (3389,): Rule(
        "exposed-rdp", "Remote Desktop exposed", "low", "high",
        "Put RDP behind a VPN or gateway and require NLA plus MFA.",
        "T1021.001", "CWE-284",
        "A top target for credential stuffing and pre-auth exploits such as BlueKeep."),
    (5900,): Rule(
        "exposed-vnc", "VNC remote desktop exposed", "medium", "high",
        "Tunnel VNC over SSH or a VPN and set a strong password.",
        "T1021.005", "CWE-284",
        "VNC authentication is weak and many builds send the session unencrypted."),
    (5985, 5986): Rule(
        "exposed-winrm", "Windows Remote Management exposed", "low", "high",
        "Restrict WinRM to management subnets and prefer HTTPS on 5986.",
        "T1021.006", "CWE-284",
        "Remote PowerShell: one valid credential is code execution."),
    (22,): Rule(
        "exposed-ssh", "SSH exposed", "info", "low",
        "Allow keys only, disable root login and restrict source addresses.",
        "T1021.004", None,
        "Reachable SSH attracts constant password guessing."),
    (1433, 1521, 3306, 5432): Rule(
        "exposed-database", "Database listener exposed", "low", "high",
        "Bind to a private interface and firewall the port to the app servers.",
        "T1190", "CWE-284",
        "Databases should answer their application tier, not the whole network."),
    (1883, 2181, 6379, 9092, 9200, 9300, 11211, 27017, 27018): Rule(
        "unauthenticated-datastore", "Data service that often runs without authentication",
        "medium", "critical",
        "Bind to a private interface, then turn on authentication and TLS.",
        "T1190", "CWE-306",
        "Redis, MongoDB, Elasticsearch, Memcached and their kin are routinely "
        "deployed with no login at all."),
    (2375,): Rule(
        "exposed-docker-api", "Docker API exposed without TLS", "high", "critical",
        "Close 2375; use the local socket, or mutual TLS on 2376.",
        "T1610", "CWE-306",
        "Whoever reaches this port can start privileged containers and take over the host."),
    (5555,): Rule(
        "exposed-adb", "Android Debug Bridge exposed", "medium", "high",
        "Turn off network debugging on the device.", "T1133", "CWE-306",
        "ADB over TCP gives shell access and is unauthenticated on many devices."),
    (6000,): Rule(
        "exposed-x11", "X11 display exposed", "medium", "high",
        "Start X with -nolisten tcp and use SSH X forwarding.",
        "T1056.001", "CWE-284",
        "An open X server lets remote clients capture keystrokes and the screen."),
    (2049,): Rule(
        "exposed-nfs", "NFS exposed", "low", "high",
        "Export to named hosts only and require Kerberos (sec=krb5p).",
        "T1039", "CWE-284",
        "Exports trust client addresses, so whoever reaches them may be able to mount them."),
    (1080, 3128): Rule(
        "exposed-proxy", "Proxy service exposed", "low", "high",
        "Require authentication and restrict which clients may connect.",
        "T1090", "CWE-441",
        "An open proxy lets strangers route traffic through this host and hide behind it."),
    (2082, 2083, 2086, 5601, 7001, 9090, 10000, 15672): Rule(
        "exposed-admin-console", "Administrative console exposed", "low", "high",
        "Restrict it to an admin network or VPN and enforce MFA.",
        "T1190", "CWE-284",
        "Management interfaces are prime targets for default credentials and known exploits."),
    (1723,): Rule(
        "exposed-pptp", "PPTP VPN exposed", "medium", "high",
        "Migrate to WireGuard, IKEv2 or OpenVPN.", "T1133", "CWE-327",
        "PPTP's MS-CHAPv2 handshake can be cracked offline; the protocol is broken."),
}
PORT_RULES: dict[int, Rule] = {port: rule for ports, rule in _EXPOSED.items()
                               for port in ports}

# --- TLS: what the handshake and the certificate say --------------------------

TLS_EXPIRED = Rule(
    "tls-expired", "TLS certificate has expired", "medium", "high",
    "Renew the certificate and automate renewal (ACME).", "T1557", "CWE-324")
TLS_EXPIRING = Rule(
    "tls-expiring", "TLS certificate expires soon", "low", "low",
    "Renew the certificate and automate renewal (ACME).")
TLS_SELF_SIGNED = Rule(
    "tls-self-signed", "Self-signed TLS certificate", "low", "medium",
    "Serve a certificate issued by a CA your clients already trust.", "T1557", "CWE-295")
TLS_UNTRUSTED = Rule(
    "tls-untrusted", "TLS certificate chain is not trusted", "low", "medium",
    "Serve the full chain from a CA your clients trust.", "T1557", "CWE-295")
TLS_MISMATCH = Rule(
    "tls-name-mismatch", "TLS certificate does not match the hostname", "low", "medium",
    "Reissue the certificate with this hostname among its alternative names.",
    "T1557", "CWE-297")
TLS_LEGACY = Rule(
    "tls-deprecated-protocol", "Deprecated TLS protocol accepted", "low", "medium",
    "Disable SSLv3, TLS 1.0 and TLS 1.1 (RFC 8996); serve TLS 1.2 or newer.",
    "T1557", "CWE-327")

# OpenSSL X509_V_ERR_* codes, as reported by ssl.SSLCertVerificationError.
_CERT_EXPIRED, _SELF_SIGNED, _NAME_MISMATCH = 10, (18, 19), 62
EXPIRY_WARNING_DAYS = 30


def _tls_findings(port: int, tls: dict, public: bool) -> Iterator[Finding]:
    days, code = tls["days_left"], tls["verify_code"]
    if code == _CERT_EXPIRED or (days is not None and days < 0):
        yield TLS_EXPIRED.at(port, public, f"The certificate expired on {tls['expires']}."
                             if tls["expires"] else "The certificate is past its end date.")
    elif days is not None and days <= EXPIRY_WARNING_DAYS:
        yield TLS_EXPIRING.at(port, public,
                              f"The certificate expires in {days} days, on {tls['expires']}.")
    if code in _SELF_SIGNED:
        yield TLS_SELF_SIGNED.at(port, public,
                                 "No public authority vouches for this certificate, so "
                                 "clients cannot tell it from an impostor's.")
    elif code == _NAME_MISMATCH:
        yield TLS_MISMATCH.at(port, public, f"It was issued to {tls['subject'] or 'another name'}.")
    elif not tls["trusted"] and code != _CERT_EXPIRED:
        yield TLS_UNTRUSTED.at(port, public,
                               f"This machine's trust store rejected it: {tls['trust_error']}.")
    if tls["legacy"]:
        yield TLS_LEGACY.at(port, public, f"The server completed a {tls['legacy']} handshake.")


# --- HTTP: what the response headers say --------------------------------------

HTTP_CLEARTEXT = Rule(
    "http-cleartext", "HTTP served without TLS", "low", "medium",
    "Redirect every request to HTTPS and enable HSTS.", "T1040", "CWE-319")
HTTP_HEADERS = Rule(
    "http-missing-headers", "Missing HTTP security headers", "low", "low",
    "Set them at the web server or in the application.", None, "CWE-693")
HTTP_VERSION = Rule(
    "http-version-disclosure", "Web server discloses its version", "info", "low",
    "Strip version details from response headers.", "T1592.002", "CWE-200")


def _http_findings(port: int, http: dict, over_tls: bool, public: bool) -> Iterator[Finding]:
    status, headers = http["status"], http["headers"]
    if status == 400:        # it rejected our request outright; its headers say nothing
        return
    redirect = 300 <= status < 400
    if not over_tls and not (redirect and
                             headers.get("location", "").lower().startswith("https://")):
        yield HTTP_CLEARTEXT.at(port, public, f"Answered {status} over plain HTTP without "
                                              "redirecting to HTTPS.")
    if not redirect:         # a redirect has no page to protect
        csp = headers.get("content-security-policy", "")
        missing = [name for name, present in (
            ("Strict-Transport-Security", not over_tls or "strict-transport-security" in headers),
            ("Content-Security-Policy", bool(csp)),
            ("X-Content-Type-Options", "x-content-type-options" in headers),
            ("X-Frame-Options", "x-frame-options" in headers or "frame-ancestors" in csp.lower()),
        ) if not present]
        if missing:
            yield HTTP_HEADERS.at(port, public, "Not set: " + ", ".join(missing) + ".")
    leaks = [f"{name.title()}: {headers[name]}" for name in ("server", "x-powered-by")
             if re.search(r"\d\.\d", headers.get(name, ""))]
    if leaks:
        yield HTTP_VERSION.at(port, public, "; ".join(leaks))


def analyze(target: Target, results: Iterable[PortResult]) -> list[Finding]:
    """Every finding for a scan, worst first."""
    public = not target.is_private
    findings: list[Finding] = []
    for result in results:
        if not result.is_open:
            continue
        rule = PORT_RULES.get(result.port)
        if rule:
            findings.append(rule.at(result.port, public))
        if result.tls:
            findings.extend(_tls_findings(result.port, result.tls, public))
        if result.http:
            findings.extend(_http_findings(result.port, result.http,
                                           bool(result.tls), public))
    return sorted(findings, key=lambda f: (-SEVERITIES.index(f.severity), f.port))
