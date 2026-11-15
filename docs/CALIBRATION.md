# Placement and calibration

The exporter maps one 96 × 170 mm design into two coordinate systems: the
1200 × 2100 print image and the printer's cutter coordinates. They use separate
transforms. All current coefficients are in
[the placement profile](../config/profiles/stickerpix-v2.json). This is runtime
configuration containing the coordinate mappings and PLT framing needed to
export any sheet, regardless of its artwork.

## Dimensions and evidence

The supported design canvas is 96 × 170 mm and the output JPEG is
1200 × 2100 pixels. These dimensions describe the software's input and output;
they do not establish the cutter's mechanical travel limits. The
[placement profile](../config/profiles/stickerpix-v2.json) defines the actual
mapping between design coordinates, image pixels, and cutter positions.

The PNG template's 1152 × 2040 resolution is a convenient 12 pixels/mm input
convention. A 96 × 170 mm image at nominal 300 DPI is approximately 1134 × 2008
pixels; it is also accepted as input. Neither size determines the final JPEG's
physical DPI or its blank strips. Physical stock dimensions and feeding margins
must not be inferred from raster dimensions alone.

## Image placement

The reference wordmark sheet contains 28 rectangles: two columns, fourteen rows,
45 × 10 design mm per rectangle, with 2 mm gaps and outside margins. Fitting
source edges to visible rectangle edges in the reference JPEG gives:

```text
JPEG_x = 11.695795937647613 * x + 34.6017949929146
JPEG_y = 11.674659518087138 * y + 57.134652123004514
```

Here `x` and `y` are design coordinates in millimeters. The largest edge-fit
residuals are about 0.305 and 0.366 pixels. The
[placement evidence](evidence/placement.json) preserves the measurement points,
fitting method, and source checksum. Regression tests compare each rendered
word's visible bounds with the retained
[numeric measurements](../tests/fixtures/wordmark-placement.json) within one pixel.
The original app-generated JPEG and PLT are not included.

These values fit the placement and scaling of the reference sheet. They are
empirical measurements, not universal physical calibration constants.

## Orientation

The wordmark grid is symmetric under a 180-degree rotation, so matching its
rectangle positions alone cannot establish the physical orientation. An
asymmetric tech-icon sheet demonstrated a 180-degree cut-layout error in the
previous profile.

The current **stickerpix-v2** profile applies the original cutter transform
to `(96-x, 170-y)`, reversing both axes around the design center `(48, 85)`:

```text
cutter_X = -40.06691331923889 * y + 6961.794774992448
cutter_Y =  40.14123760037788 * x +   92.97059518186143
```

The JPEG mapping, PLT header, and final parking command are unchanged. Regression
tests check an asymmetric triangle, every tech-icon contour's rotation, unchanged
JPEG bytes, and the wordmark rectangle positions. Earlier files are retained in
[orientation-v1 fixtures](../tests/fixtures/orientation-v1/) for those tests.

The reference wordmark cuts span design coordinates x=2..94 and y=2..168 mm.
The exporter permits outlines anywhere within the full design canvas, but areas
outside that measured envelope extrapolate the fitted mapping. The exact
firmware cutter units, blade compensation, `KP39` setting, and parking-command
meaning are still not fully established.

## Time estimates

Selected printer-reported timings are in [timing.json](evidence/timing.json).
All values below exclude upload:

| Sheet/run | Printing | Cutting | Combined |
| --- | ---: | ---: | ---: |
| Wordmarks, mobile client (job 26) | 120.363 s | 110.520 s | 230.883 s |
| Wordmarks, crycut (job 29) | 106.147 s | 110.562 s | 216.709 s |
| Tech-icons, crycut (job 30) | 105.907 s | 82.802 s | 188.709 s |

The model in [config/timing.json](../config/timing.json) uses a mean printing
baseline of about 113 seconds. Cutting scales with pen-down distance plus a
provisional 0.5 seconds per outline. The assumed overhead and variation range
are not measured guarantees; curves and different designs may behave differently.
The tech-icon estimate was 189.7 seconds, close to that run's reported duration.

Completion always comes from printer status. Countdown updates do not send extra
Bluetooth requests, and elapsed time reported as paused is excluded from the
remaining estimate. Reconnecting to an existing job does not invent a start time.
