# Contributing

Develop and test from a checkout of this repository. User projects, templates,
and configuration live alongside `src/`; the CLI resolves them from that tree.

## Development setup

Follow the [requirements](BLUETOOTH.md#requirements), including Inkscape from the
[quick start](../README.md#quick-start) for SVG rendering, then run:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check .
```

The tests run offline and do not submit printer jobs. They use temporary
projects, simulated printer connections, and the bundled reference files.
A focused test can be run with `uv run pytest tests/test_orientation.py`.

## Coverage

Run the full suite with coverage, as CI does:

```bash
uv run pytest --cov
```

Coverage includes all modules in `src/crycut`, including modules that tests never
import. The terminal report shows missing lines and branches. There is currently
no minimum coverage threshold.

To also generate a browsable report:

```bash
uv run pytest --cov --cov-report=term-missing --cov-report=html
```

Open `htmlcov/index.html` to inspect uncovered code. Coverage data and generated
reports are ignored by Git. Use the report to identify missing behavior checks;
executing a line alone does not establish that its behavior is correct.

## Code structure

All implementation modules are in [src/crycut/](../src/crycut/).

| Area | Modules | Responsibility |
| --- | --- | --- |
| CLI | `cli.py`, `arguments.py`, `commands/` | Dispatch, argument parsing, output, and connection/log ownership |
| Projects | `projects.py`, `paths.py` | Editable artwork and repository locations |
| Configuration | `configuration.py` | Validate printer settings and address overrides |
| Export | `exporter.py`, `rendering.py` | Export workflow, SVG/raster rendering, and generated-file installation |
| Geometry | `geometry.py`, `placement.py`, `plt.py` | Alpha outlines, coordinate mapping, cutter encoding and validation |
| Sheets | `sheets.py` | Offline export validation, project summaries, and upload payload preparation |
| Discovery | `discovery.py` | BlueZ scan results and printer candidates |
| Protocol | `protocol.py`, `transport.py` | Packet encoding and encryption; SDP lookup and connected requests |
| Printer | `jobs.py`, `status.py` | Job submission, monitoring, and printer properties |
| Progress | `progress.py`, `timing.py` | Terminal progress and time estimates |
| Shared types | `models.py` | Configuration and geometry types |

Command adapters call reusable modules; reusable modules do not import the CLI
or `commands/`. Only command adapters and display code write terminal output.
Printer operations report events through callbacks, keeping monitoring separate
from terminal formatting. Packet construction and sheet validation do not open
Bluetooth connections.

`commands/logging.py` owns event and protocol logs. Failure to create the event
log prevents connection; later write failures are reported without interrupting
monitoring or replacing the printer's result.

Keep additions in the module that owns the behavior. Split a module when it mixes
responsibilities, rather than creating layers just to meet a line-count target.
The CLI loads command handlers on demand, so top-level help does not import the
rendering or Bluetooth implementation.

[config/profiles/stickerpix-v2.json](../config/profiles/stickerpix-v2.json) maps
96 × 170 mm design coordinates to JPEG pixels and cutter positions.
[config/timing.json](../config/timing.json) controls time estimates. Neither
requires external services or access to the reference files at runtime.

## Verifying changes

Keep expected results independent of the code being changed. Do not regenerate
reference packets or historical geometry just to make a test pass.

- **Protocol changes:** compare complete packets against the
  [upload fixtures](../tests/fixtures/README.md), including framing, sequence
  numbers, padding, and payload order. Cover timeout and terminal-event handling
  when changing monitoring.
- **Geometry changes:** check asymmetric shapes as well as rectangular grids.
  Compare both JPEG placement and PLT coordinates; a symmetric layout can hide
  a rotation error. Validate on hardware before claiming physical alignment.
- **Profile changes:** use a new profile name when changing the coordinate
  mapping and re-export affected projects. Keep earlier fixtures so regressions
  remain detectable.
- **Timing changes:** record printer-reported print and cut durations separately,
  with outline count and geometry. Completion must still come from the printer.

The [protocol reference](PROTOCOL.md), [calibration guide](CALIBRATION.md), and
[reference data](evidence/README.md) explain the current assumptions and checks.

## Hardware checks and reports

Use `uv run crycut print NAME` to inspect an export. Only add `--send` when
intending to print (one sheet by default, or the requested `--copies`). If a
submission or connection is interrupted,
check the assigned job before resubmitting; stopping monitoring does not cancel
printing.

For a reproducible report, include the command, printer model and firmware,
expected and actual behavior, and relevant event lines. For geometry issues,
include a small source design, export settings, profile name, and a photo or
measurement of the result. Review shared files for device identifiers or
private artwork. Keep new reference data minimal and document how it was obtained.
