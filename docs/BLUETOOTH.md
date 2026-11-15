# Bluetooth setup

`crycut` talks to the StickerPix using **classic Bluetooth RFCOMM**, the serial
port service provided by the printer. It does not use BLE/GATT.

Reference printer: StickerPix model `CSTP401`, firmware `1.0.31_0033`.

## Requirements

- A Linux Bluetooth adapter with classic Bluetooth support.
- BlueZ with `bluetoothctl` and `sdptool`, installed through your distribution.
- System Python 3.11 or newer with Bluetooth socket support.
- The project's Python dependencies: `uv sync --locked`.

BlueZ is Linux's Bluetooth stack. Python uses a normal RFCOMM socket; `sdptool`
finds the printer's advertised Serial Port service. The application handles the
printer-specific handshake and commands.

`.python-version` selects `/usr/bin/python3` for Bluetooth socket support.
Python builds vary in which socket families they support. Dependencies live in
the project's isolated `.venv/`.

Check the interpreter without contacting the printer:

```bash
uv run python -c 'import socket; print(hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM"))'
```

The result should be `True`. If it is `False`, install a system Python build
with Bluetooth support and recreate the project environment with that interpreter.
Adding a Python Bluetooth library does not enable missing socket support in the
interpreter.

## Select your printer

Turn on the printer and the notebook's Bluetooth, then run:

```bash
uv run crycut setup
```

Setup uses `bluetoothctl` to scan for classic Bluetooth devices and include
printers already known to BlueZ. It shows matching StickerPix names and MAC
addresses. A single printer is selected automatically; with several printers,
choose one from the numbered list. A known device may be offline, so setup
verifies the connection and reads status before saving anything.

Settings are saved in `config/printer.json`, which is ignored by Git. Re-running
setup for the same printer preserves its configured ribbon capacity. Replacing
a different printer or invalid configuration requires confirmation in the
terminal. A failed connection leaves the saved configuration unchanged.

If you already know the address, skip scanning:

```bash
uv run crycut setup --mac AA:BB:CC:DD:EE:FF
```

Use `--timeout 20` for a longer scan. All printer commands use the single saved
configuration in `config/printer.json`; run setup again to change printers.
The `--mac` option is only available during setup.
A printer with a custom name that does not contain `StickerPix` needs `--mac`.
For manual configuration, copy [printer.example.json](../config/printer.example.json)
to `config/printer.json` and replace the example address.

### Pairing

Setup does not depend on Ubuntu's Bluetooth UI. Existing Linux Bluetooth
pairings can be reused. OS-level pairing and the application's key exchange are
separate.

If the printer is not paired and the connection requires it, use your desktop
Bluetooth settings or the terminal:

```bash
bluetoothctl pair AA:BB:CC:DD:EE:FF
```

Follow any pairing prompts, then run setup again. Only use `pair` for an unpaired
device; do not repeat it for an existing pairing. Setup does not change adapter
power, remove pairings, or submit print jobs. The Bluetooth commands are described
in the [BlueZ reference](https://github.com/bluez/bluez/blob/master/doc/bluetoothctl.rst).

After setup:

```bash
uv run crycut status
```

Each status or print command connects and disconnects on its own. There is no
persistent connection command or background service. Project listing, export,
and `print` without `--send` also work before setup, without Bluetooth. A Cricut
account or phone application is not required.

## Channels and connection ownership

The default is to query the saved address for its Serial Port service.
Do not assume that every device or firmware
uses the same endpoint. `--channel` exists as an explicit troubleshooting override
on `status`, `print`, and `monitor`. If a print needs this override, use the
same `--channel` when reconnecting with `monitor`.

Run one printer command at a time. An existing connection from another program
or device can prevent the notebook from connecting. When a print is running,
its progress monitor already owns the connection; avoid starting `status` in a
second terminal.

The protocol also has internal channels for commands and uploads. Those are
separate from the RFCOMM endpoint; see [the protocol reference](PROTOCOL.md).

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Connection unavailable | Printer power, Bluetooth enabled, correct saved address, and no competing connection. |
| No Bluetooth adapter | Check the adapter in desktop settings, `bluetoothctl show`, and `rfkill list bluetooth`. |
| `sdptool` missing | Install your distribution's package providing the BlueZ SDP tools. |
| Service lookup times out | Move closer, check pairing, and retry after clearing a stale connection. |
| Printer is not idle | Let its current job finish; use `monitor JOB_ID` for a known job. |
| A print command disconnects | Check the printer and any assigned job ID before submitting again. |

A stale connection can be disconnected in desktop settings, or with
`bluetoothctl disconnect AA:BB:CC:DD:EE:FF` using your printer's address. If the
printer remains unresponsive, try turning it off and on again. After a
restart, previous job IDs may no longer be available.

## Progress and logs

```bash
uv run crycut print my-sheet --send
uv run crycut monitor 30
```

The first command submits one sheet and monitors it. The second reconnects to
job 30 without submitting anything. Standalone `monitor` is a recovery and
debugging tool for an interrupted session, timeout, or lost connection; normal
printing already monitors progress automatically. Ctrl+C and `--max-wait` stop
monitoring; they do not cancel the physical job. Reconnect to the same saved
printer before changing its setup, since the job ID belongs to that printer.

New event logs and protocol transcripts are written to `logs/`. If reporting a
problem, include the command, error, printer model, firmware, and relevant event
lines. Review logs before sharing: protocol transcripts can contain artwork and
device or cartridge identifiers.
