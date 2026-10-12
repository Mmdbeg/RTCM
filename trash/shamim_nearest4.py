from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from queue import Queue, Empty
from time import monotonic

from pygnssutils import GNSSNTRIPClient
from pynmeagps import NMEAMessage, GET


SERVER = "shamim.ssaa.ir"
PORT = 2101


class ShamimClient(GNSSNTRIPClient):
    
    def _open_connection(self, settings):
        connection = super()._open_connection(settings)

        class TracedSocket:
            def recv(self, size, *args):
                data = connection.recv(size, *args)

                # Show incoming bytes before the library processes them.
                print(
                    f"RX {len(data)} bytes: {data[:48]!r}",
                    flush=True,
                )

                return data

            def __getattr__(self, name):
                return getattr(connection, name)

        return TracedSocket()

    def _format_gga(self):
        lat, lon, alt, sep, *_ = self._app_get_coordinates()

        # Quality values below are test values, not live receiver data.
        message = NMEAMessage(
            "GP", "GGA", GET,
            time=datetime.now(timezone.utc).time(),
            lat=float(lat),
            lon=float(lon),
            quality=2,
            numSV=6,
            HDOP=1.2,
            alt=alt,
            altUnit="M",
            sep=sep,
            sepUnit="M",
            diffAge='',
            diffStation='',
        )

        # Add the checksum and CRLF automatically.
        return message.serialize(), message


def get_mountpoints(username, password):
    messages = Queue()
    client = ShamimClient(retries=0, timeout=20)

    try:
        client.run(
            server=SERVER,
            port=PORT,
            mountpoint="",
            ntripuser=username,
            ntrippassword=password,
            version="1.0",
            https=0,
            ggainterval=-1,
            output=messages,
        )

        deadline = monotonic() + 30

        while monotonic() < deadline:
            try:
                raw, parsed = messages.get(timeout=0.5)
            except Empty:
                if client.stopevent.is_set():
                    break
                continue

            if isinstance(raw, list):
                Path("sourcetable.txt").write_text(
                    client.response_body,
                    encoding="utf-8",
                )
                return raw

        raise RuntimeError(
            f"No mountpoints received. Caster status: {client.status}"
        )

    finally:
        client.stop()


def main():
    username = '4711281207'
    password = 'mmd471'
    print("\nGetting mountpoints...")
    stations = get_mountpoints(username, password)

    if not stations:
        print("The mountpoint list is empty.")
        return

    # Display each mountpoint and its correction format.
    for number, station in enumerate(stations, start=1):
        print(f"{number}. {station[0]} | {station[2]}")

    while True:
        try:
            number = int(input("\nSelect a number: "))

            if not 1 <= number <= len(stations):
                print("Choose a number from the list.")
                continue

            selected = stations[number - 1]

            # The current decoder supports RTCM 3 only.
            if not selected[2].upper().startswith("RTCM 3"):
                print("This script requires an RTCM 3 mountpoint.")
                continue

            break

        except ValueError:
            print("Enter a valid number.")

    mountpoint = selected[0]
    print(f"\nSelected: {mountpoint}")

    lat = float(input("Latitude (decimal degrees): "))
    lon = float(input("Longitude (decimal degrees): "))
    alt = float(input("MSL height in metres [0]: ") or "0")
    sep = float(input("Geoid separation in metres [0]: ") or "0")

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid latitude or longitude.")

    messages = Queue()
    client = ShamimClient(retries=0, timeout=20)
    count = 0

    # Store each session in a separate folder.
    folder = Path(
        "shamim_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    folder.mkdir()

    with open(folder / "corrections.rtcm", "wb") as binary_file, \
         open(folder / "messages.txt", "w", encoding="utf-8") as text_file:

        try:
            client.run(
                server=SERVER,
                port=PORT,
                mountpoint=mountpoint,
                ntripuser=username,
                ntrippassword=password,
                version="1.0",
                https=0,
                datatype="RTCM",
                ggamode=1,
                reflat=lat,
                reflon=lon,
                refalt=alt,
                refsep=sep,
                ggainterval=5,
                output=messages,
            )

            print("\nConnecting... Press Ctrl+C to stop.")

            while not client.stopevent.is_set() or not messages.empty():
                try:
                    raw, parsed = messages.get(timeout=0.5)
                except Empty:
                    continue

                if not isinstance(raw, (bytes, bytearray)):
                    print("Caster response:", parsed)
                    continue

                if raw.startswith(b"$"):
                    print("GGA sent:", raw.decode("ascii").strip())

                elif raw.startswith(b"\xd3"):
                    binary_file.write(raw)
                    binary_file.flush()

                    text_file.write(str(parsed) + "\n")
                    text_file.flush()

                    print(parsed)
                    count += 1

        except KeyboardInterrupt:
            print("\nStopped.")

        finally:
            client.stop()

    print("Caster status:", client.status)
    print(f"Received RTCM messages: {count}")

    if count:
        print("Saved in:", folder.resolve())
    else:
        print("No corrections received.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as error:
        print("Error:", error)