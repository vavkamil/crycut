# crycut

StickerPix — print and cut from Linux.

A small Python CLI for preparing sticker sheets and sending them directly to a
Cricut StickerPix printer over Bluetooth. Create a design, generate its image
and cut outlines, and follow the print progress from the terminal. Everything
runs locally; no Cricut account or Design Space is required.

## Quick start

Requires Linux, Python 3.11+, `uv`, BlueZ (`bluetoothctl` and `sdptool`), and
Inkscape for SVG rendering.

```bash
uv sync --locked
```

The project uses `/usr/bin/python3` for Bluetooth socket support. `uv` installs
Python libraries and development tools into the local `.venv/`; it does not
change system packages.

Turn on your printer and the notebook's Bluetooth, then find and save it:

```bash
uv run crycut setup
uv run crycut status
```

Setup discovers printers, checks the selected connection, and saves
`config/printer.json` locally. If needed, pair the printer first; see
[Bluetooth setup](docs/BLUETOOTH.md#select-your-printer).

Create a project from the blank SVG template:

```bash
uv run crycut new my-sheet
```

Edit `projects/my-sheet/design.svg`, keeping the 96 × 170 mm canvas and a
transparent background. Export the sheet and inspect it:

```bash
uv run crycut export my-sheet --border 1.25
uv run crycut print my-sheet
```

Open `projects/my-sheet/preview.png` to check the artwork and cut outlines.
The pink guides appear only in the preview. Load sticker media, then send one
sheet to the printer:

```bash
uv run crycut print my-sheet --send
```

`print` without `--send` only checks the files and shows a summary. Sending a
sheet also monitors upload, printing stages, cutting, and completion. The
remaining time is an estimate; completion comes from the printer.

See [Preparing sticker sheets](docs/SHEET-WORKFLOW.md) for canvas dimensions,
borders, colors, and previews.

For connection setup and troubleshooting, see [Bluetooth setup](docs/BLUETOOTH.md).

## Commands

```bash
uv run crycut projects                            # List local projects
uv run crycut new my-icons --png                  # Start with a transparent PNG
uv run crycut new my-artwork --from artwork.svg   # Copy existing artwork
uv run crycut export my-sheet --border 0          # Cut along the artwork's edges
uv run crycut monitor 30                          # Reconnect to an existing job
uv run crycut --help
uv run crycut export --help
```

Cut outlines are detected from the transparent artwork. A positive `--border`
adds an outward margin following the shapes. Use `--border-color '#ffffff'`
and `--background '#000000'` to set the printed margin and sheet background.
Re-exporting remembers the project's last settings unless options override them.

A project keeps its artwork and latest export together. Editing the artwork
marks the export as out of date; export again before printing. To make a separate
variation, use `export my-sheet --output projects/my-variation`.

Each status or print command establishes its own connection to the saved printer.
The transport is classic Bluetooth RFCOMM. No separate connection command is
needed. Run printer commands one at a time.

Ctrl+C stops monitoring but does not cancel the physical print. Use the assigned
job ID with `monitor` to check an interrupted run before sending another sheet.
This is a recovery/debugging command; normal printing already monitors progress.
See [progress and logs](docs/BLUETOOTH.md#progress-and-logs).

## Local files

- `projects/` contains one folder per sheet, including the supplied
  [wordmarks](projects/wordmarks/) and [tech-icons](projects/tech-icons/) projects.
- `templates/` contains the blank SVG and PNG canvases.
- `config/` contains placement and timing settings; your local `printer.json`
  is ignored by Git.
- `logs/` contains print events and protocol transcripts; it is ignored by Git.
- `src/crycut/` contains the CLI, artwork generation, and printer protocol code.
- `tests/` contains offline tests and protocol/geometry reference fixtures.
- `docs/` contains user guides, technical references, and supporting measurements.

An exported project contains:

```text
projects/my-sheet/
  design.svg        # Editable artwork, or design.png
  preview.png       # Artwork with cut guides
  sheet-print.jpg   # Printer image
  sheet-cut.plt     # Cutter instructions
  export.json       # Export settings and file checksums
```

SVG projects also contain an intermediate `sheet-print.svg`. The manifest is
local metadata. Source and generated-file checksums
are verified before printing.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check .
```

Tests run offline using temporary projects, simulated printer connections, and
protocol fixtures. They include byte-for-byte upload comparisons and
export geometry checks. Physical print and cut alignment is checked separately
on the printer.

For focused changes, run the relevant test file, for example
`uv run pytest tests/test_monitor_failures.py`.

## Prerequisites

- Linux with a Bluetooth adapter supporting classic Bluetooth.
- System Python 3.11+ with Bluetooth socket support, and `uv`.
- BlueZ, including `bluetoothctl` for setup and `sdptool` for connections.
- Inkscape for SVG rendering; PNG designs do not require it.

See the [documentation index](docs/README.md) for setup, design, calibration,
and protocol guides. See [Contributing](docs/CONTRIBUTING.md) for the code
structure, verification steps, and reference data.

## Disclaimer

crycut was created so its author could print and cut stickers on hardware they
own from Linux, which the official
[Design Space client does not support](https://help.cricut.com/hc/en-us/articles/360009556033-System-Requirements-Design-Space).

It is shared to help other Linux owners use their printers and learn about
printer interoperability. The implementation and technical documentation focus
on preparing compatible print/cut data and communicating with the printer over
Bluetooth.

This is an independent project, not affiliated with or endorsed by Cricut.
