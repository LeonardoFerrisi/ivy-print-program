"""
Canon Ivy 1 (PV-123) Print Script for Windows

Sends a JPEG image to the printer via OBEX Object Push over Bluetooth.
No external Bluetooth libraries needed — uses Python's built-in socket module.

Requirements:
    pip install Pillow

Usage:
    python ivy1_print.py image.jpg
    python ivy1_print.py image.png
"""

import socket
import struct
import sys
import time
from PIL import Image
from io import BytesIO

# ── Configuration ────────────────────────────────────────────────────────────

PRINTER_MAC = "A4:62:DF:79:5E:EB"

# The printer expects 640x1616 but the visible area is ~640x925 in the center.
# The top and bottom margins get consumed by the ZINK printing process.
PRINT_WIDTH = 640
PRINT_HEIGHT = 1616

# OBEX constants (from decompiled Canon Mini Print app)
OBEX_CONNECT = 0x80
OBEX_PUT = 0x02
OBEX_PUT_FINAL = 0x82
OBEX_DISCONNECT = 0x81
OBEX_SUCCESS = 0xA0
OBEX_CONTINUE = 0x90

HEADER_NAME = 0x01
HEADER_TYPE = 0x42
HEADER_LENGTH = 0xC3
HEADER_BODY = 0x48
HEADER_END_BODY = 0x49

MAX_OBEX_PACKET = 6144  # from decompiled app: DEFAULT_MAX_PACKET_LENGTH


# ── Image Preparation ────────────────────────────────────────────────────────

def prepare_image(path):
    """Scale, crop, and rotate image to match Ivy 1 print format."""
    img = Image.open(path).convert("RGB")
    w, h = img.size

    # Scale to fill the print area (crop excess)
    scale = max(PRINT_WIDTH / w, PRINT_HEIGHT / h)
    img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

    # Center crop to exact dimensions
    w2, h2 = img.size
    left = (w2 - PRINT_WIDTH) // 2
    top = (h2 - PRINT_HEIGHT) // 2
    img = img.crop((left, top, left + PRINT_WIDTH, top + PRINT_HEIGHT))

    # Rotate 180° (printer feeds paper bottom-first)
    img = img.rotate(180)

    # Encode as JPEG
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=100)
    return buf.getvalue()


# ── OBEX Protocol ────────────────────────────────────────────────────────────

def obex_connect_packet():
    """OBEX CONNECT request: version 1.0, max packet 6144."""
    return struct.pack(">BHBBH", OBEX_CONNECT, 7, 0x10, 0x00, MAX_OBEX_PACKET)


def obex_disconnect_packet():
    return struct.pack(">BH", OBEX_DISCONNECT, 3)


def make_header_string(header_id, text):
    """OBEX Unicode string header (null-terminated UTF-16BE)."""
    encoded = text.encode("utf-16-be") + b"\x00\x00"
    return struct.pack(">BH", header_id, 3 + len(encoded)) + encoded


def make_header_bytes(header_id, data):
    """OBEX byte-sequence header."""
    return struct.pack(">BH", header_id, 3 + len(data)) + data


def make_header_int(header_id, value):
    """OBEX 4-byte integer header."""
    return struct.pack(">BI", header_id, value)


def obex_recv(sock):
    """Read an OBEX response packet."""
    header = sock.recv(3)
    if len(header) < 3:
        return None, b""
    code = header[0]
    length = struct.unpack(">H", header[1:3])[0]
    body = b""
    if length > 3:
        body = sock.recv(length - 3)
    return code, body


def send_image_obex(sock, jpeg_data):
    """Send image using OBEX PUT, chunked if needed."""
    # First PUT packet includes Name, Type, Length headers
    name_hdr = make_header_string(HEADER_NAME, "img.jpg")
    type_hdr = make_header_bytes(HEADER_TYPE, b"image/jpeg\x00")
    length_hdr = make_header_int(HEADER_LENGTH, len(jpeg_data))

    overhead = 3 + len(name_hdr) + len(type_hdr) + len(length_hdr) + 3  # +3 for body header
    first_chunk_size = MAX_OBEX_PACKET - overhead
    offset = 0

    if len(jpeg_data) <= first_chunk_size:
        # Single packet — use PUT FINAL + END_OF_BODY
        body_hdr = make_header_bytes(HEADER_END_BODY, jpeg_data)
        headers = name_hdr + type_hdr + length_hdr + body_hdr
        pkt = struct.pack(">BH", OBEX_PUT_FINAL, 3 + len(headers)) + headers
        sock.send(pkt)
        code, _ = obex_recv(sock)
        return code == OBEX_SUCCESS
    else:
        # Multi-packet: first PUT with BODY
        chunk = jpeg_data[:first_chunk_size]
        body_hdr = make_header_bytes(HEADER_BODY, chunk)
        headers = name_hdr + type_hdr + length_hdr + body_hdr
        pkt = struct.pack(">BH", OBEX_PUT, 3 + len(headers)) + headers
        sock.send(pkt)
        code, _ = obex_recv(sock)
        if code != OBEX_CONTINUE:
            print(f"  Expected CONTINUE (0x90), got 0x{code:02x}")
            return False
        offset = first_chunk_size

        # Subsequent chunks
        while offset < len(jpeg_data):
            remaining = len(jpeg_data) - offset
            chunk_max = MAX_OBEX_PACKET - 6  # 3 pkt header + 3 body header
            chunk_size = min(remaining, chunk_max)
            chunk = jpeg_data[offset:offset + chunk_size]
            is_last = (offset + chunk_size >= len(jpeg_data))

            if is_last:
                body_hdr = make_header_bytes(HEADER_END_BODY, chunk)
                opcode = OBEX_PUT_FINAL
            else:
                body_hdr = make_header_bytes(HEADER_BODY, chunk)
                opcode = OBEX_PUT

            pkt = struct.pack(">BH", opcode, 3 + len(body_hdr)) + body_hdr
            sock.send(pkt)
            code, _ = obex_recv(sock)

            if is_last:
                return code == OBEX_SUCCESS
            elif code != OBEX_CONTINUE:
                print(f"  Expected CONTINUE, got 0x{code:02x}")
                return False

            offset += chunk_size
            pct = min(100, int(offset / len(jpeg_data) * 100))
            print(f"  Sent {pct}%...", end="\r")

    return False


# ── Main ─────────────────────────────────────────────────────────────────────

def find_opp_port(mac):
    """Try RFCOMM ports to find the OBEX Object Push service."""
    for port in range(1, 11):
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(3)
            sock.connect((mac, port))

            # Drain any unsolicited data
            sock.settimeout(1)
            try:
                sock.recv(4096)
            except socket.timeout:
                pass

            # Try OBEX CONNECT
            sock.send(obex_connect_packet())
            sock.settimeout(3)
            code, body = obex_recv(sock)

            if code == OBEX_SUCCESS:
                print(f"  Found OPP on port {port}")
                # Parse max packet length from CONNECT response
                if len(body) >= 4:
                    max_pkt = struct.unpack(">H", body[2:4])[0]
                    print(f"  Printer max packet: {max_pkt}")
                return sock, port

            sock.close()
        except Exception:
            try:
                sock.close()
            except:
                pass
    return None, None


def main():
    if len(sys.argv) < 2:
        print("Usage: python ivy1_print.py <image_path>")
        print("       python ivy1_print.py photo.jpg")
        sys.exit(1)

    image_path = sys.argv[1]

    # Prepare image
    print(f"Preparing image: {image_path}")
    jpeg_data = prepare_image(image_path)
    print(f"  JPEG size: {len(jpeg_data)} bytes ({PRINT_WIDTH}x{PRINT_HEIGHT})")

    # Find OPP port
    print(f"\nConnecting to {PRINTER_MAC}...")
    sock, port = find_opp_port(PRINTER_MAC)
    if not sock:
        print("  Could not find OBEX service. Is the printer on?")
        sys.exit(1)

    # Send image
    print(f"\nSending image via OBEX PUT...")
    success = send_image_obex(sock, jpeg_data)

    if success:
        print(f"\n  Image sent successfully! Printing should start shortly.")
    else:
        print(f"\n  Transfer failed.")

    # Disconnect
    try:
        sock.send(obex_disconnect_packet())
        obex_recv(sock)
    except:
        pass
    sock.close()
    print("Done.")


if __name__ == "__main__":
    main()