# Regression fixtures

These selected files let the offline tests verify the printer protocol and
geometry without a connected printer or external data.

## Wordmark upload job

`print-cut/` contains:

- `sheet-print.jpg`: a 1200 × 2100 image exported by crycut from the author's
  artwork in `projects/wordmarks/design.svg`.
- `sheet-cut.plt`: the corresponding 28 rectangular cut outlines exported by crycut.
- `upload-frames.jsonl`: an independently constructed `combo-job` request and
  all 713 expected upload packets, encrypted with the mock key `crycut-test-key!`
  and using mock job ID 42.

The JPEG and PLT match the supplied crycut wordmark export. The expected packets
are constructed from the documented wire format without importing crycut's
packet encoder. They check framing, checksums, sequence numbers, fragment
boundaries, padding, and PLT-before-JPEG payload order. They are offline test
vectors; they do not represent a recorded printer exchange. Runtime connections
negotiate a fresh session key and use the printer-assigned job ID.

The manual builder is [build_upload_fixture.py](build_upload_fixture.py):

```bash
uv run python tests/fixtures/build_upload_fixture.py
```

Tests read the fixed files and never run this builder. Only regenerate them for
an intentional fixture change, review the resulting bytes, and run the tests.

## Placement measurements

[wordmark-placement.json](wordmark-placement.json) retains numeric cutter
rectangle bounds and green wordmark pixel bounds measured from the reference
session. These independent expectations check the exporter without distributing
the original app-generated JPEG or PLT. Pixel bounds are relative to the listed
crop regions and allow one pixel of rendering variation.

## Orientation regression

`orientation-v1/` contains the earlier cutter transform and the tech-icon source,
JPEG, PLT, and manifest from before the 180-degree orientation correction. The
profile retains the numeric fields needed by tests.

Tests compare the corrected geometry with these original contours, require the
JPEG to remain unchanged, and ensure obsolete-profile exports cannot be sent as
current projects. These checks do not replace a physical alignment test; see
[calibration notes](../../docs/CALIBRATION.md#orientation).

## Legacy guide handling

`wordmarks-manual.svg` contains the old explicit cut-guide layer. It verifies
that guide strokes are excluded from printed artwork and automatic tracing.
Users do not need to create a manual cut layer.

## Updating fixtures

Treat the vectors, measurements, and historical geometry as fixed expectations.
Do not regenerate expected packets or historical geometry just to make a failing
test pass. For a deliberate format change, explain the new expectation in the test and
preserve independent measurement checks.

Public tests depend only on these fixtures, the two supplied projects,
templates, and configuration. Placement measurements and timing observations
are described in the [reference data guide](../../docs/evidence/README.md).
