# Preparing sticker sheets

Each project contains one editable design and its generated print files. Create
one from the SVG template, a transparent PNG template, or existing artwork:

```bash
uv run crycut new my-sheet
uv run crycut new my-png-sheet --png
uv run crycut new imported-sheet --from artwork.svg
```

Use an unused name starting with a letter or number, containing only ASCII
letters, numbers, hyphens, and underscores (for example, `my-sheet` or `Sheet_2`).
Spaces and directory separators are not allowed in new project names.

For export and print, a bare name selects an existing project in `projects/`
before checking the working directory. For example, `config` selects
`projects/config` when that project exists; use `./config` to explicitly select
a directory in the working directory. Absolute paths also work.

The supplied [wordmarks](../projects/wordmarks/)
and [tech-icons](../projects/tech-icons/) are examples of rectangular labels and
shape-following borders.

## Canvas dimensions

Design on a **96 × 170 mm** transparent canvas. Keep the full page when saving;
do not crop away its transparent margins.

| Input | Canvas |
| --- | --- |
| SVG | `width="96mm" height="170mm" viewBox="0 0 96 170"` |
| PNG template | 1152 × 2040 pixels |
| Other PNG sizes | Same 96:170 aspect ratio, within 0.5%; 1134 × 2008 is accepted. |

One SVG coordinate unit represents one design millimeter. PNG DPI metadata is
ignored: the entire image is fitted to the design canvas. The PNG template uses
12 pixels/mm as a convenient convention; it is not a pixel crop of the final
printer image.

The exported JPEG is **1200 × 2100 pixels**. Placement inside that image comes
from the calibration profile. Do not resize the design to that image size or
add assumed sheet-edge offsets yourself. See [placement and calibration](CALIBRATION.md)
for the distinction between the canvas, raster, and physical stock.

## Artwork and transparency

Keep the page background transparent. An opaque background within an individual
sticker is part of its silhouette. For example, the wordmark project's black
rectangles produce rectangular cuts; text alone on transparency would produce
separate letters and dots.

For SVG, keep the template's page dimensions and avoid a transform on the root
SVG element. Convert text to paths for consistent rendering across computers.
Embedded or linked raster images in SVG are not supported; export the full
sheet as a transparent PNG instead.

Export detects outlines from the artwork's alpha channel. Interior holes are not
cut. Disconnected components remain separate unless an added border joins them.
A fully opaque page is rejected because it provides no sticker silhouettes.

### SVG scope and known limitations

SVG input is intended for simple, self-contained vector artwork like the
`wordmarks` example. Crycut checks the canvas and generated cut geometry, but
does not validate every SVG feature or guarantee that arbitrary imported SVGs
render faithfully.

- Put colors and other styling directly on elements, using attributes such as
  `fill="#ff0000"` or inline `style="fill:#ff0000"`. Stylesheet blocks (`<style>`)
  and external stylesheets are unsupported. Artwork CSS is not isolated from
  the generated sheet: a rule such as `rect { fill: red; }` can override the
  requested background, and other selectors can affect borders or placement.
- Keep referenced vector definitions inside the same SVG. External references
  such as `<use href="asset.svg#shape"/>` are unsupported. Export renders temporary
  copies of the SVG without bundling dependencies, so relative references can
  disappear silently, leaving missing artwork and missing cuts.

These unsupported inputs are not comprehensively detected or rejected. A
successful export does not prove that all source artwork was preserved. Compare
`preview.png` with the original design before printing, checking colors, sticker
count, and cut outlines.

For artwork using stylesheets, external assets, or embedded images, export the
full page from your editor as a transparent PNG and use that as the project
source. Keep the 96:170 aspect ratio and transparent margins; PNG artwork uses
the same automatic outline detection and border options.

## Borders and background

```bash
uv run crycut export my-sheet --border 0
uv run crycut export my-sheet --border 1.25
```

`--border 0` follows the outer silhouette. A positive value expands the outline
by that many design millimeters and prints a margin beneath the artwork.

```bash
uv run crycut export my-sheet \
  --border 1.25 \
  --border-color '#ffffff' \
  --background '#000000'
```

The default border and sheet background colors are white. Colors must use
`#RRGGBB`; quote them in the shell. `--background-color` is an alias for
`--background`. Neither option recolors the original artwork. Border color has
no effect when the border is zero.

The sheet background is applied after outline detection, so a black print
background does not join separate stickers. Keep the source transparent even
when choosing an opaque print background.

Allow room for the expanded border inside the canvas. For separate stickers,
leave a gap greater than twice the border width between their original shapes.
Touching expanded borders can merge into one cut outline. Shapes extending
outside the canvas, ambiguous touching contours, and cuts that collapse after
rounding to cutter coordinates are rejected.

Tracing samples at 24 pixels/mm, uses alpha values of at least 128, and simplifies
curves with a 0.02 mm tolerance. Very fine or faint details may not survive that
process. Inspect the preview when working with small details.

## Export and preview

Re-exporting a project remembers its previous border and background settings;
explicit flags override them. The editable design is preserved. To create a
separate variation:

```bash
uv run crycut export my-sheet --output projects/my-variation
```

An export contains:

| File | Purpose |
| --- | --- |
| `design.svg` or `design.png` | Editable source; exactly one per project. |
| `sheet-print.jpg` | 1200 × 2100 printer image. |
| `sheet-cut.plt` | Closed cutter paths. |
| `preview.png` | Print image with pink cut guides, for inspection only. |
| `sheet-print.svg` | Intermediate layout for SVG inputs only. |
| `export.json` | Local settings, profile name, and file checksums. |

The preview's pink lines are never printed. Export runs locally and does not
connect to the printer. `export.json` stores the settings and checksums used
to validate the generated files.

```bash
uv run crycut projects
uv run crycut print my-sheet
```

These commands validate the saved export without printing. Open `preview.png`
to inspect it; `print` does not open the image automatically. A changed source
shows as `Needs export` until its files are regenerated.

## Print and monitor

Load sticker media and send the selected sheet explicitly:

```bash
uv run crycut print my-sheet --send
uv run crycut print my-sheet --copies 3 --send
```

The sender checks source and output checksums, JPEG dimensions, the placement
profile, and closed cut geometry before connecting. `--copies` defaults to one
and accepts an integer from 1 to 36. It also works in offline previews without
`--send`. All copies share one job ID and one upload of the image and cut paths.
The display shows upload percentage, printer stages, completed cuts, and
estimated time per sheet.

The countdown updates locally once per second; status is polled every two seconds
by default. Estimates do not determine completion. Reported pauses suspend the
estimate, and the printer must confirm the final result. `Copies: 0/3` means
preparation; the printer advances the counter to `1/3`, `2/3`, and `3/3`.
Each new copy resets the countdown. While `Paper near blade` is shown, remove
the finished sheet; the countdown stays hidden until the alert clears.
The client keeps polling without sending a resume command.

The `Progress` bar below Status estimates overall completion across all copies.
It advances within each sheet, refreshes every second on a terminal, and freezes
during reported pauses. Each sheet contributes an equal share of the bar. If a
sheet takes longer than estimated, its share stops advancing until the next
copy starts. The bar never moves backward and reaches 100% only when the printer
confirms the job completed.

The default monitoring limit is 15 minutes per requested copy. `--max-wait`
overrides the limit for the entire job in seconds.

For a submitted project, the cut total comes from the PLT. The display tracks
completed outlines against that fixed total for the current sheet.

Ctrl+C stops monitoring without cancelling the print. Let the printer finish
and check it before sending another sheet.

## Simulate a print

Use an exported project to try the live display or capture screenshots without
a printer:

```bash
uv run crycut print my-sheet --simulate
uv run crycut print my-sheet --simulate --speed 1
uv run crycut print my-sheet --simulate --copies 3
```

Playback defaults to 10× speed; `--speed 1` runs in real time. The simulation uses
the same display as real printing, with modeled stages and timing based on the
selected sheet. Multiple copies include a paper-removal pause that clears
automatically after 30 simulated seconds.

Simulation requires no printer setup, makes no Bluetooth connection, and writes
no files. Use `--simulate` on its own, without `--send`. Ctrl+C ends the demo.

## Placement

The exporter uses the `stickerpix-v2` placement profile. See
[placement and calibration](CALIBRATION.md#orientation) for the coordinate mappings.
