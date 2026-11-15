# StickerPix printer protocol

This reference describes the implementation verified with a StickerPix `CSTP401`,
firmware `1.0.31_0033`. It is an implementation reference rather than an
official firmware specification. For everyday use, start with
[Bluetooth setup](BLUETOOTH.md) and [sheet preparation](SHEET-WORKFLOW.md).

Packet encoding is in [protocol.py](../src/crycut/protocol.py), connected requests
in [transport.py](../src/crycut/transport.py), upload preparation in
[sheets.py](../src/crycut/sheets.py), and job monitoring in
[jobs.py](../src/crycut/jobs.py). Reference packets and print files
are retained as [regression fixtures](../tests/fixtures/README.md).

## Transport

The printer exposes a classic Bluetooth Serial Port service:
`00001101-0000-1000-8000-00805f9b34fb`. The client queries SDP for its RFCOMM channel
rather than assuming an endpoint. The tested printer advertised channel 2.

Bluetooth bonding/link encryption and application encryption are separate.
BlueZ manages the Bluetooth connection; `crycut` implements the application
handshake below. Pairing-free operation has not been verified. No account token
or cloud request is involved.

RFCOMM endpoint numbers are distinct from the **application channel byte** inside
each packet:

| Application channel | Command | Meaning |
| --- | --- | --- |
| `0xFF` | `0x10` | Client hello |
| `0xFF` | `0x11` | Printer key-exchange parameters |
| `0xFF` | `0x12` | Client public value and confirmation |
| `0xFF` | `0x13` | Handshake result |
| `0x01` | `0x06` / `0x07` | JSON request / response |
| `0x02` | `0x06` | File upload |
| `0x02` | `0x07` | Also observed for printer error events |

## Packet framing

Packets contain a 20-byte header, payload, checksum byte, and final `0x7E`.
Multibyte header fields are little-endian.

| Offset | Size | Meaning or observed value |
| --- | ---: | --- |
| 0 | 1 | Start marker `0x7E` |
| 1–2 | 2 | Fixed `64 00` |
| 3 | 1 | Application channel |
| 4 | 1 | Command |
| 5 | 1 | Payload type: `0x03` for JSON, `0x02` for file data |
| 6–9 | 4 | Zero in generated packets; semantics unresolved |
| 10–13 | 4 | Sequence |
| 14–15 | 2 | Fragment count |
| 16–17 | 2 | Fragment index |
| 18–19 | 2 | Length and flags |

The low ten flag bits contain payload length. Bits 10–12 contain the encryption
indicator (`5` for AES), and bit 13 is set for file transfer. The checksum is the
sum modulo 256 of bytes from offset 1 through the payload's last byte.

Parse packets by length. Encrypted payloads may contain `0x7E`, and RFCOMM reads
may split a packet or return several packets together.

File uploads require payload type `0x02`. Using JSON type `0x03` for a file
payload can produce error `-8012`. Regression tests compare all 713 complete
upload packets in the independently constructed fixture, including headers and ciphertext.
Fixture payloads are encrypted with a mock test key; see the
[fixture guide](../tests/fixtures/README.md).

## Application handshake

1. Send `hello` on application channel `0xFF`, command `0x10`.
2. Read the 36-byte DH-info body from command `0x11`: four bytes of
   NUL-terminated hexadecimal generator text, sixteen ASCII hexadecimal bytes
   for the modulus, and sixteen for the printer's public value `A`.
3. Observed parameters are `g = 2`, `p = E6969D3D495BE32C`. Choose a fresh private
   value `b`; calculate `B = pow(g, b, p)` and `shared = pow(A, b, p)`.
4. Encode `shared` as uppercase hexadecimal **text**, then right-pad it with NUL
   bytes to 16 bytes. This is the AES key; do not decode the text as binary hex.
5. Send command `0x12`: `B` as sixteen uppercase hex characters, left-padded with
   ASCII zeroes, followed by AES encryption of the original sixteen ASCII bytes
   containing `p`.
6. Require `ok` with optional trailing NUL bytes in command `0x13` before enabling
   application requests. A rejected or incomplete handshake leaves no usable key.

Application encryption is AES-ECB with zero padding to the next 16-byte boundary.
An already aligned payload does not receive an extra padding block. The key is
negotiated per connection; there is no hardcoded device key in the runtime.

## JSON requests and properties

A property request looks like:

```json
{"id":1,"method":"get-prop","params":["printer-state","printer-sub-state","printer-state-alerts"],"spec-type":1}
```

An observed idle response is:

```json
{"result":["20","2000",":0:0"],"id":1}
```

Replies are matched by request ID. Unsolicited messages use a `method` field;
the client retains them while waiting for the matching response. Plaintext error
events have been observed even after the encrypted handshake.

| Property | Interpretation |
| --- | --- |
| `printer-state` | Overall device state |
| `printer-sub-state` | Current operation stage |
| `printer-state-alerts` | Raw alert string; `:0:0` observed during normal idle/printing |
| `ribbon-info` | Six-element array; element 2 holds the observed remaining-print count |
| `firmware-revision` | Firmware version |
| `model`, `serial-number` | Device identity |
| `auto-off-interval` | Seconds; observed `1200` means 20 minutes, `0` means never |

A fresh cartridge reported 36 prints. One completed sheet reduced the count to
35. A missing cartridge reported `-1`; the CLI treats this as unavailable rather
than claiming the cartridge is empty. Consumption on failed or cancelled jobs
has not been established. The displayed cartridge capacity comes from local
configuration, not a discovered capacity field.

Device state mappings:

| Code | Meaning |
| --- | --- |
| 10 | Initializing |
| 20 | Idle |
| 30 | Sleeping |
| 40 | Working |
| 50 | Shutting down |
| 60 | Error |

## Submit and upload a sheet

The implementation submits one `combo-job` containing `print-job` and `cut-job`
entries. The observed media settings are:

| Setting | Value |
| --- | --- |
| Copies | Positive integer, default 1; identical in both entries |
| Job type | 600, print and cut |
| Media size | 5013 |
| Media type | 2030 |
| Image format | 9, JPEG |
| Cut format | 18, PLT text |

Each nested entry includes its document name, file path, byte length, and those
media settings. The printer returns a job ID before file upload begins.
The Android SDK passes the requested copy count to both nested entries and
uploads the files once. Crycut follows that path; multiple-copy behavior still
needs a physical verification run on the documented firmware.

Despite the request's entry order, the uploaded data is **PLT bytes followed by
JPEG bytes**. Split it into chunks of at most 892 bytes. Prefix each chunk with
the assigned job ID as a four-byte little-endian integer, then encrypt/pad it.
Send on application channel 2, command `0x06`, payload type `0x02`, with the
file-transfer flag and one-based sequence/fragment indices.

The offline wordmark fixture transfers 2,132 PLT bytes and 633,446 JPEG bytes in
713 fragments. Its JPEG is 1200 × 2100 pixels. Both files were exported by crycut
from the author's artwork; expected packets were constructed separately using
a mock test key. Runtime jobs use the selected project's files and a freshly
assigned job ID.

## Monitoring and completion

`get-job-info` takes `{"job-id":30}` and returns a list containing the matching
job. The client also polls printer properties for the current stage.
`printing-page-number` is the printer's current one-based copy number (`0`
during initial preparation). The Android app uses it directly to advance its
page display. Crycut resets the per-sheet estimate when this number increases.
Single-copy captures show `0` becoming `1` before printing and remaining `1`
through completion.

Alert `8401` is presented as `Paper near blade`. While it is present, Crycut
hides the countdown and keeps polling the same job. The Android app also has
an automatic clearance-monitoring path; Crycut does not send `resume-printer`.
Clearing the alert alone does not advance the copy counter or reset its timer.

| Job state | Meaning |
| --- | --- |
| 1 | Waiting |
| 2 | Started |
| 3 | In progress |
| 4 | Temporarily unavailable |
| 5 | Interrupted |
| 6 | Terminating |
| 7 | Failed |
| 8 | Cancelled |
| 9 | Completed |

An unsolicited `event.combo-job-finish` can arrive during either query, including
when the query's own response never arrives. A matching terminal notification
is authoritative. Unknown or missing completion is never treated as success,
and a timed-out submission is never automatically retried.

Observed stages:

| Sub-state | Meaning |
| --- | --- |
| 2000 | Idle/starting |
| 3015 | Preparing (general label; exact operation unresolved) |
| 3016 | Decoding |
| 3017 | Feeding paper |
| 3018 | Printing yellow |
| 3019 | Printing magenta |
| 3020 | Printing cyan |
| 3021 | Protective layer |
| 3025 | Returning home |
| 3028 | Feeding for cutting |
| 3029 | Finding origin |
| 3030 | Cutting |
| 3031 | Retracting blade |

The printer's `cut-contours` grows during a job. `cutting-progress` reached 28
for 28 wordmark outlines and 32 for 32 tech-icon outlines. Submitted projects
therefore use their validated PLT outline count as the fixed total. A standalone
monitor shows only completed cuts when no project total is available.

Printer-reported time fields match milliseconds in the observed runs. Selected
measurements and the estimate model are described in [CALIBRATION.md](CALIBRATION.md#time-estimates).
Power-off notifications are not implemented or relied upon.

## Error codes

Error `-8012` was observed when uploading file data with an incorrect payload
type. The client reports other printer errors as received; their meanings have
not been verified on hardware.

## PLT cutter commands

The reference PLT file is ASCII and begins `IN VER0.1.0 KP39`. A contour consists of
an absolute `U{x},{y}` travel followed by `D{x},{y}` cutting coordinates. The
interpretation is consistent with blade-up/blade-down motion and successful
cuts; firmware definitions are not available.

One reference rectangular contour is:

```text
U631,2060 D631,2060 D231,2060 D231,3866 D631,3866 D631,2060 D631,2060
```

Repeated start/end points are retained by the exporter. The file ends with
` U6476,0  @ `, including the original spacing. That final travel is not a sticker
outline. `KP39`, exact physical cutter units, and parking semantics remain
partly unresolved. Coordinate transforms and the orientation correction are
documented in [CALIBRATION.md](CALIBRATION.md).
