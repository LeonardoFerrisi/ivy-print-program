# ivy-print-program
Program for printing a png to a canon ivy printer

The IVY1 is a bluetooth only device,


# Canon Ivy 1 (PV-123) — Python Print API

A Python script to print images on the Canon Ivy 1 (PV-123) mini photo printer from Windows over Bluetooth.

Reverse engineered from the Canon Mini Print Android app (v3.8.3) and Bluetooth HCI packet captures.

## Requirements

- Python 3.9+ (for native `socket.AF_BLUETOOTH` support on Windows)
- Pillow (`pip install Pillow`)
- The printer must be paired in Windows Bluetooth settings

No external Bluetooth libraries are needed.

## Usage

```
python ivy1_print.py photo.jpg
python ivy1_print.py photo.png
```

The script handles image preparation (scaling, cropping, rotation) and sends it to the printer automatically.

Update `PRINTER_MAC` at the top of `ivy1_print.py` to match your printer's Bluetooth MAC address.

## How the Communication Protocol Works

### Overview

The Canon Ivy 1 uses a completely different protocol from the Canon Ivy 2. The Ivy 2 uses a custom 34-byte binary command protocol over a single RFCOMM channel. The Ivy 1 uses standard **OBEX Object Push Profile (OPP)** — the same protocol Bluetooth uses for generic file transfer.

To print, you push a JPEG file to the printer over OBEX. The printer receives it and prints it. No special setup commands, handshakes, or state machines are required on the OBEX channel.

### Bluetooth Services

The Ivy 1 exposes three RFCOMM services:

| Port | UUID | Protocol | Purpose |
|------|------|----------|---------|
| 1 | `0x1101` (SPP) | Custom `FF 55` binary | Status/control channel. Printer sends periodic status beacons. Not required for printing. |
| 2 | `0x1101` (SPP) | Custom `1B 2A` binary (34-byte packets) | Command channel. Echoes back `ack=0x0104` to any command. Used by the app for status polling. Not required for printing. |
| 4+ | `0x1105` (OPP) | OBEX Object Push | **Image transfer channel. This is how printing works.** |

The Canon Mini Print app opens all three simultaneously, but only the OPP channel is needed to print.

### Port 1 — Status Channel (`FF 55` Protocol)

On connect, the printer immediately sends a 6-byte greeting:

```
ff 55 02 00 ee 10
```

If you echo this back, the printer responds with a 26-byte device info packet:

```
ff 5a 00 1a 80 2b 00 00 e2 01 05 03 ef 05 dc 10 49 03 03 0a 00 01 0b 02 01 af
```

Decoded fields (partial):

| Offset | Value | Meaning |
|--------|-------|---------|
| 0–1 | `ff 5a` | Response header |
| 2–3 | `00 1a` | Packet length (26 bytes) |
| 4–5 | `80 2b` | Product code |
| 10–11 | `05 03` | Firmware version (5.3) |
| 13–14 | `05 dc` | Max payload (1500) |
| 19 | `0a` | Auto power-off (10 minutes) |

If you don't respond to the greeting, the printer resends `ff 55 02 00 ee 10` every second. This channel is informational only — you do not need it to print.

### Port 2 — Command Channel (`1B 2A` Protocol)

This channel uses 34-byte binary packets with the same structure as the Ivy 2 protocol, but with start code `0x1B2A` instead of `0x430F`:

```
Bytes 0–1:  Start code (0x1B2A)
Bytes 2–3:  Sequence/session ID
Byte  4:    Sub-type
Bytes 5–6:  Command or ACK
Byte  7:    Error code
Bytes 8–33: Payload
```

The printer responds to every command with `ack=0x0104` and `error=0`. The Canon app uses this channel to poll printer status during printing. It is not required to initiate or complete a print job.

### OPP Channel — Image Transfer (OBEX Object Push)

This is the only channel needed for printing. The protocol is standard OBEX:

**Step 1: OBEX CONNECT**

```
Client → Printer:  80 00 07 10 00 18 00
                    │  │     │  │  │
                    │  │     │  │  └── Max packet length (6144)
                    │  │     │  └── Flags
                    │  │     └── OBEX version 1.0
                    │  └── Packet length (7)
                    └── CONNECT opcode

Printer → Client:  A0 00 07 10 00 XX XX
                    │              │
                    └── SUCCESS    └── Printer's max packet length
```

**Step 2: OBEX PUT (image data)**

For small images (fits in one packet):

```
Client → Printer:  82 [length] [Name header] [Type header] [Length header] [End-of-Body header]
                    │
                    └── PUT FINAL opcode

Printer → Client:  A0 00 03
                    │
                    └── SUCCESS
```

For large images, the data is sent in chunks using OBEX BODY headers with opcode `0x02` (PUT), and the final chunk uses opcode `0x82` (PUT FINAL) with an End-of-Body header. The printer responds with `0x90` (CONTINUE) after each intermediate chunk.

**Step 3: OBEX DISCONNECT**

```
Client → Printer:  81 00 03
Printer → Client:  A0 00 03
```

### OBEX Headers Used

| Header ID | Type | Value |
|-----------|------|-------|
| `0x01` | Unicode string | Filename: `img.jpg` |
| `0x42` | Byte sequence | MIME type: `image/jpeg\0` |
| `0xC3` | 4-byte integer | File length in bytes |
| `0x48` | Byte sequence | Body (intermediate image chunk) |
| `0x49` | Byte sequence | End-of-Body (final image chunk) |

## Image Format

The printer expects a JPEG image with the following properties:

| Property | Value |
|----------|-------|
| Width | 640 pixels |
| Height | 1616 pixels |
| Format | JPEG |
| Quality | 100 |
| Rotation | 180° (printer feeds bottom-first) |
| Color space | RGB |

The physical ZINK paper is 2×3 inches. The printer consumes roughly 345 pixels of margin at the top and bottom during the printing process (the ZINK chemical activation area). The **visible print area** is approximately 640×925 pixels in the center of the image. Content placed in the top or bottom ~345px will be cropped.

```
┌──────────────────┐
│  ~345px cropped   │  ← consumed by printer mechanism
├──────────────────┤
│                  │
│   ~925px visible │  ← your actual photo appears here
│                  │
├──────────────────┤
│  ~345px cropped   │  ← consumed by printer mechanism
└──────────────────┘
       640px
```

The `ivy1_print.py` script handles all image preparation automatically — just pass any image file and it will be scaled, cropped, and formatted correctly.

## Differences from Canon Ivy 2

| | Ivy 1 (PV-123) | Ivy 2 |
|---|---|---|
| Image transfer | OBEX Object Push (standard) | Custom RFCOMM protocol |
| Start code | `0x1B2A` (status channel) | `0x430F` |
| SPP commands | Informational only | Required for print flow |
| Print trigger | Just send OBEX PUT | START_SESSION → GET_STATUS → PRINT_READY → raw data |
| Bluetooth | Classic BT + OBEX | Classic BT RFCOMM only |
| Python dependencies | None (native sockets) | PyBluez |

## Troubleshooting

**"Could not find OBEX service"** — Make sure the printer is powered on, paired in Windows Bluetooth settings, and not connected to your phone's Canon Mini Print app (only one device can connect at a time).

**Image prints but is cropped** — The visible area is ~640×925 in the center. Make sure your content isn't in the top/bottom margins.

**Script hangs on connect** — The printer may have gone to sleep. Press the power button to wake it, then retry.

## How This Was Reverse Engineered

1. Captured Bluetooth HCI traffic between the Canon Mini Print Android app and the printer using Android's built-in HCI snoop logging
2. Analyzed the traffic in Wireshark to identify RFCOMM channels and protocol framing
3. Decompiled the Canon Mini Print APK (v3.8.3) using JADX to find the exact protocol implementation
4. Key classes from the decompiled app: `OPPConnection.java`, `Obex.java`, `PrintImageTask.java`, `BulkTransferBase.java`
5. Confirmed OBEX Object Push works by sending a test JPEG via Windows Bluetooth File Transfer
6. Implemented the OBEX protocol in Python using native Bluetooth sockets (`socket.AF_BLUETOOTH`)