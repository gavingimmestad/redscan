<h1 align="center">RedScan</h1>

<p align="center">
  <strong>An unreasonably good looking port scanner and IP pinger for the terminal.</strong><br>
  Live radar sweep, ember-gradient progress bars, banner grabbing, latency sparklines.
</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-1.0.0-E8102B?style=for-the-badge&labelColor=2B0509">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-C81E3A?style=for-the-badge&labelColor=2B0509">
  <img alt="platform" src="https://img.shields.io/badge/windows%20%7C%20linux%20%7C%20macos-FF3B2E?style=for-the-badge&labelColor=2B0509">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-8B0000?style=for-the-badge&labelColor=2B0509">
</p>

```
██████╗ ███████╗██████╗ ███████╗ ██████╗ █████╗ ███╗   ██╗
██╔══██╗██╔════╝██╔══██╗██╔════╝██╔════╝██╔══██╗████╗  ██║
██████╔╝█████╗  ██║  ██║███████╗██║     ███████║██╔██╗ ██║
██╔══██╗██╔══╝  ██║  ██║╚════██║██║     ██╔══██║██║╚██╗██║
██║  ██║███████╗██████╔╝███████║╚██████╗██║  ██║██║ ╚████║
╚═╝  ╚═╝╚══════╝╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝
         PORT SCANNER  ::  IP PINGER  ::  RECON
```

---

## Why

Because `nmap` output is grey, and a tool you look at for ten minutes at a time
should be worth looking at. RedScan does the same honest work — TCP connect
scanning, ICMP latency, service fingerprints — behind a dashboard that updates
live in a single shade of angry red.

## Features

| | |
|---|---|
| **Live scan dashboard** | Radar sweep header, ember-gradient progress bar, a coverage strip that fills as the port list burns down, and open ports that appear the instant they answer |
| **88 curated ports** | The ports that actually tell you something, named — `ssh`, `smb`, `redis`, `mongodb`, `plex`, `rabbitmq-ui` and friends |
| **Full 65,535 sweep** | 400 concurrent socket workers by default, thousands of ports per second on a LAN |
| **Banner grabbing** | Service fingerprints pulled straight off the socket, with a real `HEAD` request on HTTP-ish ports |
| **Ping monitor** | Live ICMP latency with per-reply heat bars, a history sparkline, loss, jitter and a TTL-based OS guess |
| **TCP ping** | Latency to a single port, for the many networks where ICMP is dropped on the floor |
| **No admin needed** | No raw sockets anywhere — ICMP goes through the system `ping`, everything else is a plain TCP connect |
| **Exports** | `--json` and `--csv` for when the pretty version has to become a ticket |
| **Interactive menu** | Double-click the `.exe` and it asks what you want instead of printing usage and vanishing |

## Install

### Grab the executable

Download `RedScan.exe` from the [latest release](../../releases/latest) and run
it. No Python, no dependencies, nothing to install.

### Run from source

```bash
git clone https://github.com/gavingimmestad/redscan.git
cd redscan
pip install -r requirements.txt
python -m redscan
```

## Usage

```bash
redscan                             # interactive menu
redscan 192.168.1.1                 # scan the 88 curated ports
redscan example.com -p 1-1024       # scan a range
redscan 10.0.0.5 -p full            # all 65,535 ports
redscan 8.8.8.8 --ping-only -c 0    # continuous ping monitor, Ctrl+C to stop
redscan router.lan -P -p top        # ping first, then scan
redscan 10.0.0.5 --tcp-ping 443     # latency to one port, no ICMP
redscan scanme.nmap.org --json out.json --csv ports.csv
redscan --net-info                  # this machine's addresses
```

### Options

| Flag | Meaning |
|---|---|
| `-p, --ports` | `top`, `full`, `22,80,443`, `1-1024`, or any mix |
| `-P, --ping` | Ping the target before scanning |
| `--ping-only` | Ping and skip the scan |
| `-c, --count` | Ping count, `0` for continuous (default `4`) |
| `-i, --interval` | Seconds between pings (default `1.0`) |
| `--tcp-ping PORT` | Measure latency to a TCP port instead of ICMP |
| `-w, --workers` | Concurrent socket workers (default `400`) |
| `-t, --timeout` | Per-port connect timeout in seconds (default `0.6`) |
| `--timeout-ms` | Ping timeout in milliseconds (default `1200`) |
| `--no-banner` | Skip banner grabbing (faster, quieter) |
| `--no-anim` | Skip the startup animation |
| `--json`, `--csv` | Write results to disk |
| `--net-info` | Show local addresses and exit |

### Reading the results

| State | Meaning |
|---|---|
| `OPEN` | The TCP handshake completed |
| `closed` | The host actively refused — something is listening on that address, just not on that port |
| `filtered` | Silence until timeout, which usually means a firewall dropped the packet |

## Build the executable yourself

```powershell
pip install -r requirements-dev.txt
python tools/make_icon.py      # regenerate the radar icon (optional)
./build.ps1
```

The one-file binary lands in `dist/RedScan.exe`, icon and all. On Linux or macOS:

```bash
pip install -r requirements-dev.txt
pyinstaller --onefile --name RedScan --console run_redscan.py
```

## Layout

```
redscan/
├── redscan/
│   ├── theme.py     red palette, gradient maths, Rich theme
│   ├── banner.py    the oversized logo and the radar sweep
│   ├── net.py       resolution, ICMP/TCP ping, threaded scanning
│   ├── ui.py        every panel, table, sparkline and stat tile
│   └── cli.py       arguments, interactive menu, exports
├── tools/
│   └── make_icon.py renders assets/redscan.ico with no image libraries
├── run_redscan.py   PyInstaller entry point
└── build.ps1        one-file Windows build
```

## Scan only what you are allowed to scan

Port scanning hosts you do not own or have written permission to test is, in a
lot of places, illegal, and in most of the rest it is a quick way to get your
address blocked. Point RedScan at your own machines, your own network, or a host
that explicitly invites it such as `scanme.nmap.org`.

## License

MIT — see [LICENSE](LICENSE).
