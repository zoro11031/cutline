# Silhouette Control Center

Silhouette Cameo cut templates and cutting for proxy card sheets, on Linux.

The third leg of the Printing suite. [`proxypress`](../proxypress) makes the
sheet, [Canon Suite](../canon-suite/canon-control-center) puts it on 330gsm black-core card
through the [Canon Control Center](../canon-suite/canon-control-center), and
this cuts the cards out of it.

```
proxypress → PDF → canon-control-center → printed sheet
                                                               ↓
                                          silhouette-control-center → Silhouette Cameo
```

* **Geometry that matches a published reference.** The layout engine
  reimplements Silhouette Card Maker's algorithm and reproduces every number
  in its published layout table exactly — grid and registration-mark length,
  to two decimal places, for every sheet and card size. That check is a test.
* **Coordinate locked.** SVG with explicit `mm` and a matching `viewBox`, DXF
  with declared units, arcs flattened to half a machine step. An independent
  renderer measures the proof at exactly Letter.
* **Verified before it cuts.** The byte stream is built, decoded back, and
  every card measured out of it — size and position to within 0.05 mm. If
  anything disagrees with the layout the job is refused, not warned about.
  The same bargain Canon Suite makes with the printer.
* **Honest about provenance.** Every protocol byte cites the source it was
  recovered from. Every blade preset says whether its numbers were published,
  recovered, extrapolated or actually cut — and the card presets say
  `[NOT CUT]`, because they have not been.

## Install

```sh
ln -sf ~/Projects/Printing/cut-proxies/bin/silhouette-control-center ~/.local/bin/silhouette-control-center
```

Template generation needs nothing but the standard library. Driving the
cutter needs `pyusb` and a udev rule — see [docs/SETUP.md](docs/SETUP.md).

AppImage, RPM and Arch/AUR build recipes are in
[packaging/README.md](packaging/README.md). Built artifacts go into
`out/packages/`.

The [UI and functionality audit](docs/UI_FUNCTIONALITY_AUDIT.md) records
tested behavior, fixes, and remaining issues. In the window, **Advanced
details** reveals diagnostics; **Colorblind mode** adds blue/orange styling,
patterned failures and text labels. Uploaded PDF artwork appears behind the
cut paths at its physical page size.

## Use

```sh
silhouette-control-center                            # the window
silhouette-control-center layouts                    # what fits on what
silhouette-control-center template --sheet Letter --landscape
silhouette-control-center template --portrait --mark-inset 3.5     # nine up
silhouette-control-center template --engine proxypress             # match a Proxy Press sheet
silhouette-control-center cut --dry-run              # build, verify, send nothing
silhouette-control-center cut --preset "Pen, no blade"             # draw it instead
silhouette-control-center status                     # cutter, transports, WinBoat guest
silhouette-control-center materials                  # Silhouette's own material settings
silhouette-control-center testcut --force 25,29,33 --passes 1,2   # find your settings
silhouette-control-center calibrate --x 0.2 --y -0.1 # standing blade-vs-sensor offset
silhouette-control-center import art.svg             # what is in a file
silhouette-control-center cut --from art.dxf --color red          # cut someone else's file
silhouette-control-center check-sheet printed.pdf    # measure the marks that got printed
```

## What it replaces

This is the Silhouette Studio functionality that matters for printing
proxies, on Linux, without the VM:

| Studio does | so does this |
|---|---|
| Page and card layout with registration marks | `template`, `layouts` |
| Material cut settings | `materials` — read from Studio's own database |
| Send to the cutter with an optical mark scan | `cut` |
| Test cut to dial in blade settings | `testcut` — every combination in one pass |
| Machine calibration | `calibrate` |
| Open an SVG or DXF and cut it | `cut --from`, `import` |
| Cut by line colour | `--color` |
| — | `check-sheet`: measure where the marks actually printed |

The last row checks the digital PDF geometry. It cannot detect scaling or
feed drift introduced by the physical printer. Measure a printed ruler and
use a pen alignment test before cutting valuable stock.

`template` writes three files: the cut SVG, the cut DXF, and a `-proof.svg`
that also carries the registration marks so it can be held against the printed
sheet. **The proof is for looking at, not for cutting** — Studio would try to
cut the marks.

## The sheet

**Eight cards: four across, two down, Letter or A4 turned landscape.**

ProxyJak comes in Letter and A4, so those are the only two sizes here.
Nine-up exists, but it needs a 3.5 mm mark inset and a portrait sheet,
which puts the registration marks 3.5 mm from the paper edge with no room
for printer feed drift. Eight is not a compromise on this stock — it is
the layout.

The usual Card Maker workaround is to cover the bottom-left card during the
registration scan, then uncover it before cutting. The preview also tints
cards within a conservative 5 mm of a mark zone amber; that proximity check
does not mean each highlighted card needs covering.

## Working with Proxy Press

Choose **Letter · MTG Cameo** or **A4 · MTG Cameo** in the window's
Layout preset selector, or use:

```sh
silhouette-control-center template --layout-preset mtg-cameo-letter
silhouette-control-center cut --layout-preset mtg-cameo-letter --dry-run
```

These match the current Proxy Press Cameo setup: upright 63 × 88 mm MTG
cards, 4 × 2 landscape, 0.625 mm bleed, no gap, 10 mm margin, 3.2 mm corners,
and standard three-mark registration at a 10 mm inset with 1 mm lines.
Proxy Press's auto arm is 7.25 mm on Letter and 8.75 mm on A4. Blade/material
settings remain separate. Both applications use the same standard patterns;
there is no separate Proxy Press registration pattern.

```sh
silhouette-control-center proxypress            # the exact settings to type in
```

`silhouette-control-center proxypress` prints the matching current Cameo settings and
commands. Card positions differ from Card Maker's integer-pixel layout by
less than 0.07 mm; the named presets use Proxy Press's own positions exactly.

Then:

```sh
silhouette-control-center template   --layout-preset mtg-cameo-letter
silhouette-control-center check-sheet sheet.pdf --layout-preset mtg-cameo-letter
silhouette-control-center cut        --layout-preset mtg-cameo-letter
```

`--engine proxypress` lays the grid out the way Proxy Press does, so the
cut matches the sheet that exists rather than the one we would have
preferred.

## Finding your settings

Rather than guessing from a table, cut a sheet of test squares — each one
with its own force, speed, depth and pass count, all in a single pass of the
mat:

```sh
silhouette-control-center testcut --force 25,29,33 --passes 1,2,3 --out ~/Documents
```

That prints a map, writes a labelled SVG of the same map, and cuts nine
15 mm squares on a blank offcut. Lift a corner of each **without ejecting the
mat** and see which separated. Tune passes first, then force, then speed,
then depth.

Registration is off by default — a settings test should not cost a printed
sheet.

## Calibration

If every cut lands slightly off the printed card in the same direction, that
is the fixed error between where the sensor sees the marks and where the tool
comes down. Measure it once with the pen preset and store it:

```sh
silhouette-control-center calibrate              # how to measure it
silhouette-control-center calibrate --x 0.2 --y -0.1
```

It is then applied to every job, and the verification step knows about it, so
a calibrated job still checks out. It is **not** a fix for a sheet printed at
the wrong scale — no offset can correct that.

## Blade presets

| Preset | For | Provenance |
|---|---|---|
| `Cameo 4 starting point` | where to begin, then tune | the Card Maker cutting guide |
| `330 gsm black-core` | the stock this suite prints on | extrapolated, **never cut** |
| `310 gsm black-core` | 310 gsm stock; force 33, speed 1, depth 7, two passes | extrapolated, **never cut** |
| `330 gsm black-core, laminated` | same, under a laminate pouch | extrapolated, **never cut** |
| `300 gsm cardstock` | lighter card | extrapolated, **never cut** |
| `Deep-cut blade, 330 gsm` | the 2.0 mm deep-cut blade | extrapolated, **never cut** |
| `Plain paper test` | proofing alignment, costs nothing | from the recovered media table |
| `Pen, no blade` | draw the cut instead of cutting it | from the recovered media table |

Force and speed are unitless machine indices, the same numbers Studio shows.
**The Cameo 4 and 5 take speed 1–30; every earlier Cameo takes 1–10**, so a
number copied from a modern guide means something very different on an older
machine.

Start at `Cameo 4 starting point` (force 25, speed 25, depth 7, one pass), cut
a test sheet, and **without ejecting the mat** lift a corner and look at the
cut. Re-send until it separates cleanly; the number of sends is your pass
count. Tune in this order: **passes, force, speed, depth** — depth is only a
ceiling on how far the AutoBlade extends.

The recovered media table stops at 275 g. 330 gsm is past the end of it, just
as it is past the end of the Canon's paper-weight range. Record what you
measure — a file in `~/.config/cut-proxies/presets/` overrides a built-in of
the same name.

## Checks

```sh
./run-tests.sh                         # 298 tests, no cutter needed
silhouette-control-center cut --dry-run              # build and verify a real job
silhouette-control-center status                     # what hardware is actually reachable
```

The tests are fast and offline. Nothing in them touches USB or the WinBoat
guest.

## Caveats

**No cutter has ever been attached to this machine** — a Cameo 4 is on order.
The geometry is verified
against a published reference and the stream is decoded and measured, but
"the bytes are right" and "the machine did the right thing" are different
claims and only the first is being made. [docs/FINDINGS.md](docs/FINDINGS.md)
keeps them apart, section by section, including the null results.

Silhouette Studio is installed in the WinBoat guest and is detected
automatically. `silhouette-control-center studio --run-helper` runs a read-only survey
inside Windows and writes its report back to the shared folder; `silhouette-control-center
studio <template>` opens a template in Studio. Windows serves one RDP session
at a time, so both refuse — with a message naming the holder — if something
else already has it.

## Independent application handoff

The supported flow is Proxy Press → PDF → Canon Suite → printed sheet →
Silhouette Control Center. The former `print-proxies` program was deprecated;
its placement and spool verification now live in Canon Suite.

Proxy Press can export a cut SVG for each sheet. Choose the same sheet number
as the printed front, especially for a partially filled final sheet. Import
that SVG at its declared physical size; it contains cut paths, not marks.
Use the registration pattern, inset, arm length and thickness shown by Proxy
Press. For example, for an uncalibrated Letter Cameo layout:

```sh
canon-print print cards.pdf --sheet Letter --scaling none --duplex None --pages 1 --dry-run
silhouette-control-center cut --from cut-lines.svg --sheet Letter --landscape --pattern three --mark-inset 10 --mark-length 7.25 --mark-thickness 1 --dry-run
```

Choose your Canon stock profile explicitly when it differs from the default.
Inspect the report and printer status before printing one page. Duplex PDFs
interleave front/back pages: 1/2, 3/4, and so on. Re-feed one sheet at a time
using Proxy Press's instructions and print its matching back page.

Named MTG Cameo presets match Proxy Press's default geometry. Customized
layouts need the exported SVG and matching registration settings. Proxy
Press bakes printer scale and placement compensation into PDF and SVG;
the cutter's calibration is a separate blade-to-sensor translation. Do not
apply printer compensation a second time. Non-default printer compensation
also transforms the PDF marks: compare the PDF and cut overlay and perform
a physical pen test; nominal mark settings alone do not describe arbitrary
asymmetric calibration.

`check-sheet` verifies the selected PDF page, not the physical sheet or the
printer's output. Spool verification proves encoded settings, and cutter
verification proves encoded paths. Neither substitutes for measuring a print
or inspecting a physical cut.

### Rechecking compatibility

From the Printing workspace, generate fresh SVG fixtures using the actual
Proxy Press functions and pass them to the cutter's offline test:

```sh
node proxypress/tools/export-interop.js > /tmp/proxypress-geometry.json
(cd cut-proxies && PROXYPRESS_INTEROP_JSON=/tmp/proxypress-geometry.json python3 -m unittest tests.test_proxypress_exports -v)
```

The normal cutter suite uses a dated local fixture so it remains independent.
The optional Proxy Press browser probe also exercises sheet selection and
real duplex PDF export:

```sh
cd proxypress
node tools/console-check.js --probe tools/interop-probe.js --artifacts /tmp/proxypress-browser.json
```

Pass `/tmp/proxypress-browser.json` through `PROXYPRESS_INTEROP_JSON` to the
same cutter test to raster-check front-page registration marks as well.
Browser artifacts contain each exported PDF as base64 and the sheet SVGs.
A successful geometry-only check does not establish browser or PDF behavior.

For registered cuts, an SVG with declared page dimensions must match the
selected sheet size and orientation within 0.1 mm. A one-card final sheet
still uses the full paper coordinates for registration. Choose the matching
sheet or re-export; fitting artwork inside another sheet is insufficient.
DXF drawing bounds and SVGs without declared dimensions do not establish a
paper size, so select their sheet and registration settings explicitly.
