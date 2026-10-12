
import base64
from datetime import datetime, timezone
from getpass import getpass
import math
from pathlib import Path
import select
import socket
from time import monotonic
from urllib.parse import quote


class VirtualReceiver:
    def __init__(
        self,
        lat,
        lon,
        height=0.0,
        geoid=0.0,
        satellites=12,
        hdop=1.0,
    ):
        values = (
            lat,
            lon,
            height,
            geoid,
            hdop,
        )

        if not all(math.isfinite(value) for value in values):
            raise ValueError(
                "Position, height, geoid and HDOP must be finite"
            )

        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError(
                "Invalid latitude or longitude"
            )

        if (
            not isinstance(satellites, int)
            or not 1 <= satellites <= 99
            or hdop <= 0
        ):
            raise ValueError(
                "Invalid satellite count or HDOP"
            )

        self.lat = lat
        self.lon = lon

        self.height = height
        self.geoid = geoid

        self.satellites = satellites
        self.hdop = hdop

        self.socket = None

        self._buffer = bytearray()

        self.rtcm_messages = 0
        self.rtcm_bytes = 0
        self.bytes_received = 0

        self.stop_reason = ""

        # Keep track of already printed station information
        self.last_reference_station = None

    # --------------------------------------------------
    # GGA coordinate formatting
    # --------------------------------------------------

    @staticmethod
    def _coordinate(value, degree_width):
        # Round before splitting to avoid 60.00000000 minutes
        units = round(
            abs(value)
            * 60
            * 100_000_000
        )

        degrees, minute_units = divmod(
            units,
            60 * 100_000_000,
        )

        minutes, fraction = divmod(
            minute_units,
            100_000_000,
        )

        return (
            f"{degrees:0{degree_width}d}"
            f"{minutes:02d}."
            f"{fraction:08d}"
        )

    # --------------------------------------------------
    # Create NMEA GGA
    # --------------------------------------------------

    def make_gga(self, when=None):
        """Create one NMEA GGA sentence."""

        when = when or datetime.now(
            timezone.utc
        )

        utc = (
            when.astimezone(timezone.utc)
            .strftime("%H%M%S.%f")[:9]
        )

        lat = self._coordinate(
            self.lat,
            2,
        )

        lon = self._coordinate(
            self.lon,
            3,
        )

        ns = (
            "N"
            if self.lat >= 0
            else "S"
        )

        ew = (
            "E"
            if self.lon >= 0
            else "W"
        )

        body = (
            f"GNGGA,"
            f"{utc},"
            f"{lat},{ns},"
            f"{lon},{ew},"
            f"1,"
            f"{self.satellites:02d},"
            f"{self.hdop:.1f},"
            f"{self.height:.4f},M,"
            f"{self.geoid:.4f},M,,"
        )

        checksum = 0

        for byte in body.encode("ascii"):
            checksum ^= byte

        return (
            f"${body}*{checksum:02X}\r\n"
        ).encode("ascii")

    # --------------------------------------------------
    # Read NTRIP response line
    # --------------------------------------------------

    def _read_line(self):
        line = bytearray()

        while len(line) < 8192:

            byte = self.socket.recv(1)

            if not byte:
                raise ConnectionError(
                    "Connection closed during NTRIP handshake"
                )

            line.extend(byte)

            if line.endswith(b"\r\n"):
                return bytes(line)

        raise RuntimeError(
            "NTRIP response line is too long"
        )

    # --------------------------------------------------
    # Connect to NTRIP caster
    # --------------------------------------------------

    def connect(
        self,
        username,
        password,
        host="shamim.ssaa.ir",
        port=2101,
        mountpoint="Nearest4",
    ):
        if (
            not username
            or not password
            or ":" in username
        ):
            raise ValueError(
                "Valid NTRIP username and password are required"
            )

        mountpoint = mountpoint.strip("/")

        if not mountpoint:
            raise ValueError(
                "A mountpoint is required"
            )

        self.disconnect()

        self._buffer.clear()

        token = base64.b64encode(
            f"{username}:{password}".encode(
                "utf-8"
            )
        ).decode("ascii")

        request = (
            f"GET /{quote(mountpoint, safe='')} HTTP/1.0\r\n"
            f"Host: {host}:{port}\r\n"
            "User-Agent: NTRIP VirtualReceiver/1.0\r\n"
            f"Authorization: Basic {token}\r\n"
            "\r\n"
        )

        print(
            f"Connecting to "
            f"{host}:{port}/{mountpoint} ..."
        )

        try:

            self.socket = socket.create_connection(
                (host, port),
                timeout=10,
            )

            self.socket.sendall(
                request.encode("ascii")
            )

            status = self._read_line()

            print(
                "Caster:",
                status.decode(
                    "ascii",
                    errors="replace",
                ).strip(),
            )

            valid_status = (
                [b"ICY", b"200"],
                [b"HTTP/1.0", b"200"],
                [b"HTTP/1.1", b"200"],
            )

            if status.split()[:2] not in valid_status:
                raise RuntimeError(
                    "Caster did not accept the stream request"
                )

            if status.startswith(b"HTTP/"):

                headers = {}

                for _ in range(64):

                    line = self._read_line()

                    if line == b"\r\n":
                        break

                    name, separator, value = (
                        line.partition(b":")
                    )

                    if separator:
                        headers[
                            name.strip().lower()
                        ] = value.strip().lower()

                else:
                    raise RuntimeError(
                        "Too many response headers"
                    )

                for name in (
                    b"transfer-encoding",
                    b"content-encoding",
                ):

                    if (
                        headers.get(
                            name,
                            b"identity",
                        )
                        != b"identity"
                    ):
                        raise RuntimeError(
                            "Chunked or compressed "
                            "responses are not supported"
                        )

                if (
                    b"sourcetable"
                    in headers.get(
                        b"content-type",
                        b"",
                    )
                ):
                    raise RuntimeError(
                        "Caster returned a sourcetable"
                    )

        except BaseException:

            self.disconnect()

            raise

    # --------------------------------------------------
    # RTCM CRC24Q
    # --------------------------------------------------

    @staticmethod
    def _crc24q(data):
        crc = 0

        for byte in data:

            crc ^= byte << 16

            for _ in range(8):

                crc <<= 1

                if crc & 0x1000000:
                    crc ^= 0x1864CFB

        return crc

    # --------------------------------------------------
    # Extract RTCM frames
    # --------------------------------------------------

    def _frames(self, data):
        """Reassemble and validate RTCM3 frames."""

        self._buffer.extend(data)

        while self._buffer:

            start = self._buffer.find(
                b"\xd3"
            )

            if start < 0:

                self._buffer.clear()

                return

            del self._buffer[:start]

            if len(self._buffer) < 3:
                return

            if self._buffer[1] & 0xFC:

                del self._buffer[0]

                continue

            payload_length = (
                ((self._buffer[1] & 0x03) << 8)
                | self._buffer[2]
            )

            size = (
                3
                + payload_length
                + 3
            )

            if len(self._buffer) < size:
                return

            frame = bytes(
                self._buffer[:size]
            )

            if (
                size < 8
                or self._crc24q(frame) != 0
            ):

                del self._buffer[0]

                continue

            del self._buffer[:size]

            yield frame

    # --------------------------------------------------
    # Read unsigned RTCM bits
    # --------------------------------------------------

    @staticmethod
    def _get_bits(
        data,
        start,
        length,
    ):
        value = 0

        for bit_index in range(
            start,
            start + length,
        ):

            byte_index = (
                bit_index // 8
            )

            bit_in_byte = (
                7
                - (bit_index % 8)
            )

            value = (
                value << 1
            ) | (
                (
                    data[byte_index]
                    >> bit_in_byte
                )
                & 1
            )

        return value

    # --------------------------------------------------
    # Read signed RTCM bits
    # --------------------------------------------------

    @classmethod
    def _get_signed_bits(
        cls,
        data,
        start,
        length,
    ):
        value = cls._get_bits(
            data,
            start,
            length,
        )

        sign_bit = (
            1 << (length - 1)
        )

        if value & sign_bit:
            value -= (
                1 << length
            )

        return value

    # --------------------------------------------------
    # Convert ECEF to latitude / longitude / height
    # --------------------------------------------------

    @staticmethod
    def _ecef_to_geodetic(
        x,
        y,
        z,
    ):
        """Convert ECEF coordinates using WGS84."""

        a = 6378137.0

        f = (
            1.0
            / 298.257223563
        )

        e2 = (
            f
            * (2.0 - f)
        )

        longitude = math.atan2(
            y,
            x,
        )

        p = math.hypot(
            x,
            y,
        )

        latitude = math.atan2(
            z,
            p * (1.0 - e2),
        )

        for _ in range(20):

            sin_latitude = math.sin(
                latitude
            )

            n = (
                a
                / math.sqrt(
                    1.0
                    - e2
                    * sin_latitude
                    * sin_latitude
                )
            )

            height = (
                p
                / math.cos(latitude)
                - n
            )

            new_latitude = math.atan2(
                z,
                p
                * (
                    1.0
                    - (
                        e2
                        * n
                        / (
                            n
                            + height
                        )
                    )
                ),
            )

            if (
                abs(
                    new_latitude
                    - latitude
                )
                < 1e-12
            ):
                latitude = (
                    new_latitude
                )

                break

            latitude = (
                new_latitude
            )

        sin_latitude = math.sin(
            latitude
        )

        n = (
            a
            / math.sqrt(
                1.0
                - e2
                * sin_latitude
                * sin_latitude
            )
        )

        height = (
            p
            / math.cos(latitude)
            - n
        )

        return (
            math.degrees(
                latitude
            ),
            math.degrees(
                longitude
            ),
            height,
        )

    # --------------------------------------------------
    # Calculate distance between rover and base
    # --------------------------------------------------

    @staticmethod
    def _distance_km(
        lat1,
        lon1,
        lat2,
        lon2,
    ):
        """Calculate approximate surface distance using Haversine."""

        radius = 6371.0088

        lat1 = math.radians(
            lat1
        )

        lon1 = math.radians(
            lon1
        )

        lat2 = math.radians(
            lat2
        )

        lon2 = math.radians(
            lon2
        )

        dlat = (
            lat2 - lat1
        )

        dlon = (
            lon2 - lon1
        )

        a = (
            math.sin(
                dlat / 2
            ) ** 2
            + math.cos(lat1)
            * math.cos(lat2)
            * math.sin(
                dlon / 2
            ) ** 2
        )

        c = (
            2
            * math.atan2(
                math.sqrt(a),
                math.sqrt(
                    1.0 - a
                ),
            )
        )

        return (
            radius * c
        )

    # --------------------------------------------------
    # Decode RTCM 1005 / 1006
    # --------------------------------------------------

    @classmethod
    def decode_reference_station(
        cls,
        frame,
    ):
        payload = frame[3:-3]

        message_type = cls._get_bits(
            payload,
            0,
            12,
        )

        if message_type not in (
            1005,
            1006,
        ):
            return None

        station_id = cls._get_bits(
            payload,
            12,
            12,
        )

        itrf_year = cls._get_bits(
            payload,
            24,
            6,
        )

        x_raw = cls._get_signed_bits(
            payload,
            34,
            38,
        )

        y_raw = cls._get_signed_bits(
            payload,
            74,
            38,
        )

        z_raw = cls._get_signed_bits(
            payload,
            114,
            38,
        )

        x = (
            x_raw
            * 0.0001
        )

        y = (
            y_raw
            * 0.0001
        )

        z = (
            z_raw
            * 0.0001
        )

        antenna_height = None

        if message_type == 1006:

            antenna_height_raw = (
                cls._get_bits(
                    payload,
                    152,
                    16,
                )
            )

            antenna_height = (
                antenna_height_raw
                * 0.0001
            )

        (
            latitude,
            longitude,
            ellipsoidal_height,
        ) = cls._ecef_to_geodetic(
            x,
            y,
            z,
        )

        return {
            "message_type": message_type,
            "station_id": station_id,
            "itrf_year": itrf_year,
            "ecef_x": x,
            "ecef_y": y,
            "ecef_z": z,
            "latitude": latitude,
            "longitude": longitude,
            "ellipsoidal_height": (
                ellipsoidal_height
            ),
            "antenna_height": (
                antenna_height
            ),
        }

    # --------------------------------------------------
    # Print reference station information
    # --------------------------------------------------

    def _print_reference_station(
        self,
        station,
    ):
        distance = self._distance_km(
            self.lat,
            self.lon,
            station["latitude"],
            station["longitude"],
        )

        print()
        print(
            "=============================================="
        )
        print(
            "       RTCM REFERENCE STATION DETECTED"
        )
        print(
            "=============================================="
        )

        print(
            f"RTCM message       : "
            f"{station['message_type']}"
        )

        print(
            f"Station ID         : "
            f"{station['station_id']:04d}"
        )

        print(
            f"ITRF year          : "
            f"{station['itrf_year']}"
        )

        print(
            f"Latitude           : "
            f"{station['latitude']:.9f}"
        )

        print(
            f"Longitude          : "
            f"{station['longitude']:.9f}"
        )

        print(
            f"Ellipsoid height   : "
            f"{station['ellipsoidal_height']:.4f} m"
        )

        if (
            station["antenna_height"]
            is not None
        ):
            print(
                f"Antenna height     : "
                f"{station['antenna_height']:.4f} m"
            )

        print(
            f"ECEF X             : "
            f"{station['ecef_x']:.4f} m"
        )

        print(
            f"ECEF Y             : "
            f"{station['ecef_y']:.4f} m"
        )

        print(
            f"ECEF Z             : "
            f"{station['ecef_z']:.4f} m"
        )

        print(
            f"Distance from rover: "
            f"{distance:.3f} km"
        )

        if station["station_id"] == 47:

            print(
                "Station check      : "
                "MATCHES expected station 0047"
            )

        else:

            print(
                "Station check      : "
                f"Station is "
                f"{station['station_id']:04d}, "
                f"not 0047"
            )

        print(
            "=============================================="
        )
        print()

    # --------------------------------------------------
    # Receive RTCM stream
    # --------------------------------------------------

    def receive(
        self,
        output=None,
        seconds=60,
        gga_interval=5,
        idle_timeout=30,
    ):
        if self.socket is None:
            raise RuntimeError(
                "Call connect() first"
            )

        if (
            not all(
                math.isfinite(v)
                for v in (
                    seconds,
                    gga_interval,
                    idle_timeout,
                )
            )
            or seconds < 0
            or gga_interval <= 0
            or idle_timeout <= 0
        ):
            raise ValueError(
                "Invalid duration, GGA interval or idle timeout"
            )

        output = (
            Path(output)
            if output
            else Path(
                datetime.now(
                    timezone.utc
                ).strftime(
                    "shamim_%Y%m%d_%H%M%S_%f.rtcm"
                )
            )
        )

        self.rtcm_messages = 0
        self.rtcm_bytes = 0
        self.bytes_received = 0
        self.stop_reason = ""

        self.last_reference_station = None

        started = monotonic()
        last_rtcm = started
        next_gga = started

        print(
            "Recording to:",
            output.resolve(),
        )

        with output.open(
            "xb"
        ) as file:

            try:

                while True:

                    now = monotonic()

                    if (
                        seconds
                        and now - started >= seconds
                    ):
                        self.stop_reason = (
                            "Duration reached"
                        )

                        break

                    if (
                        now - last_rtcm
                        >= idle_timeout
                    ):
                        self.stop_reason = (
                            "No valid RTCM before idle timeout"
                        )

                        break

                    if now >= next_gga:

                        gga = self.make_gga()

                        self.socket.sendall(
                            gga
                        )

                        print(
                            "GGA sent:",
                            gga.decode(
                                "ascii"
                            ).strip(),
                        )

                        next_gga = (
                            monotonic()
                            + gga_interval
                        )

                    wait = min(
                        0.25,
                        max(
                            0,
                            next_gga
                            - monotonic(),
                        ),
                    )

                    readable = select.select(
                        [self.socket],
                        [],
                        [],
                        wait,
                    )[0]

                    if not readable:
                        continue

                    data = self.socket.recv(
                        4096
                    )

                    if not data:

                        self.stop_reason = (
                            "EOF"
                        )

                        break

                    self.bytes_received += len(
                        data
                    )

                    for frame in self._frames(
                        data
                    ):

                        file.write(
                            frame
                        )

                        file.flush()

                        self.rtcm_messages += 1

                        self.rtcm_bytes += len(
                            frame
                        )

                        last_rtcm = monotonic()

                        message_type = (
                            (frame[3] << 4)
                            | (frame[4] >> 4)
                        )

                        station_id = (
                            (
                                frame[4]
                                & 0x0F
                            )
                            << 8
                        ) | frame[5]

                        print(
                            f"RTCM "
                            f"{message_type} | "
                            f"Station ID: "
                            f"{station_id:04d} | "
                            f"{len(frame)} bytes",
                            flush=True,
                        )

                        if message_type in (
                            1005,
                            1006,
                        ):

                            station = (
                                self.decode_reference_station(
                                    frame
                                )
                            )

                            if station:

                                station_key = (
                                    station[
                                        "station_id"
                                    ],
                                    round(
                                        station[
                                            "latitude"
                                        ],
                                        9,
                                    ),
                                    round(
                                        station[
                                            "longitude"
                                        ],
                                        9,
                                    ),
                                )

                                if (
                                    station_key
                                    != self.last_reference_station
                                ):

                                    self.last_reference_station = (
                                        station_key
                                    )

                                    self._print_reference_station(
                                        station
                                    )

            except KeyboardInterrupt:

                self.stop_reason = (
                    "Stopped by user"
                )

            finally:

                print(
                    f"Saved "
                    f"{self.rtcm_messages} "
                    f"RTCM frames "
                    f"({self.rtcm_bytes} bytes)"
                )

                if self.rtcm_messages == 0:

                    print(
                        "No valid RTCM corrections were recorded."
                    )

        print(
            "Stopped:",
            self.stop_reason,
        )

        return self.rtcm_messages

    # --------------------------------------------------
    # Disconnect
    # --------------------------------------------------

    def disconnect(self):

        if self.socket is not None:

            self.socket.close()

            self.socket = None


# ==================================================
# MAIN
# ==================================================

if __name__ == "__main__":

    receiver = VirtualReceiver(
        lat=35.72176143883333,
        lon=51.441835367,
        height=1266.8765,
        geoid=6.0397,
        satellites=12,
        hdop=0.6,
    )

    username = '4711281207'

    password = 'mmd471'

    try:

        receiver.connect(
            username=username,
            password=password,
            mountpoint="Nearest4",
        )

        receiver.receive(
            seconds=60,
        )

    finally:

        receiver.disconnect()