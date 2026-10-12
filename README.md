# RTCM / NTRIP Receiver

A small Python project for connecting to an **NTRIP caster**, sending a virtual rover **NMEA GGA** position, receiving **RTCM correction streams**, saving valid RTCM frames to disk, and inspecting reference-station information contained in RTCM messages.

The current project is configured around the **Shamim** NTRIP service and includes a sample Shamim sourcetable.

## Features

- Connect to an NTRIP caster with Basic Authentication
- Generate and periodically send NMEA `GNGGA` messages
- Receive and record RTCM correction data
- Reassemble RTCM 3 frames from a TCP stream
- Validate RTCM 3 frames using CRC-24Q
- Print RTCM message type, station ID, and frame size
- Decode RTCM `1005` and `1006` reference-station messages
- Convert reference-station ECEF coordinates to WGS84 latitude, longitude, and ellipsoidal height
- Calculate the approximate rover-to-reference-station distance
- Save received RTCM data to a `.rtcm` file
- Decode a saved RTCM file to readable text using `pyrtcm`

## Repository Structure

```text
RTCM/
├── rtcm_receiver.py     # NTRIP client and RTCM 3 receiver
├── rtcm_parser.py       # Decode a saved RTCM file using pyrtcm
├── sourcetable.txt      # Example Shamim NTRIP sourcetable
├── *.rtcm               # Recorded RTCM streams
├── *.log                # Example receiver logs
└── README.md
```

## Requirements

- Python 3.9+ recommended
- Internet access to the NTRIP caster
- Valid NTRIP username and password
- `pyrtcm` only if you want to use `rtcm_parser.py`

Install the parser dependency:

```bash
python -m pip install pyrtcm
```

The NTRIP receiver itself uses only Python standard-library modules.

## Quick Start

Clone the repository:

```bash
git clone https://github.com/Mmdbeg/RTCM.git
cd RTCM
```

A safer way to use the receiver is to import `VirtualReceiver` and provide the credentials at runtime instead of storing them in the source code:

```python
import os

from rtcm_receiver import VirtualReceiver

receiver = VirtualReceiver(
    lat=35.7217614,
    lon=51.4418354,
    height=1266.8765,
    geoid=6.0397,
    satellites=12,
    hdop=0.6,
)

username = os.environ["NTRIP_USERNAME"]
password = os.environ["NTRIP_PASSWORD"]

try:
    receiver.connect(
        username=username,
        password=password,
        host="shamim.ssaa.ir",
        port=2101,
        mountpoint="Nearest4",
    )

    receiver.receive(
        output="shamim_capture.rtcm",
        seconds=60,
        gga_interval=5,
        idle_timeout=30,
    )
finally:
    receiver.disconnect()
```

On PowerShell, environment variables can be set with:

```powershell
$env:NTRIP_USERNAME = "YOUR_USERNAME"
$env:NTRIP_PASSWORD = "YOUR_PASSWORD"
python your_script.py
```

## How It Works

```text
Virtual rover coordinates
        |
        v
Generate NMEA GGA
        |
        v
Connect to NTRIP caster
        |
        v
Send GGA periodically
        |
        v
Receive RTCM byte stream
        |
        v
Find RTCM 3 frames
        |
        v
Validate CRC-24Q
        |
        +----> Save frame to .rtcm
        |
        +----> Print message type / station ID
        |
        +----> Decode 1005 / 1006
                    |
                    v
             ECEF X/Y/Z
                    |
                    v
          WGS84 Lat/Lon/Height
                    |
                    v
        Rover-to-base distance
```

## NMEA GGA

`VirtualReceiver.make_gga()` creates a `GNGGA` sentence from the configured rover position.

The generated message includes:

- UTC time
- Latitude
- Longitude
- Fix quality
- Number of satellites
- HDOP
- Antenna height
- Geoid separation
- NMEA checksum

During reception, the GGA message is sent periodically. The default interval is **5 seconds**.

## RTCM Frame Validation

The receiver currently implements **RTCM 3 framing**.

A valid RTCM 3 frame is identified by:

```text
Preamble: 0xD3
Header
Payload
CRC-24Q
```

The receiver searches the TCP stream for the `0xD3` preamble, reads the payload length, reassembles the complete frame, verifies CRC-24Q, discards invalid frames, and writes valid frames to the output file.

## RTCM 1005 / 1006

The receiver contains a dedicated decoder for:

- `1005` — Stationary RTK Reference Station ARP
- `1006` — Stationary RTK Reference Station ARP with antenna height

For these messages it extracts:

```text
Message type
Station ID
ITRF year
ECEF X
ECEF Y
ECEF Z
Antenna height (1006)
Latitude
Longitude
Ellipsoidal height
Distance from rover
```

ECEF coordinates are converted to geodetic coordinates using WGS84.

## Recording RTCM

Example:

```python
receiver.receive(
    output="corrections.rtcm",
    seconds=120,
)
```

If no output filename is supplied, the receiver automatically creates a timestamped filename similar to:

```text
shamim_20261011_100732_177062.rtcm
```

During reception the console prints records similar to:

```text
RTCM 1074 | Station ID: 0047 | 212 bytes
RTCM 1084 | Station ID: 0047 | 71 bytes
RTCM 1094 | Station ID: 0047 | 215 bytes
RTCM 1124 | Station ID: 0047 | 154 bytes
RTCM 1006 | Station ID: 0047 | 27 bytes
```

## Decoding a Recorded RTCM File

`rtcm_parser.py` uses the `pyrtcm` package to decode a saved RTCM file into a text file.

Edit these values in `rtcm_parser.py`:

```python
input_file = "your_file.rtcm"
output_file = "decoded_rtcm.txt"
```

Then run:

```bash
python rtcm_parser.py
```

## Shamim Mountpoints

The included `sourcetable.txt` currently contains:

| Mountpoint | RTCM | Constellations |
|---|---|---|
| `VRS4` | RTCM 3 | GPS + GLONASS + Galileo + BeiDou |
| `Nearest4` | RTCM 3 | GPS + GLONASS + Galileo + BeiDou |
| `i-MAX4` | RTCM 3 | GPS + GLONASS + Galileo + BeiDou |
| `i-MAXGG` | RTCM 2 | GPS + GLONASS |
| `NearestGG` | RTCM 2 | GPS + GLONASS |
| `VRSGG` | RTCM 2 | GPS + GLONASS |
| `VGB` | RTCM 3 | Galileo + BeiDou |

> **Note:** `rtcm_receiver.py` currently implements RTCM 3 frame extraction and CRC validation. The RTCM 2 mountpoints listed in the sourcetable are therefore not handled by the current RTCM 3 frame parser.

## Important Parameters

### `VirtualReceiver`

```python
VirtualReceiver(
    lat,
    lon,
    height=0.0,
    geoid=0.0,
    satellites=12,
    hdop=1.0,
)
```

### `connect()`

```python
receiver.connect(
    username,
    password,
    host="shamim.ssaa.ir",
    port=2101,
    mountpoint="Nearest4",
)
```

### `receive()`

```python
receiver.receive(
    output=None,
    seconds=60,
    gga_interval=5,
    idle_timeout=30,
)
```

| Parameter | Description |
|---|---|
| `output` | Output `.rtcm` filename |
| `seconds` | Recording duration |
| `gga_interval` | Interval between GGA messages |
| `idle_timeout` | Stop if no valid RTCM frame is received within this period |

## Security

Do **not** commit real NTRIP usernames, passwords, tokens, or other credentials to a public repository.

Use environment variables, a local configuration file excluded by `.gitignore`, or another secret-management mechanism.

If credentials have previously been committed to a public repository, they should be considered exposed and should be changed.

A useful `.gitignore` is:

```gitignore
.env
*.log
__pycache__/
*.pyc
```

Depending on the project, you may also want to ignore recorded RTCM files:

```gitignore
*.rtcm
```

## Current Limitations

- The built-in frame parser is for RTCM 3.
- Only RTCM `1005` and `1006` are manually decoded by `rtcm_receiver.py`.
- Full decoding of recorded messages is delegated to `pyrtcm`.
- HTTP chunked or compressed NTRIP responses are not supported.
- The receiver currently uses a simple socket-based NTRIP client implementation.
- The project is primarily intended for testing, research, and development.

## Disclaimer

This project is intended for GNSS/NTRIP research and development. Verify correction streams, coordinate reference systems, receiver configuration, and positioning results before using the software in production surveying or safety-critical applications.
