# AGENTS.md

Cut templates and cutting for the Silhouette Cameo, downstream of the printed
sheet. See `README.md` for what users see and `docs/FINDINGS.md` for what was
actually established and what was not.

## Layout

| File | What it is |
|---|---|
| `cut_proxies/geometry.py` | Sheets, cards, and the two layout engines. mm, top-left origin. |
| `cut_proxies/regmarks.py` | Registration mark geometry, the scan window, corner exclusion. |
| `cut_proxies/template.py` | Cut paths, arc flattening, the SVG and DXF writers. |
| `cut_proxies/graphtec.py` | The command layer. Every byte cited to its source. |
| `cut_proxies/transport.py` | pyusb, usblp and file transports. |
| `cut_proxies/verify.py` | Decodes a built stream and measures it back. |
| `cut_proxies/presets.py` | Blade presets, each declaring where its numbers came from. |
| `cut_proxies/materials.py` | Silhouette's own material settings, read from a Studio install. |
| `cut_proxies/testcut.py` | The settings-finding test sheet: one square per combination, one job. |
| `cut_proxies/importer.py` | Reads SVG and DXF into cut paths. Units are the whole problem. |
| `cut_proxies/sheet.py` | Measures the registration marks on a printed PDF, optically. |
| `cut_proxies/job.py` | Composes all of the above into one checked job. Both front ends go through it. |
| `cut_proxies/cli.py` | Terminal frontend and desktop toolkit selection. |
| `cut_proxies/qt_gui.py`, `qt_design.py` | Qt Widgets workspace and editor. |
| `cut_proxies/gui.py`, `editor.py` | GTK4/libadwaita workspace and editor. |

## Ground rules

**Nothing here is a lookalike.** Every command byte in `graphtec.py` came out
of `fablabnbg/inkscape-silhouette`, which recovered it from Silhouette
Studio's traffic, and each carries its source line. The layout engine
reproduces Silhouette Card Maker's published table exactly and there is a test
that proves it. Do not add a command or change a constant on the strength of a
plausible guess. Where a value is magic — `TB51,400`, `TB53,10`, `TB99` — it
is labelled magic rather than explained away.

**Geometry claims and hardware claims are different claims.** No cutter has
ever been attached to this machine. "The stream decodes to the right
coordinates" is verified; "the machine cuts in the right place" is not, and
nothing in the docs or the UI may blur the two. `docs/FINDINGS.md` keeps
sections 1–6 (verified) apart from 7–9 (untested) on purpose.

**Presets carry their provenance.** `origin` is one of `silhouette` (verbatim
from Studio's own `condition.csv`), `guide` (community documentation measured
on someone else's machine), `recovered` (inkscape-silhouette's media table),
`derived` (extrapolated here) or `measured` (a sheet was cut on *this*
machine). A `derived` preset must say "never cut" in its note, and a
`silhouette` one must still match the vendor file — there are tests for both.
Only move a preset to `measured` when a sheet has been cut and the edge looked
at, and put the date in the note.

**`condition.csv` is not vendored.** It is Silhouette's and ships with their
software. `materials.py` reads it out of an installation the user already
has; do not copy it into the repo to make a test unconditional.

**The verification proves itself every run.** `verify.liveness_failures`
corrupts each built stream nine ways and confirms the checks reject it,
before the job is allowed through. Each probe must be caught by the check it
*names*, not merely rejected by something: two checks reading the same
decoded stream can hide each other, and the position probe did exactly that
until it was fixed. Two controls bracket the probes: the unmodified stream
must pass *before* them, because a check already failing cannot be proved by
breaking it, and must verify identically *after* them, so no probe leaves
state behind. Both controls have caught real bugs — the leading one found
that `--no-registration` had never been sendable. Do not make it
conditional, cache it, or
move it to a separate command: a separate proving run produces output
indistinguishable from a blind check's, so it is not evidence about the job
in front of you. If you add a check worth refusing a job over, add a probe
that breaks it.

**The verification is the point.** `job.build` lays out, places marks, checks
clearance, builds the stream, decodes it back and measures every card. A
caller refuses on `Job.blocked`. Do not add a path to the cutter that skips
it, and do not downgrade a check to a warning to make a job go through.

## Checks before committing

```sh
./run-tests.sh                 # 306 tests + the coverage audit
cut-proxies cut --dry-run      # build and verify a real job end to end
cut-proxies status             # cutter and transports
```

**A test that asserts nothing passes unconditionally.** `run-tests.sh`
ends with `tools/audit_tests.py`, which counts assertions per test and
fails on any that made none — usually a loop that iterated zero times. It
is a script, not a test, because as a test it discovers itself and
recurses. `--self-test` proves it can still fail.

**Every guard needs a test for both answers.** Refusing is not
discriminating: a guard tested only where it should refuse is passed by a
function hardwired to refuse, and a guard tested only where it should allow
is passed by one hardwired to allow. `tests/test_discrimination.py` pins
each guard in both directions and requires its test class to object; add a
case there whenever you add a guard.

**Every guard needs a test that fires it.** `tests/test_guards.py` exists
because six safety checks had been verified once by hand and never again — a
detector that has never fired is an assumption, and a clean result from a
broken check reads as a pass. When adding a check that refuses a job or warns
an operator, add the test that makes it refuse; then break the guard at its
source and confirm the test fails. A new test that passes first time has
proven nothing yet. Section 18 has the mutation results.

The test tier must stay fast and offline. Anything needing real USB or the
guest does not belong in it.

For a change to the SVG or DXF writers, rasterise the proof and measure it:

```sh
cut-proxies template --portrait --mark-inset 3.5 --out /tmp/t
rsvg-convert -d 150 -p 150 /tmp/t/*-proof.svg -o /tmp/t/proof.png
identify /tmp/t/proof.png     # must be 1275x1650 for Letter at 150 dpi
```

## Things that will bite you

- **An importer that fails is fine; one that succeeds at the wrong size is
  not.** `importer` always reports the scale it used, because an SVG whose
  `viewBox` disagrees with its `width` cuts the right shape at the wrong
  size and nothing downstream can tell. Coarsening the curve flattening was
  caught only once chord-sag tests existed — the earlier "points lie on the
  curve" tests passed a 40000x coarsening that would have cut visibly
  polygonal cards.
- **Letter and A4 only, eight up, landscape.** The stock is sold in those
  two sizes; a layout on anything else is advice about paper that cannot be
  bought. `Sheet.stocked` marks them and `layouts` hides the rest behind
  `--all-sheets`. Nine-up needs a 3.5 mm inset and is not what this prints.
- **`--engine proxypress` means the eight-up sheet**, `config.PROXYPRESS_8UP`,
  not Proxy Press's four-card b-cameo preset. The two engines must place
  cards within 0.1 mm of each other; there is a test.
- **Auto arm length is searched, not estimated.** `Grid.max_mark_length_mm`
  is a per-engine guess and the Proxy Press one put 11.2 mm arms on top of
  four cards. `regmarks.for_grid` finds the longest arm that clears with one
  card gap to spare, which lands within a quarter-millimetre of Card Maker's
  published figures by a different route.
- **The SVG/DXF agreement is not a scale check.** Both files come from
  `template.py` and both flatteners share a segment-count formula, so they
  agree exactly by construction. Absolute size rests on one outside
  measurement: the `rsvg-convert` render in `test_sheet`. Do not delete it
  and do not cite the format agreement in its place. Section 24.
- **An imported file gets a synthetic `Grid` from its path bounding boxes**,
  so every existing check applies to it. Do not add a second, weaker
  verification path for imports: imported geometry is the least trustworthy
  input this project takes.
- **Coordinates are sent (y, x).** `M` and `D` take the long axis first. This
  is the easiest way to produce a transposed cut and it is tested directly.
- **A registration scan moves the origin.** After the marks are found, (0,0)
  is the top-left mark, not the paper corner. The shift happens once, in
  `CutJob._path_commands`, and nowhere else.
- **The corner exclusion rule is not a bounding box.** It is a
  `length × length` square at each mark's inset corner, with no padding and no
  line-width growth, because that is the rule that chose the layout —
  `Grid.max_mark_length_mm` already has a card-distance of safety inside it.
  Growing the zone makes the check reject Silhouette Card Maker's own
  published layouts, which are known good. This was got wrong once.
- **SCM's integer-pixel quantisation is load-bearing.** Reimplementing the
  layout in floats changes the arm lengths in the third decimal and the
  published-table test stops matching. Keep the 300 ppi rounding.
- **DXF corners must be bulge polylines.** LINE + ARC renders a sharp corner
  in Silhouette Studio. Bulge is `tan(22.5°)`, and the vertex order has to
  match the sign or the corners bulge outward into ears.
- **The proof is not a cut file.** It carries the registration marks so it can
  be held against the sheet. Sending it to the cutter cuts the marks.
- **No `pip` on this machine, and no `pyusb`.** Everything runs on the
  standard library; SVG and DXF are written by hand for that reason. Do not
  add a dependency to the template path.
- **Proxy Press uses the standard three/four registration patterns.** Its
  owner requested removal of the obsolete separate `proxypress` pattern on
  2026-11-09. Section 3 describes historical geometry, not a compatibility
  requirement. Match its current local source when defining layout presets.
- **The speed scale is unresolved.** Silhouette's own database never exceeds
  10; the Card Maker guide says 25 on a Cameo 5. Section 11. The presets
  follow the vendor. Do not "fix" one source to match the other — record the
  disagreement until a real cut settles it.
- **Cards crowd the marks on every full Letter and A4 sheet**, by about
  1.2 mm. That is by design in the layout algorithm, which only requires
  clearance on one axis. `mask_hints()` reports it and the operator covers
  those cards for the scan; it is deliberately *not* a blocking error.
  Section 12.
- **The calibration offset is a translation, not a scale.** It is applied
  once, in `CutJob._path_commands`, by folding it into the origin shift, and
  `verify` subtracts it again before measuring — otherwise a correctly
  calibrated job reads as a position error exactly its own size and gets
  refused. There are tests for both the first and the last card, because an
  offset applied in the wrong place grows across the sheet instead of
  translating it.
- **The test sheet's map is not a cut file.** It carries text labels. Same
  rule as the proof: it exists to be read next to the sheet.
