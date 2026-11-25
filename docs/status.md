# Status and open questions

Where every claim stands, as of 2026-11-25. **Verified** means measured, or
checked against a published reference or a capture of Studio. **Untested**
means the bytes are checked but no cutter has done it yet. Details are in
[How Cutline works](how-it-works.md) and
[How Silhouette Studio was studied](studying-studio.md).

## Verified

**On paper or against a published reference**

- Layouts match Silhouette Card Maker's published table exactly, in eight
  combinations.
- The Letter proof renders at exactly Letter size in an outside renderer
  (1275 × 1650 px at 150 dpi).
- Built streams decode back to cards within 0.031 mm in size and 0.023 mm in
  position.
- The marks on a real print measured within 0.03 mm of their template
  positions at 200 dpi.

**On a Cameo 4**

- USB status and firmware queries, on firmware 1.05 and 1.20.
- Tool detection: ` 2, 0` with an AutoBlade in holder 1, over USB.
- A registration-only run from Linux found the marks on a printed sheet in
  14.51 s, using Studio's captured sequence (2026-11-17).
- Cutting: Cutline has cut several registered Print & Cut sheets over USB,
  scanning the marks and cutting around the print (reported by the operator,
  2026-11-24). The cuts landed slightly off-centre; the offset was seen, not
  measured. Jobs using both tool holders register and cut as well (same
  report).
- Track Enhancing, Pause Cut, and Cancel Cut: tested on the cutter and
  working (reported by the operator, 2026-11-25).
- Bluetooth cutting: tested on a Cameo 4 and working (reported by the
  operator, 2026-11-25).
- Bluetooth Classic: firmware, status and tool queries; a link held open
  across actions; recovery from stalled and orphaned sessions; five
  consecutive status checks succeeding (2026-11-21).
- Pairing from Linux, starting from an unpaired cutter: found by a Classic
  inquiry, paired by Just Works through BlueZ, then read, from both windows
  and the command line (2026-11-22). Windows and macOS still need the
  system's Bluetooth settings.

**In Studio's own traffic or files**

- The registration sequence and its replies (USB capture).
- The Bluetooth search and connection: classic inquiry, RFCOMM on channel 1,
  the queries sent and their replies (static reading and a live capture).
- The material table, and two protocol values it confirms (`FC` 18 for
  blades and 0 for the pen; depth × 10 as the AutoBlade index).
- The PixScan mat geometry, cross-checked three ways.

## Untested

These are built from recovered protocol or community documentation, and the
bytes are checked, but no cutter has done them yet:

- **Cut placement** has not been measured. Registered cuts over USB landed
  slightly off-centre, and how far, and whether calibration corrects it, is
  open.
- **Every preset except Silhouette's own** has not been cut. The speed scale
  is disputed (Silhouette's table never exceeds 10; the Card Maker guide uses
  25 on a Cameo 5).
- **Staged passes**: stream-verified; the benefit on laminated, core and foil
  stock is users' reports.
- **The test sheet and the calibration offset**: stream-verified only.
- **Tool codes other than 0, 1, 2, 4 and 255** are names from
  inkscape-silhouette's table, not replies seen here.
- **DXF bulge corners** are SCM's finding, taken on trust.
- **Rewritten `.studio3` files** have not been opened in Studio.
- **PixScan** has not seen a real photograph, and which mat each key
  describes is unknown.
- **Bluetooth LE** has not carried a reply from a Cameo 4 on Linux.
- **Cards near a mark**: the 5 mm masking threshold and the stricter
  collision rule are judgement calls a cutter has not tested.

## Open experiments

Each needs Studio driven against a real cutter with its traffic captured, one
setting changed at a time and an unchanged repeat as a control.

| Question | Experiment |
|---|---|
| AutoBlade | Capture Studio at depths 1, 2, 7 and 10 with everything else fixed. Does Studio send only `TF`, or more? |
| Registration | Do the replies ever carry coordinates? Is correction done in the cutter or in Studio? Why did identical searches fail twice and then succeed? |
| Overcut | Toggle Studio's overcut alone on a square. Do not equate it with `FE`/`FF`/`FC` without a capture. |
| Second tool | Capture a compatible tool in each holder; holder selection (`J`) alone does not prove Kraft or Rotary support. |
| DXF import | Repeat a known 100 mm square, one import preference at a time, to explain the 2.84%. |
| `.studio3` | Controlled saves in Studio (blank, one square, the square moved, one setting changed) to decode the remaining fields, and opening a rewritten file. |
| Material resource | How `SPEC_CUTTER/materials` is encoded, and whether Studio still reads `condition.csv`. |
| PixScan | Which physical mat each key is, and where each frame sits on the cutting mat. |
| Unknown queries | What `ESC 0x16`, `TI` and `TO` report. |

Never infer a Cameo 5 Alpha result from a Cameo 4.
