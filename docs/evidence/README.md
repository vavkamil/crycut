# Reference data

These files support the protocol and geometry documentation. They are not
runtime dependencies. They let contributors trace a number to its source and
compare a replacement implementation with the same inputs.

| File | Contents | How to use it |
| --- | --- | --- |
| [placement.json](placement.json) | Measured edges, fitting method, residuals, and source checksum | Refit the pixel mapping and compare the resulting scale and offset. |
| [timing.json](timing.json) | Selected job durations, outline counts, and progress readings | Compare timing estimates with printer-reported execution. |
| [Regression fixtures](../../tests/fixtures/README.md) | Crycut exports, constructed upload packets, placement measurements, and earlier geometry | Reproduce the packet and export checks offline. |

## Provenance and limits

The protocol fixtures use crycut-generated files and a mock test key for offline
upload comparisons. Expected packets are constructed independently of the runtime encoder.
The [fixture guide](../../tests/fixtures/README.md) documents their provenance,
payloads, and mock metadata. Runtime connections negotiate a fresh key.

Device-state names in the protocol reference came from the application's
mappings. Printing stages, upload behavior, and the documented upload error
were observed in printer sessions.

Placement measurements come from the reference wordmark design and JPEG; the
original app-generated files are not bundled. Numeric bounds retain the
independent placement checks. See [calibration](../CALIBRATION.md) for the
transforms and limits.
Timing records cover three successful jobs, not a broad performance benchmark.

## Reproducing and extending checks

Run the [contributor checks](../CONTRIBUTING.md#development-setup) from the repo
root. All inputs needed by the offline tests are included in the repository.

When adding evidence, retain original reference bytes, identify the device and
firmware where relevant, describe any normalization, and add a focused test.
Keep measurements separate from inferred meanings.
