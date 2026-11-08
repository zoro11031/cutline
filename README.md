# cut-proxies

Silhouette Cameo cut templates and cutting for proxy card sheets, on Linux.

The third leg of the Printing suite. [`proxypress`](../proxypress) makes the
sheet, [`print-proxies`](../print-proxies) puts it on 330gsm black-core card
through the [Canon Control Center](../canon-suite/canon-control-center), and
this cuts the cards out of it.

```
proxypress → PDF → print-proxies → canon-control-center → printed sheet
                                                               ↓
                                          cut-proxies → Silhouette Cameo
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
  The same bargain `print-proxies` makes with the printer.
* **Honest about provenance.** Every protocol byte cites the source it was
  recovered from. Every blade preset says whether its numbers were published,
  recovered, extrapolated or actually cut — and the card presets say
  `[NOT CUT]`, because they have not been.

## Install

```sh
ln -sf ~/Projects/Printing/cut-proxies/bin/cut-proxies ~/.local/bin/cut-proxies
```

Template generation needs nothing but the standard library. Driving the
cutter needs `pyusb` and a udev rule — see [docs/SETUP.md](docs/SETUP.md).

## Use

```sh
cut-proxies                            # the window
cut-proxies layouts                    # what fits on what
cut-proxies template --sheet Letter --landscape
cut-proxies template --portrait --mark-inset 3.5     # nine up
cut-proxies template --engine proxypress             # match a Proxy Press sheet
cut-proxies cut --dry-run              # build, verify, send nothing
cut-proxies cut --preset "Pen, no blade"             # draw it instead
cut-proxies status                     # cutter, transports, WinBoat guest
cut-proxies materials                  # Silhouette's own material settings
cut-proxies testcut --force 25,29,33 --passes 1,2   # find your settings
cut-proxies calibrate --x 0.2 --y -0.1 # standing blade-vs-sensor offset
cut-proxies import art.svg             # what is in a file
cut-proxies cut --from art.dxf --color red          # cut someone else's file
cut-proxies check-sheet printed.pdf    # measure the marks that got printed
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

The last row is the one Studio has no equivalent for, and it is the check
that catches a sheet printed at 97% before the blade finds out.

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

Four cards sit about 1.3 mm from a registration mark on an eight-up sheet.
That is unavoidable: the marks and the grid both want the corners. Cover
those four with a sticky note for the scan and take them off before the
cut — `cut-proxies` names exactly which, and tints them amber in the
preview.

## Working with Proxy Press

```sh
cut-proxies proxypress            # the exact settings to type in
```

Proxy Press's own "Silhouette Cameo" preset fits **four** cards: a 16 mm
margin keeps the grid clear of the marks by brute force and costs half the
sheet. `cut-proxies proxypress` prints the settings that put eight on the
same paper, and places every card within 0.07 mm of where Silhouette Card
Maker would — the rounding difference between its integer-pixel layout and
Proxy Press's floats.

Then:

```sh
cut-proxies template   --sheet Letter --engine proxypress
cut-proxies check-sheet sheet.pdf --sheet Letter --engine proxypress
cut-proxies cut        --sheet Letter --engine proxypress
```

`--engine proxypress` lays the grid out the way Proxy Press does, so the
cut matches the sheet that exists rather than the one we would have
preferred.

## Finding your settings

Rather than guessing from a table, cut a sheet of test squares — each one
with its own force, speed, depth and pass count, all in a single pass of the
mat:

```sh
cut-proxies testcut --force 25,29,33 --passes 1,2,3 --out ~/Documents
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
cut-proxies calibrate              # how to measure it
cut-proxies calibrate --x 0.2 --y -0.1
```

It is then applied to every job, and the verification step knows about it, so
a calibrated job still checks out. It is **not** a fix for a sheet printed at
the wrong scale — no offset can correct that.

## Blade presets

| Preset | For | Provenance |
|---|---|---|
| `Cameo 4 starting point` | where to begin, then tune | the Card Maker cutting guide |
| `330 gsm black-core` | the stock this suite prints on | extrapolated, **never cut** |
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
cut-proxies cut --dry-run              # build and verify a real job
cut-proxies status                     # what hardware is actually reachable
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
automatically. `cut-proxies studio --run-helper` runs a read-only survey
inside Windows and writes its report back to the shared folder; `cut-proxies
studio <template>` opens a template in Studio. Windows serves one RDP session
at a time, so both refuse — with a message naming the holder — if something
else already has it.
