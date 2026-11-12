"""The security audit: TLS and HTTP inspection, exposure rules, SARIF, CI gating."""

import contextlib
import io
import json
import socketserver
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from rich.console import Console

from redscan import audit, cli, net, ui
from redscan.net import PortResult, Target
from redscan.theme import THEME

CERT = Path(__file__).with_name("selfsigned.pem")     # CN=redscan.test, valid until 2126
LAN = Target(raw="127.0.0.1", ip="127.0.0.1")
WAN = Target(raw="8.8.8.8", ip="8.8.8.8")
# A careless web server, with an escape sequence and a C1 control in its banner.
RESPONSE = b"HTTP/1.1 200 OK\r\nServer: Apache/2.4.1 \x1b[31mevil\x9b\r\nContent-Length: 0\r\n\r\n"
TLS_OK = {"version": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384", "bits": 256,
          "subject": "example.test", "issuer": "Some CA", "expires": "2030-01-01",
          "days_left": 900, "trusted": True, "trust_error": None, "verify_code": None,
          "legacy": None}
ALL_HEADERS = {"strict-transport-security": "max-age=63072000",
               "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
               "x-content-type-options": "nosniff"}


class Handler(socketserver.BaseRequestHandler):
    """Answers HEAD and hangs up on anything else, such as a TLS hello sent in the clear."""

    def handle(self):
        with contextlib.suppress(OSError):
            if self.request.recv(4096).startswith(b"HEAD "):
                self.request.sendall(RESPONSE)


def ids(target: Target, *results: PortResult) -> list[str]:
    return [finding.id for finding in audit.analyze(target, results)]


def web(port: int, status: int, headers: dict, tls: dict | None = None) -> PortResult:
    return PortResult(port, "open", "http", tls=tls, http={"status": status, "headers": headers})


class AuditTests(unittest.TestCase):
    def serve(self, tls: bool) -> int:
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(CERT)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_severity_follows_the_network(self):
        telnet, ssh = PortResult(23, "open", "telnet"), PortResult(22, "open", "ssh")
        lan, wan = audit.analyze(LAN, [ssh, telnet]), audit.analyze(WAN, [ssh, telnet])
        self.assertEqual([(f.port, f.severity) for f in lan], [(23, "high"), (22, "info")])
        self.assertEqual([(f.port, f.severity) for f in wan], [(23, "critical"), (22, "low")])
        self.assertEqual((lan[0].attack, lan[0].cwe), ("T1040", "CWE-319"))
        self.assertEqual(ids(LAN, PortResult(23, "closed", "telnet")), [])

    def test_inspects_a_live_tls_service(self):
        result = PortResult(self.serve(tls=True), "open", "https")
        net.inspect_port("127.0.0.1", result)
        tls = result.tls
        self.assertIn(tls["version"], ("TLSv1.2", "TLSv1.3"))
        self.assertEqual((tls["subject"], tls["issuer"]), ("redscan.test", "RedScan Test Fixture"))
        self.assertEqual((tls["trusted"], tls["verify_code"], tls["legacy"]), (False, 18, None))
        self.assertGreater(tls["days_left"], 0)
        self.assertEqual(result.http["status"], 200)
        # Escape and C1 bytes from the server never reach the terminal.
        self.assertEqual(result.http["headers"]["server"], "Apache/2.4.1 [31mevil")
        found = {f.id: f for f in audit.analyze(LAN, [result])}
        self.assertEqual(set(found), {"tls-self-signed", "http-missing-headers",
                                      "http-version-disclosure"})
        self.assertIn("Strict-Transport-Security", found["http-missing-headers"].detail)

    def test_plain_http_falls_back_from_tls(self):
        result = PortResult(self.serve(tls=False), "open", "http", banner="HTTP/1.1 200 OK")
        net.inspect_ports(LAN, [result])
        self.assertIsNone(result.tls)
        self.assertEqual(result.http["status"], 200)
        self.assertIn("http-cleartext", ids(LAN, result))

    def test_only_talks_to_ports_that_expect_it(self):
        quiet = [PortResult(9100, "open", "jetdirect"),                 # would print our hello
                 PortResult(22, "open", "ssh", banner="SSH-2.0-OpenSSH_9.6"),
                 PortResult(443, "closed", "https")]
        with mock.patch.object(net, "inspect_port") as inspect:
            net.inspect_ports(LAN, quiet)
            inspect.assert_not_called()
            net.inspect_ports(LAN, quiet + [PortResult(8443, "open", "https-alt")])
            self.assertEqual(inspect.call_count, 1)

    def test_http_rules(self):
        self.assertEqual(ids(LAN, web(8080, 200, {})),
                         ["http-cleartext", "http-missing-headers"])
        self.assertNotIn("Strict-Transport-Security",                   # HSTS is HTTPS-only
                         audit.analyze(LAN, [web(8080, 200, {})])[1].detail)
        self.assertEqual(ids(LAN, web(8080, 301, {"location": "https://example.test/"})), [])
        self.assertEqual(ids(LAN, web(8080, 400, {})), [])
        self.assertEqual(ids(LAN, web(8443, 200, ALL_HEADERS, TLS_OK)), [])
        self.assertEqual(ids(LAN, web(8443, 200, ALL_HEADERS | {"x-powered-by": "PHP/8.1"},
                                      TLS_OK)), ["http-version-disclosure"])

    def test_certificate_rules(self):
        def tls_ids(**changes):
            return ids(WAN, PortResult(8443, "open", "https-alt", tls=TLS_OK | changes))

        self.assertEqual(tls_ids(), [])
        self.assertEqual(tls_ids(days_left=-5, trusted=False, verify_code=10), ["tls-expired"])
        self.assertEqual(tls_ids(days_left=10), ["tls-expiring"])
        self.assertEqual(tls_ids(trusted=False, verify_code=62), ["tls-name-mismatch"])
        self.assertEqual(tls_ids(trusted=False, verify_code=20,
                                 trust_error="unable to get local issuer certificate"),
                         ["tls-untrusted"])
        self.assertEqual(tls_ids(legacy="TLSv1"), ["tls-deprecated-protocol"])

    def test_remote_text_is_not_rich_markup(self):
        result = web(8080, 200, {"server": "evil/1.0 [/bogus] [bold]x"})
        out = io.StringIO()
        Console(theme=THEME, file=out, width=160).print(
            ui.audit_panel([result], audit.analyze(LAN, [result])))
        self.assertIn("evil/1.0 [/bogus] [bold]x", out.getvalue())

    def test_exports_carry_findings(self):
        view = ui.ScanView(target=WAN, total=1)
        view.record(PortResult(23, "open", "telnet"))
        findings = audit.analyze(WAN, view.open_ports)
        with tempfile.TemporaryDirectory() as tmp:
            cli.export_results(Console(file=io.StringIO()), WAN, view,
                               f"{tmp}/out.json", None, f"{tmp}/out.sarif", findings)
            report = json.loads(Path(tmp, "out.json").read_text(encoding="utf-8"))
            sarif = json.loads(Path(tmp, "out.sarif").read_text(encoding="utf-8"))
        self.assertEqual(report["findings"][0]["id"], "exposed-telnet")
        run = sarif["runs"][0]
        rule, result = run["tool"]["driver"]["rules"][0], run["results"][0]
        self.assertEqual((sarif["version"], result["ruleId"], result["level"]),
                         ("2.1.0", "exposed-telnet", "error"))
        self.assertEqual(rule["properties"]["security-severity"], "9.5")
        self.assertEqual(rule["properties"]["tags"],
                         ["security", "external/cwe/cwe-319", "attack.t1040"])
        self.assertIn("8.8.8.8:23", result["message"]["text"])

    def test_fail_on_gates_the_exit_code(self):
        port = self.serve(tls=False)

        def run(*flags: str) -> int:
            console = Console(file=io.StringIO(), theme=THEME, width=120)
            with mock.patch.object(cli, "build_console", return_value=console), \
                    mock.patch.object(net, "HTTP_PORTS", {port}):
                return cli.main(["127.0.0.1", "-p", str(port), "--no-anim", *flags])

        self.assertEqual(run("--fail-on", "low"), 1)       # cleartext HTTP is low on a LAN
        self.assertEqual(run("--fail-on", "high"), 0)
        self.assertEqual(run(), 0)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run("--no-audit", "--fail-on", "low")          # a gate with nothing behind it


if __name__ == "__main__":
    unittest.main()
