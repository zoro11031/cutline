# Sheets, layouts and registration marks

## Layouts reproduce Silhouette Card Maker exactly

Silhouette Card Maker (SCM) publishes `assets/layouts.json`, a table of
computed layouts per paper and card size with the grid and the optimised
registration-mark arm length, and the `page_manager.py` code that generated
it. `geometry.py` reimplements that algorithm (`select_best_margins`,
`compute_grid_fit`, `compute_card_positions`, `generate_layout`) including its
integer-pixel rounding at 300 ppi.

Checked on 2026-11-07 against eight published combinations; all eight match,
grid and arm length, to the published two decimals:

| Sheet | Card | Orientation | Inset | Grid | Arm |
|---|---|---|---|---|---|
| Letter | standard | landscape | 10 mm | 4×2 | 8.04 mm |
| Letter | standard | portrait | 3.5 mm | 3×3 | 7.45 mm |
| A4 | standard | landscape | 10 mm | 4×2 | 9.40 mm |
| A4 | standard | portrait | 3.5 mm | 3×3 | 10.50 mm |
| Letter | poker | landscape | 10 mm | 4×2 | 7.20 mm |
| A4 | poker | landscape | 10 mm | 4×2 | 8.38 mm |
| Letter | japanese | landscape | 10 mm | 4×2 | 10.08 mm |
| A4 | japanese | portrait | 10 mm | 3×3 | 6.94 mm |

The check is `tests/test_geometry.py::TestPublishedLayouts::test_matches_scm_published_layouts`.
The 300 ppi rounding matters: computing in floats moves the arms in the third
decimal and the table stops matching.

A second engine, `--engine centered`, centres the grid on the whole sheet, the
way card-printing tools draw an eight-up sheet. The two engines must place
cards within 0.1 mm of each other, and a test holds them to it.

## Which sheets

Letter and A4, eight up, landscape, is the default. With the standard 10 mm
mark inset there is no 3×3 standard-card layout on Letter in either
orientation; the marks take the room:

```
Letter  10.0 mm  landscape  4x2   8 cards
Letter  10.0 mm  portrait   3x2   6 cards
Letter   3.5 mm  portrait   3x3   9 cards
A4      10.0 mm  landscape  4x2   8 cards
A4       3.5 mm  portrait   3x3   9 cards
```

SCM ships its 3×3 layouts only under its borderless 3.5 mm preset for the same
reason. Nine-up works (`--mark-inset 3.5 --portrait`), but a 3.5 mm inset
leaves little room for printer feed variation. Legal portrait fits 3×3 at the
full 10 mm inset, and it is the only nine-card layout with no card crowding a
mark. `cutline layouts` prints the whole table.

## Mark geometry

Cutline draws Silhouette's standard patterns: three marks (a square top-left
and an L at top-right and bottom-left) or the Cameo 5 Alpha's four Ls.

**The square sits with its corner on the inset, not its centre.** SCM's
`SPECIFICATION.md` says the square's centre is `inset` mm from both edges, but
its code (`generate_reg_mark`) puts the corner there, 2.5 mm different in both
axes. The code is what SCM's published templates were cut against, so the
code wins, and `regmarks.py` says so. If SCM ever changes the code to match
the prose, every existing template moves 2.5 mm.

**Mark length is told to the cutter.** The mark search command carries the
arm length, and searching for marks that were never printed fails. Two
findings shaped this:

- *Arms were once a third of their proper length (2026-11-15).* The automatic
  length took the smaller of the two clearances between the mark and the card
  grid. An arm lies along a page edge, so only the clearance perpendicular to
  it can bring it into the cards. The rule was corrected.
- *Printed sheets pin the length (2026-11-18).* Stock landscape jobs take the
  arm lengths of the printed-sheet presets: 8.0264 mm on Letter, which the
  stream encodes as `TB51,161`. Rounding it to 8 mm (`TB51,160`) once selected
  a different registration sequence from the one Studio was captured sending.
  An explicit length still takes precedence, and a print bundle supplies its
  own measured dimensions.

## Cards that crowd a mark

The Card Maker guide tells you to cover the bottom-left card with a sticky
note before the registration scan, because the card sits close enough to the
bottom-left mark that the sensor can confuse them. Measured against Cutline's
layouts (2026-11-07), every full Letter and A4 layout puts three cards within
about 1.2 mm of a mark's corner zone:

| Layout | Cards | Crowding a mark | Smallest gap |
|---|---|---|---|
| Letter landscape, 10 mm | 8 | 3 | 1.26 mm |
| Letter portrait, 3.5 mm | 9 | 3 | 1.24 mm |
| A4 landscape, 10 mm | 8 | 3 | 1.16 mm |
| Legal portrait, 10 mm | 9 | 0 | — |

That gap is by design: SCM's corner rule needs clearance on only one axis.

`regmarks.mask_hints()` names every card within 5 mm of a mark's corner zone,
and the windows say "Cover during scan" on those cards in the preview. It is
advice, not a refusal: masking works, and the alternative is losing a third of
the sheet. The 5 mm threshold is a judgement call, set well above the ~1.2 mm
known to fail; nothing published gives a figure. `--skip N` leaves a slot
uncut when it was printed empty instead.

A stricter rule stays deliberately: `regmarks.collisions` refuses any card
inside a `length × length` zone around a mark. At a full 20 mm arm that rule
rejects some of SCM's own published layouts, which SCM ships with the
sticky-note advice instead. Loosening it was tried and reverted
(2026-11-15): it encodes a sensor-proximity rule that no cutter here has been
able to measure, so it stays until something can.

## Printed sheets

`sheet.py` measures the registration marks on a printed PDF optically. On
2026-11-17 it found all three marks of a real print within 0.03 mm of their
template positions at 200 dpi. It recognises both a 5 × 5 mm square with its
corner at the 10 mm inset and a 6 × 6 mm square starting at 9.5 mm, because
printed sheets of both kinds are in circulation.

**Double-sided sheets register from the front.** Marks belong on the front
only. A sheet turned the wrong way between print passes is a printing fault,
not a cutting one, and a front/back misalignment cannot be fixed by the cut:
the blade cuts one outline through both faces. On short-edge-fed paper, a
landscape page turns top-to-bottom, not left-to-right.
