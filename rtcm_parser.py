from pyrtcm import RTCMReader


input_file = "shamim_20261003_060326_407107.rtcm"
output_file = "shamim_20261003_060326_407107.txt"


with open(input_file, "rb") as stream, open(
    output_file,
    "w",
    encoding="utf-8",
) as text_file:

    reader = RTCMReader(stream)

    for raw_data, message in reader:

        text_file.write(
            "========================================\n"
        )

        text_file.write(
            f"RTCM Message Type: {message.identity}\n"
        )

        text_file.write(
            f"{message}\n"
        )


print(
    f"Decoded RTCM saved to: {output_file}"
)