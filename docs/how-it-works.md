# How Cutline works

Cutline turns a design into a Graphtec command stream, decodes that stream
back and measures it, and only then sends it to a cutter. Every route to the
cutter, from either window or the command line, goes through the same builder
and the same checks.

```
artwork or layout ──▶ job.build ──▶ verified Job ──▶ transport ──▶ cutter
                        │  lay out the sheet, place the marks
                        │  build the byte stream
                        │  decode it and measure it
                        └─ prove the checks can fail
```

A job that fails a check is refused, with the check named. No check is
downgraded to a warning to let a job through.

The Qt and GTK windows are two front ends over one engine. Anything that
decides what is sent (job building, verification, connections, tool labels)
lives in shared modules, so behaviour that differs between the windows is a
bug. `cutline cut --dry-run` builds and verifies a real job without opening a
connection, and `./run-tests.sh` runs entirely offline.

| Area | Modules |
|---|---|
| Layout and marks | `geometry.py`, `regmarks.py`, `sheet.py` |
| Files | `importer.py`, `template.py`, `studio3.py`, `pixscan.py` |
| The cut stream | `graphtec.py`, `job.py`, `actions.py` |
| Verification | `verify.py` |
| Materials and tests | `presets.py`, `materials.py`, `testcut.py`, `starting_guide.py` |
| Connections | `transport.py`, `rfcomm.py`, `ble.py`, `cutters.py` |
| Front ends | `cli.py`, `qt_gui.py`, `qt_design.py`, `gui.py`, `editor.py` |

## Sheets, layouts and registration marks

**Layouts match Silhouette Card Maker exactly.** `geometry.py` reimplements
Card Maker's layout algorithm, including its rounding to whole pixels at
300 ppi. All eight published combinations of sheet, card and orientation match,
grid and mark arm length, to two decimals (checked 2026-11-07, held by a
test). Computing in floats instead moves the arms in the third decimal.

The default is Letter or A4, eight up, landscape. At the standard 10 mm mark
inset, nine-up fits only with a 3.5 mm inset, or on Legal portrait.
`cutline layouts` prints the whole table.

**Marks follow Card Maker's code, not its prose.** The specification puts the
square's centre at the inset; the code, which its published templates were cut
against, puts the corner there. Cutline follows the code.

**The arm length is part of the scan command**, so it has to match what was
printed. Printed-sheet presets pin it: 8.0264 mm on Letter. Rounding that to
8 mm once selected a different registration sequence from the one Studio was
captured sending (2026-11-18).

**Cards near a mark.** The Card Maker guide says to cover the card nearest
the bottom-left mark with a sticky note during the scan. Every full Letter and
A4 layout puts three cards within about 1.2 mm of a mark (measured
2026-11-07), so the preview labels those cards "Cover during scan". This is a
hint, not a refusal. A stricter rule still refuses any card inside a mark's
own zone. That rule rejects some of Card Maker's own layouts, but loosening it
was tried and reverted (2026-11-15), because nobody here has measured where
the sensor's limit actually is.

**Printed sheets.** `sheet.py` measures the marks on a printed PDF. On a real
print all three were within 0.03 mm of their template positions (2026-11-17).
On double-sided sheets the marks go on the front only, and a front/back
offset is a printing fault the cut cannot fix.

## The cut stream

Silhouette cutters speak Graphtec's GP-GL, which the vendor does not publish.
Every command byte in `graphtec.py` comes either from
[inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette)
(recovered from Studio's USB traffic) or from a capture of Studio made here,
and each one cites its source. Values whose meaning is unknown (`TB99`,
`TB55,1`, the force command sent twice) are sent exactly as Studio sends them
and labelled unknown. Force and speed are unitless indices.

The essentials: one unit is 0.05 mm; commands end with `ETX`; moves and draws
take **y, x**; and after a successful scan, (0, 0) is the top-left mark.

**Registration** uses the sequence Studio sent to a Cameo 4 (firmware 1.20),
captured on 2026-11-17, sent verbatim for that geometry. It differs from the
earlier sequence in several ways at once, including registering *before*
sending blade settings. With it, a registration run from Linux found the marks
on a printed sheet in 14.5 s. Which change made the difference is not known.
The capture is kept as
[`cameo4-studio-registration-2026-11-17.tsv`](cameo4-studio-registration-2026-11-17.tsv),
and a test compares Cutline's setup against it. Studio's own scan failed twice
on that sheet before succeeding, with an identical command each time.

**Tools.** Cutline reads which tool sits in each holder once per connection
and on Check Status, never during a job. Blade Depth is an AutoBlade command,
so it is switched off when holder 1 reports another tool, and the transport
refuses a job that still carries it. The Cameo 4's tool detection can flicker
between readings; Studio's polling shows the same.

**Staged passes** ("Lighter first passes") split each group into a light run
and a full run over the same paths, using no new command bytes (2026-11-20).
Passes stays the total.

**Calibration.** A fixed per-machine offset between where the sensor finds the
marks and where the blade lands. The verifier subtracts it again before
measuring, and tests check that it moves the whole sheet rather than growing
across it.

## Verification

Before a job is sent, `verify.py` decodes the stream and checks the blade
settings at every cut, every card's position and size on every pass, closed
loops, path counts, cuts inside the registered area, and the registration
preamble. On the Letter 3×3 job the decoded cards were within 0.031 mm in size
and 0.023 mm in position, against a 0.12 mm tolerance (2026-11-07).

**The checks prove themselves on every build.** `verify.liveness_failures`
breaks the real stream in several ways, one per check: wrong force, card
moved, path deleted, preamble removed and so on. Each broken copy must be
caught by the check it targets, or the job is refused. An unmodified copy must
pass before and after the probes. These rules came from real holes: a probe
caught by the wrong check, a second force command the decoder ignored, and
later passes that were never measured.

The tests hold the guards to the same standard. Every guard is fired, each is
forced to always refuse and then always allow to show its tests notice, and
the suite fails on any test that asserts nothing.

One check is independent of Cutline's own arithmetic: `rsvg-convert` renders
the Letter proof at exactly Letter size. The SVG and DXF writers agreeing with
each other is not a scale check, because both are built from the same numbers.

## Files

**SVG import** always reports the scale it used, because an import at the
wrong size looks fine and cuts wrong. It follows the SVG coordinate rules
(bare numbers are pixels even on a millimetre page; `viewBox` honours
`preserveAspectRatio`), flattens curves after all transforms, and refuses a
registered job whose SVG declares a different page size. Imported outlines
render pixel-identical to the originals. Inkscape Silhouette's registration
marks are recognised by their shape.

**DXF export** writes rounded cards as closed polylines with bulge corners and
explicit millimetre units. Studio 5.0.556.1 opened one about 2.84% too large
(2026-11-08). The cause wasn't isolated, so the writer is not compensated,
and Cutline writes `.studio3` instead.

**`.studio3`** is Studio's native document, and the only format the free
edition opens with page and marks intact. It is a plain container, neither
compressed nor encrypted. Page size, registration marks and card outlines were
decoded by comparing 32 Card Maker templates (2026-11-15). A few header fields
remain unidentified. Cutline rewrites coordinates in place without changing
the file's length, so undecoded fields stay valid; the card count cannot
change. Whether Studio opens a rewritten file has not been checked.

**PixScan photos** are straightened using the mat's bar pattern, whose
geometry was read out of Studio (2026-11-20). The fit is refused if it is
worse than 0.5 mm RMS. On synthetic photos items land within 0.5 mm, but no
real photograph has been tried, and which physical mat each pattern belongs to
is unknown.

## Materials and presets

**Silhouette's material table** is a plain CSV inside Studio 5.0.556.1
(`condition.csv`). A copy is bundled so the material library works without
Studio; it is replaced only as a whole, never edited. It confirms two values
recovered from traffic: a tool offset of 18 for blades and 0 for the pen, and
depth × 10 as the AutoBlade index. Studio also ships a second, encoded
materials file, and which of the two it actually reads is open.

**Every preset says where its numbers come from**: `silhouette` (verbatim from
the table, test-checked), `guide` (community, measured on someone else's
machine), `recovered` (from inkscape-silhouette), `derived` (worked out here
and never cut), or `measured` (cut here and examined, with a date). No preset
is `measured` yet.

For heavy stock, passes beat force. One very hard pass embosses the back and
drags the sheet, while several moderate passes cut cleaner. The tuning order
is passes, then force, then speed, then depth.

**The speed scale is disputed.** Silhouette's table never goes above 10
(cardstock 1–4); the Card Maker guide uses 25 on a Cameo 5. Presets were once
rescaled to the guide's numbers, which was reverted when the table
contradicted it. Each preset follows its own source, and the question stays
open until a cut settles it.

**Coated, core and foil stock** (community report, 2026-11-20; not cut here).
The printed coating is weaker than the core, so a few light passes should come
first and fewer full passes finish the cut. One example: 4 passes at force 10,
then 3 at force 25. Foil needs more light passes at lower force. Fibers left
on the back mean one more full pass. Speed mattered little to either user.
The help and material-test advice draw on this. None of it is a preset.

## Connecting to a cutter

All of this was observed on one Cameo 4, firmware 1.05 and then 1.20.

**USB** goes through PyUSB, or through the kernel's `usblp` driver when PyUSB
is missing.

**Bluetooth Classic** is how Studio talks to a Cameo 4: RFCOMM on channel 1,
not Bluetooth LE (established 2026-11-21 from Studio's binaries and a live
capture). Cutline does the same with the kernel's RFCOMM sockets, no driver
needed. The cutter's Bluetooth is slow and sometimes stalls, and the transport
is built around that (measured 2026-11-21 to 23):

- It waits up to 40 s for a first connection; one took 18 s.
- A session that stalls with no reply within 5 s is dropped and reopened, and
  orphaned kernel sessions are cleared.
- A cutter that never answers is reported as not detected, in about 13 s.
- A link busy with another connection is waited for, never reset.

**One link stays open between actions**, as in Studio. Status checks went
from 3–55 s to about 0.1 s. The link is checked before each use, closed after
5 minutes idle so Studio can reach the cutter, and only dropped by a failure
if it has stopped answering.

**Finding and pairing** (measured 2026-11-22 with BlueZ 5.88). An unpaired
cutter is found by a 13 s Classic inquiry, as in Studio, which runs on Scan
for Cutters or Refresh Cutters, or once when an unknown cutter shows up over
LE. Choosing it pairs it by Just Works, with no PIN, in about 5 s. Pairing has
to come before connecting, or BlueZ keeps no key and forgets the cutter. On
Windows and macOS, pair it in the system settings first.

**Bluetooth LE** uses the vendor GATT service that inkscape-silhouette uses.
On a Cameo 4 under Linux it has never carried a reply: BlueZ finds the service
but does not expose it. Studio doesn't use LE for this cutter, so it was not
pursued further.
