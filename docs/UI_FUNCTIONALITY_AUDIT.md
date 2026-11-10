# Silhouette Control Center: UI and functionality audit

2026-11-09. Audited the local application and current sibling Proxy Press
source. No cutter was connected and no hardware job was sent. Findings below
distinguish reproduced software behavior from source review and from work
that needs hardware. This is not hardware qualification.

## Reproduced and fixed during the audit

| Finding | Result and evidence |
|---|---|
| SVG/DXF and PDF Open buttons crashed | Missing `Gio` import. Both handler tests failed with `NameError` before the fix and pass afterward. |
| Sending discarded calibration | Both CLI and GUI rebuilt the plan without the checked offset. The actual fake-transport tests failed before the fix. Jobs now retain the exact verified write/read plan; sending uses it. |
| GUI sent registration despite the switch being off | The send callback forced `registration=True`. The actual send regression failed before the fix and now passes. |
| Confirmation could send a changed or blocked job | Confirmation now retains the reviewed job and refuses changed, blocked, absent or already-sending jobs. Both constant-allow and constant-refuse mutations are detected in the normal discrimination suite. |
| Export could change imported geometry | An imported triangle exported as a rectangle. SVG/DXF exports now retain imported paths. The regression failed before the fix; round trips preserve the triangle's vertices. |
| Radius overrides disappeared from export | A checked 8 mm radius exported at 3 mm. The chosen radius now remains in the job's geometry; the regression failed before the fix. Generated DXF rounded corners still use bulges. |
| Crowding warning overstated the evidence | Proximity within a guessed 5 mm threshold demanded masking all nearby cards. Geometry details remain in Advanced; the operator advice now identifies the documented bottom-left workaround. |
| Obsolete separate Proxy Press pattern | Removed at the owner's request. Both layout engines offer the standard three/four patterns. |
| Current Proxy Press setup was not directly selectable | Added Letter/A4 MTG Cameo layout presets. Executed the actual local JavaScript `layout()` and `regArm()` using Node and compared the results. Letter arm: 7.25 mm; A4 arm: 8.75 mm. |
| Preset names could not be distinguished | Renamed choices to `Letter · MTG Cameo` / `A4 · MTG Cameo`, with provenance outside the selector. |
| Maximized form wasted horizontal space | Replaced the narrow centered preferences clamp with full-width scrolling controls and an adjustable pane divider. |
| Default preview was an engineering log | Added a compact layout/material/check summary. Detailed coordinates, stream sizes and verification rows require the explicit Advanced details checkbox. |
| Mark feedback depended on red/green | Added persistent Colorblind mode: blue/orange, dashed failures, and OK/! labels. Preference saving preserves calibration settings. |
| Loaded PDF artwork never appeared | Added a color PDF background at the PDF's physical page dimensions, with cut/mark overlays. It reuses the rendered page for mark measurement. Mismatched paper sizes are not stretched to match the template. |
| Old asynchronous measurements could replace a newer result | Requests now carry an identity token. Current/stale success and failure callbacks are tested; constant-accept and constant-reject mutations are detected. Layout changes invalidate measured-mark overlays. |
| Failed artwork import discarded the previous source without rebuilding | A failed load now retains the previous artwork. Added a handler regression. |
| Color selection could silently fall back to cutting everything | Removed the GUI fallback. Added `(none)` handling for paths without a stroke color. |
| Community guide presets displayed as extrapolated | Added the `guide` provenance label in the GUI. |
| Native package minimums were too low | GTK FileDialog needs GTK 4.10; Adw AlertDialog is version 1.5 in the installed GIR. Declared those minimums. |

Also added `310 gsm black-core`: derived, never cut, force 33 / speed 1 /
depth 7 / two passes. This is an explicitly requested starting guess based
on vendor Coverstock values, not a vendor-certified material or measured cut.

## Open findings, in priority order

### P1: Direct cutting has no operator pause after a registration scan

Source review: `Transport.run` checks the scan reply and immediately writes
the cut commands. The masking advice says to uncover the bottom-left card
between those phases, but neither the CLI nor GUI currently offers that
pause. A dedicated phase callback/operator acknowledgement is needed without
changing the verified command bytes or skipping the scan reply check.

### P1: Test-cut jobs bypass the per-job verification proof

Source review: `testcut.build` goes straight to `CutJob.plan_matrix`, and the
CLI/GUI send `TestSheet.steps`. It does not run `job.build`, decode-and-measure
the matrix, or prove checks live. The existing offline tests of generated
matrices do not establish a proof for the particular job about to be sent.
The matrix needs its own per-cell expectations and named liveness probes,
or composition through a checked job representation. Do not call this
equivalent to the normal checked cut path yet.

### P1: SVG import does not preserve all visible geometry

Reproduced: `width="200mm" height="100mm" viewBox="0 0 100 100"` with an
unqualified 100-unit square imports as 200 × 100 mm with no warning. SVG's
default aspect-ratio behavior should instead retain a square and center it.
The loader applies independent X/Y scales and ignores `preserveAspectRatio`.

Reproduced: a file containing one rectangle inside `defs`, one rectangle with
`style="display:none"`, and one visible rectangle imports three paths with
no warnings. The walk traverses definitions and only recognizes the literal
`display` attribute. CSS/clipping/masks/nested viewports and transforms also
need a clearly enforced supported subset. The verifier compares the decoded
path against the already-imported path; it cannot catch a mistaken SVG
interpretation shared by both. Reject unsupported semantics or implement and
test them against an independent renderer.

The actual current Proxy Press `Downloads/cut-lines.svg` does **not** use
these problematic constructs. Its eight rounded rectangles imported without
warnings, matched the MTG Letter preset to below 0.001 mm, passed verification
and all eight probes, and rendered at 1650 × 1275 px at 150 dpi.

### P1: DXF negative and sparse bulges need correction

Reproduced: `_bulge_arc((0,0), (10,0), -tan(pi/8), .025)` ends near `(0,10)`,
not `(10,0)`. The center offset is not reversed for negative bulges.
Source review also found LWPOLYLINE's optional per-vertex bulges are collected
into one compact list, which loses association when a vertex omits group 42.
These can change the imported shape before verification. Add signed endpoint
and sparse-vertex tests before correcting the parser.

### P1: A failed PDF measurement does not block Cut

Source review: `_sheet_done` displays `report.problems`, but `rebuild` only
uses `Job.blocked`; the measured PDF's problems are not included. A visibly
bad or wrong-size loaded sheet can therefore coexist with an enabled Cut
button. The new preview improves visibility but is not this missing guard.
Decide how a loaded sheet, failed measurement and pending measurement bind
to the exact job; test acceptance and refusal as well as stale results.

### P2: PDF offset adoption is not machine calibration

Source review: `on_adopt_offset` saves a PDF mark translation but does not
update the calibration widgets. `options` keeps supplying the old widget
values, so the current job ignores the newly saved offset. More fundamentally,
PDF print displacement is not a measured blade-versus-sensor error; optical
registration already changes the coordinate origin. This workflow needs to
separate print placement from a physical calibration measurement rather than
merely syncing the controls.

### P2: AutoBlade zero does not honor the GUI's explanation

Source review and reproduction: the GUI maps zero to `depth=None` and says
this means do not send depth. `job.build(depth=None)` retains the preset's
depth (7 for the default), so a depth command still goes out. Separate
"inherit preset" from "omit depth", then test the final verified/send bytes.

### P2: Transport handling has unchecked assumptions

Source review: PyUSB sums partial writes without retrying the unsent suffix;
usblp can loop indefinitely if writes return zero or stay blocked. Selecting
a device now filters PyUSB through the existing API, but the usblp fallback
does not identify which model its node belongs to. Test cuts/status queries
can also overlap a regular GUI send because only regular sends share the
new `_sending` state. These require controlled transport tests and eventual
hardware validation, not optimistic success messages.

### P2: Studio's operator guide uses scan height as arm length

Source review: both Studio guides print `marks.reg_length_mm` (195.9 mm on
Letter landscape) where Studio needs `marks.length_mm` (7.25 mm in the MTG
preset). The guides also force Type 1 regardless of pattern and substitute
depth 7 when there is no depth. Bounds include skipped slots. Correct the
guidance before relying on it for Studio setup. The existing live DXF import
size discrepancy remains undiagnosed; do not present a guessed 72/70 factor
as an established cause.

### P2: Some controls silently use other values

Source review: invalid skip-field tokens are ignored; the Proxy Press
engine uses the configured MTG geometry even if a different Card size is
selected; the test-cut GUI takes speed/depth from the blade preset rather
than current edited controls. Layout preset labels also remain selected
after custom edits. Make applied settings explicit and reject invalid input
instead of silently switching behavior.

### P2: Two WinBoat launch invariants have gaps

Source review: `_run_freerdp` drops the launch bound if `timeout` is absent,
and `run_powershell` does not perform the start-of-run reap used by
`run_script`/`open_in_studio`. Refuse an unbounded launch and keep cleanup
strictly scoped to identifiable sessions. Credential-pipe clients hide their
marker from argv, so do not widen the reaper to catch unrelated processes.

### Distribution follow-up

The local RPM/Arch builds and extracted-package checks do not establish a
Fedora installation test or broad AppImage distribution compatibility. The
checkout has no declared license, so package metadata says
`LicenseRef-Unknown`. The AUR recipe uses a local source tarball with its
actual SHA-256; the GitHub release source must exist and licensing must be
declared before publication. Nothing was published during this task.

## Verification and artifacts

The normal suite remains fast/offline. Additional display tests run separately
through `timeout 30 python3 tools/audit-gui.py`, which blocks USB sends and
guest launches. They exercise all five pages, exact layout preset application,
filtered dialogs, registration switching, refusal/recovery for an incompatible
cutter, opt-in diagnostics, and color PDF artwork behind cut geometry.

`out/ui-audit/report.json` and five window-only PNG captures record the live
GUI run. `out/send-regressions-before.log` and
`out/export-regressions-before.log` preserve the initial failing regressions.
`out/audit-reproductions.json` records the importer/radius probes before fixes;
the triangle and radius items in that file have since been fixed as described
above. `out/proxypress-svg-check.log` records the actual SVG dry run.

For the writer change, `rsvg-convert` independently rasterized the portrait
Letter proof at 150 dpi to **1275 × 1650 px**. This is a physical-size check;
SVG/DXF agreement alone is not an independent scale check.
