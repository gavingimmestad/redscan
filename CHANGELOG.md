# Changelog

## 1.2.0 - 2026-11-12

### Added
- **Security audit** after every scan: 28 rules over 41 ports, each finding with a
  severity, a MITRE ATT&CK technique, a CWE and a fix. Severity depends on whether
  the target is on a private or a public address. `--no-audit` turns it off.
- **TLS inspection**: protocol, cipher, certificate subject, issuer and expiry,
  chain trust, hostname match, and whether TLS 1.0/1.1 is still accepted.
- **HTTP security-header audit**: missing HSTS, CSP, `X-Content-Type-Options` and
  clickjacking protection, cleartext HTTP with no redirect, version-leaking headers.
- `--sarif FILE` writes findings as SARIF 2.1.0.
- `--fail-on SEVERITY` exits 1 when a finding is that severe or worse, for CI.
- `--json` output now includes findings and TLS/HTTP details.

## 1.1.0

### Added
- Six colour palettes, five of them animated: `--palette`, `--trip`,
  `--list-palettes`, and a repaint entry in the interactive menu.
- An offline alarm in the ping monitor: a detonation animation when a target that
  was answering goes dark. `--nuke` previews it.

### Fixed
- Service banners can no longer crash the output or steer the terminal.
- Ctrl+C works everywhere and keeps what the scan had found.
- Closed ports are reported as closed on Windows, and much faster.
- Failed exports are reported instead of crashing; CSV cells can no longer be formulas.
- The menu no longer spins forever on closed input; impossible flag values are refused.
- Ping only trusts real echo replies, in any system language.
- Survives the terminal being resized to almost nothing.

## 1.0.0

- First release: TCP port scanner and IP pinger with a Rich terminal UI,
  JSON/CSV export and a standalone Windows exe.
