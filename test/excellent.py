import argparse
import asyncio
import io
import socket
import struct
import sys
import uuid

from PIL import Image
from winsdk.windows.devices.bluetooth import BluetoothDevice
from winsdk.windows.devices.bluetooth.rfcomm import RfcommServiceId


MAC = "A4:62:DF:79:5E:EB"

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
OPP_UUID = "00001105-0000-1000-8000-00805f9b34fb"

CMD_PRINT_READY = 0x0000
CMD_START_OF_SEND_IMAGE = 0x0200
CMD_ERROR_MESSAGE = 0x0400

START_CODE = 0x1B2A
CUSTOMER_CODE = 0x4341
FROM_CLIENT = 0x00
PRODUCT_CODE = 0x00

OBEX_CONNECT = 0x80
OBEX_PUT = 0x02
OBEX_PUT_FINAL = 0x82

OBEX_RSP_CONTINUE = 0x90
OBEX_RSP_SUCCESS = 0xA0

HDR_NAME = 0x01
HDR_TYPE = 0x42
HDR_LENGTH = 0xC3
HDR_BODY = 0x48
HDR_END_OF_BODY = 0x49

# Windows Winsock Bluetooth constants.
AF_BTH = getattr(socket, "AF_BTH", 32)
BTPROTO_RFCOMM = getattr(socket, "BTPROTO_RFCOMM", 3)


def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", "").replace("-", ""), 16)


def prepare_png_as_jpeg(path: str, quality: int = 95) -> bytes:
    img = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    rgb = bg.convert("RGB")

    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def build_ctrl_packet(command: int, payload: bytes = b"") -> bytes:
    packet = bytearray(34)
    struct.pack_into(">HHBBH", packet, 0, START_CODE, CUSTOMER_CODE, FROM_CLIENT, PRODUCT_CODE, command)
    packet[8:8 + len(payload)] = payload
    return bytes(packet)


def build_print_ready_payload(image_size: int, is_precut: bool = False) -> bytes:
    if image_size > 0xFFFFFF:
        raise ValueError("JPEG too large for 3-byte PRINT_READY size field")

    return (
        image_size.to_bytes(3, "big") +
        bytes([
            0x01,
            0x00,
            0x00,
            0x00,
            0x01,
            0x01 if is_precut else 0x00,
        ])
    )


def parse_ctrl_packet(data: bytes) -> tuple[int, int]:
    if len(data) < 34:
        raise RuntimeError(f"Expected 34-byte control packet, got {len(data)} bytes")

    command = struct.unpack_from(">H", data, 6)[0]
    error_byte = data[8]
    return command, error_byte


def obex_string_header(header_id: int, value: str) -> bytes:
    data = value.encode("utf-16-be") + b"\x00\x00"
    return bytes([header_id]) + struct.pack(">H", len(data) + 3) + data


def obex_bytes_header(header_id: int, value: bytes) -> bytes:
    return bytes([header_id]) + struct.pack(">H", len(value) + 3) + value


def obex_int_header(header_id: int, value: int) -> bytes:
    return bytes([header_id]) + struct.pack(">I", value)


def build_obex_packet(opcode: int, headers: bytes = b"", body: bytes = b"") -> bytes:
    payload = headers + body
    return bytes([opcode]) + struct.pack(">H", len(payload) + 3) + payload


async def discover_rfcomm_channel(mac: str, service_uuid: str) -> int:
    device = await BluetoothDevice.from_bluetooth_address_async(mac_to_int(mac))
    if device is None:
        raise RuntimeError(f"Could not resolve Bluetooth device {mac}. Pair it in Windows first.")

    service_id = RfcommServiceId.from_uuid(uuid.UUID(service_uuid))
    result = await device.get_rfcomm_services_for_id_async(service_id)

    if result.services.size == 0:
        raise RuntimeError(
            f"No RFCOMM service {service_uuid} found on {mac}. "
            f"Windows may not expose this profile for the paired device."
        )

    service = result.services[0]
    service_name = service.connection_service_name
    print(f"Discovered {service_uuid} -> service/channel '{service_name}'")

    try:
        return int(service_name)
    except Exception as exc:
        raise RuntimeError(
            f"Windows discovered the service but did not return a numeric RFCOMM channel: {service_name!r}"
        ) from exc


class WinBtSocket:
    def __init__(self):
        self.sock = None

    async def connect(self, mac: str, service_uuid: str):
        channel = await discover_rfcomm_channel(mac, service_uuid)

        print(f"Using AF_BTH={AF_BTH}, BTPROTO_RFCOMM={BTPROTO_RFCOMM}")

        s = socket.socket(AF_BTH, socket.SOCK_STREAM, BTPROTO_RFCOMM)
        s.settimeout(20)

        last_error = None

        # Try the integer address form first, then the string form.
        for addr in (mac_to_int(mac), mac):
            try:
                print(f"Connecting to {mac} channel {channel} using address form {addr!r}")
                s.connect((addr, channel))
                self.sock = s
                print("Connected")
                return
            except Exception as exc:
                last_error = exc

        s.close()
        raise RuntimeError(f"Bluetooth socket connect failed: {last_error}") from last_error

    async def write(self, data: bytes):
        self.sock.sendall(data)

    async def read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("Socket closed while reading")
            buf.extend(chunk)
        return bytes(buf)

    async def read_obex_packet(self) -> bytes:
        header = await self.read_exact(3)
        total_len = struct.unpack(">H", header[1:3])[0]
        if total_len < 3:
            raise RuntimeError(f"Invalid OBEX length: {total_len}")
        rest = await self.read_exact(total_len - 3)
        return header + rest

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


async def wait_for_print_ready(spp: WinBtSocket, jpeg_size: int):
    payload = build_print_ready_payload(jpeg_size, is_precut=False)
    pkt = build_ctrl_packet(CMD_PRINT_READY, payload)

    print(f"Sending PRINT_READY, JPEG size={jpeg_size}")
    await spp.write(pkt)

    rsp = await spp.read_exact(34)
    command, error_byte = parse_ctrl_packet(rsp)
    print(f"SPP response command=0x{command:04X}, error={error_byte}")

    if command == CMD_START_OF_SEND_IMAGE:
        return

    if command == CMD_ERROR_MESSAGE:
        raise RuntimeError(f"Printer returned ERROR_MESSAGE, code={error_byte}")

    raise RuntimeError(f"Unexpected control response: command=0x{command:04X}, error={error_byte}")


async def send_jpeg_over_opp(opp: WinBtSocket, jpeg: bytes):
    print("Starting OBEX CONNECT")
    connect_pkt = bytes([OBEX_CONNECT]) + struct.pack(">H", 7) + bytes([0x10, 0x00, 0xFF, 0x00])
    await opp.write(connect_pkt)

    rsp = await opp.read_obex_packet()
    print(f"OBEX CONNECT response=0x{rsp[0]:02X}")
    if rsp[0] != OBEX_RSP_SUCCESS:
        raise RuntimeError(f"OBEX CONNECT failed: 0x{rsp[0]:02X}")

    headers = b"".join([
        obex_string_header(HDR_NAME, "img.jpg"),
        obex_string_header(HDR_TYPE, "image/jpeg"),
        obex_int_header(HDR_LENGTH, len(jpeg)),
    ])

    print("Sending OBEX PUT headers")
    await opp.write(build_obex_packet(OBEX_PUT, headers=headers))

    rsp = await opp.read_obex_packet()
    print(f"OBEX PUT headers response=0x{rsp[0]:02X}")
    if rsp[0] not in (OBEX_RSP_CONTINUE, OBEX_RSP_SUCCESS):
        raise RuntimeError(f"Initial OBEX PUT failed: 0x{rsp[0]:02X}")

    chunk_size = 4096
    offset = 0

    while offset < len(jpeg):
        chunk = jpeg[offset:offset + chunk_size]
        offset += len(chunk)

        await opp.write(build_obex_packet(OBEX_PUT, body=obex_bytes_header(HDR_BODY, chunk)))
        rsp = await opp.read_obex_packet()

        print(f"Sent {offset}/{len(jpeg)} bytes, response=0x{rsp[0]:02X}")

        if rsp[0] not in (OBEX_RSP_CONTINUE, OBEX_RSP_SUCCESS):
            raise RuntimeError(f"OBEX BODY failed at offset {offset}: 0x{rsp[0]:02X}")

    print("Sending OBEX final packet")
    await opp.write(build_obex_packet(OBEX_PUT_FINAL, body=obex_bytes_header(HDR_END_OF_BODY, b"")))

    rsp = await opp.read_obex_packet()
    print(f"OBEX final response=0x{rsp[0]:02X}")
    if rsp[0] != OBEX_RSP_SUCCESS:
        raise RuntimeError(f"OBEX final PUT failed: 0x{rsp[0]:02X}")


async def print_png(image_path: str):
    jpeg = prepare_png_as_jpeg(image_path)
    print(f"Prepared JPEG: {len(jpeg)} bytes")

    spp = WinBtSocket()
    opp = WinBtSocket()

    try:
        print("Connecting SPP")
        await spp.connect(MAC, SPP_UUID)

        await wait_for_print_ready(spp, len(jpeg))

        print("Connecting OPP")
        await opp.connect(MAC, OPP_UUID)

        await send_jpeg_over_opp(opp, jpeg)

        print("Transfer complete. Printer should begin shortly.")
    finally:
        opp.close()
        spp.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", help="Path to PNG image")
    args = parser.parse_args()

    try:
        asyncio.run(print_png(args.image))
    except KeyboardInterrupt:
        print("Cancelled")
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()