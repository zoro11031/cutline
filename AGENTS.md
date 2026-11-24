# AGENTS.md

Cutline: design, print-and-cut and sending jobs to Silhouette cutters on Linux.
An independent project (repo `zoro11031/cutline`); it does not
name or depend on the card-printing tools that produce some of its sheets.
See `README.md` for what users see, and `docs/` for how it works and what
was actually established about Silhouette's cutters and Studio, and what was
not.

**`README.md`, this file and `docs/` are committed; every other `.md` file
is local.** That includes `PRODUCT.md`, `ROADMAP.md` and `notes/` (plans,
audits, and the retired `notes/retired-findings/FINDINGS.md`, the old dated
log). Everything committed is public: no cutter serials, Bluetooth addresses,
home paths, credentials, or the names of the card-printing tools. Do not
cite a doc file from code, a test or the UI: state the fact where it is
needed.

## Layout

| File | What it is |
|---|---|
| `cutline/geometry.py` | Sheets, cards, and the two layout engines. mm, top-left origin. |
| `cutline/regmarks.py` | Registration mark geometry, the scan window, corner exclusion. |
| `cutline/template.py` | Cut paths, arc flattening, the SVG and DXF writers. |
| `cutline/graphtec.py` | The command layer. Every byte cited to its source. |
| `cutline/transport.py` | pyusb, usblp and file transports. |
| `cutline/verify.py` | Decodes a built stream and measures it back. |
| `cutline/presets.py` | Blade presets, each declaring where its numbers came from. |
| `cutline/materials.py` | Silhouette's own material settings, read from a Studio install. |
| `cutline/testcut.py` | The settings-finding test sheet: one square per combination, one job. |
| `cutline/importer.py` | Reads SVG and DXF into cut paths. Units are the whole problem. |
| `cutline/sheet.py` | Measures the registration marks on a printed PDF, optically. |
| `cutline/job.py` | Composes all of the above into one checked job. Both front ends go through it. |
| `cutline/cli.py` | Terminal frontend and desktop toolkit selection. |
| `cutline/qt_gui.py`, `qt_design.py` | Qt Widgets workspace and editor. |
| `cutline/gui.py`, `editor.py` | GTK4/libadwaita workspace and editor. |
| `cutline/desktop_help.py` | Help text both windows share: control tips, the setup guide, Troubleshoot a Cut. |
| `cutline/starting_guide.py` | Dial In a Material: test spots, passes, Cut Settings A and B, symptom advice. |
| `packaging/` | Every package recipe: native, AppImage, Flatpak, Windows installer, `install-cutline.sh`. |
| `.github/workflows/release.yml` | The release pipeline. |
| `tools/capture_screenshots.py` | Regenerates the README screenshots, offscreen, with a simulated cutter. |

## Ground rules

**Nothing here is a lookalike.** Every command byte in `graphtec.py` came out
of `fablabnbg/inkscape-silhouette`, which recovered it from Silhouette
Studio's traffic, and each carries its source line. The layout engine
reproduces Silhouette Card Maker's published table exactly and there is a test
that proves it. Do not add a command or change a constant on the strength of a
plausible guess. Where a value is magic — `TB51,400`, `TB53,10`, `TB99` — it
is labelled magic rather than explained away.

**Geometry claims and hardware claims are different claims.** A Cameo 4 is
attached here over USB and paired over Bluetooth Classic. Cutline has cut
several registered Print & Cut sheets on it over USB; the cuts landed
slightly off-centre, which nobody has measured yet. It has not cut over
Bluetooth. A claim about the byte stream ("it decodes to the right
coordinates") is still not a claim about where the blade lands, and nothing
in the docs or the UI may blur the two. `docs/status.md` keeps the verified
and the untested apart on purpose. Ask the operator what the cutter has
actually done before writing it down; this file once said no blade had moved
long after it had.

**Presets carry their provenance.** `origin` is one of `silhouette` (verbatim
from Studio's own `condition.csv`), `guide` (community documentation measured
on someone else's machine), `recovered` (inkscape-silhouette's media table),
`derived` (extrapolated here) or `measured` (a sheet was cut on *this*
machine). A `derived` preset must say "never cut" in its note, and a
`silhouette` one must still match the vendor file — there are tests for both.
Only move a preset to `measured` when a sheet has been cut and the edge looked
at, and put the date in the note.

**`condition.csv` is a bundled snapshot, and it is Silhouette's file.**
`cutline/assets/condition.csv` was added deliberately so the material library
works without a Studio install. `materials.find_condition_csv`
looks in this order: the user's imported copy under
`~/.config/cutline/studio-materials/`, then the bundled snapshot, then a
Studio installation (the configured `material_csv`, the macOS app bundle,
`%ProgramFiles%`, the Wine prefix). Importing through the UI writes to the
first, which therefore wins. Do not hand-edit the snapshot: replace it from a
Studio install, as a whole file, and say which Studio version it came from.
`silhouette`-origin presets must still match it, and there is a test.

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
./run-tests.sh                 # ~890 tests + the coverage audit
cutline cut --dry-run             # build and verify a real job end to end
cutline status                    # cutter and transports
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
proven nothing yet.

The test tier must stay fast and offline. Anything needing real USB or the
guest does not belong in it.

For a change to the SVG or DXF writers, rasterise the proof and measure it:

```sh
cutline template --portrait --mark-inset 3.5 --out /tmp/t
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
- **`--engine centered` means the eight-up sheet**, `config.CARD_SHEET_8UP`:
  a grid centred on the whole sheet, as card-printing tools draw it. The two
  engines must place cards within 0.1 mm of each other; there is a test. It
  was called `proxypress`; `config.load_settings` still reads that name back,
  and `bundle.CARD_FORMAT` keeps the format id existing bundles carry.
- **Auto arm length is searched, not estimated.** `Grid.max_mark_length_mm`
  is a per-engine guess and the centred-grid one put 11.2 mm arms on top of
  four cards. `regmarks.for_grid` finds the longest arm that clears with one
  card gap to spare, which lands within a quarter-millimetre of Card Maker's
  published figures by a different route.
- **The SVG/DXF agreement is not a scale check.** Both files come from
  `template.py` and both flatteners share a segment-count formula, so they
  agree exactly by construction. Absolute size rests on one outside
  measurement: the `rsvg-convert` render in `test_sheet`. Do not delete it
  and do not cite the format agreement in its place.
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
- **The template path is standard library only.** SVG and DXF are written by
  hand for that reason. Do not add a dependency to it. The development
  machine has no host `pip`: the Flatpak and Windows builds download wheels
  with the GNOME SDK's pip instead.
- **Tests must run without either toolkit.** A test module that touches Qt or
  GTK at import time breaks the whole suite on a machine without it. Keep
  toolkit names out of class bodies and default arguments, and let
  `skipIf` do its job. CI runs everything with both toolkits; a laptop may
  not.
- **Both engines use the standard three/four registration patterns.** The
  obsolete separate fourth-mark pattern was removed on 2026-11-09.
- **The speed scale is unresolved.** Silhouette's own database never exceeds
  10; the Card Maker guide says 25 on a Cameo 5. The presets
  follow the vendor. Do not "fix" one source to match the other — record the
  disagreement until a real cut settles it.
- **Cards crowd the marks on every full Letter and A4 sheet**, by about
  1.2 mm. That is by design in the layout algorithm, which only requires
  clearance on one axis. `mask_hints()` reports it and the operator covers
  those cards for the scan; it is deliberately *not* a blocking error.
- **The calibration offset is a translation, not a scale.** It is applied
  once, in `CutJob._path_commands`, by folding it into the origin shift, and
  `verify` subtracts it again before measuring — otherwise a correctly
  calibrated job reads as a position error exactly its own size and gets
  refused. There are tests for both the first and the last card, because an
  offset applied in the wrong place grows across the sheet instead of
  translating it.
- **The test sheet's map is not a cut file.** It carries text labels. Same
  rule as the proof: it exists to be read next to the sheet.

## Findings

Recorded in `docs/`, by topic rather than by date. A new finding goes in the
section it belongs to (`docs/how-it-works.md` for how Cutline behaves,
`docs/studying-studio.md` for what was learned about Studio and how) with its
date, the Studio version and the method, and the ledger in `docs/status.md`
moves it between Verified and Untested. Record what failed as well as what
worked.

Keep it short: the result and how it was established, not every step. The
docs were consolidated from a dozen pages on 2026-11-23 because the detail had
buried the findings; the steps live in git history. Raw captures and research
stay in `guest-staging/` (gitignored); a Bluetooth capture that includes
pairing holds the link key.

## Studio parity

`docs/studio-parity.md` lists Silhouette Studio's features beside Cutline's,
marked ✅ works, 🟡 partial, ❌ missing, with a headline percentage (✅ counts
1, 🟡 counts ½). `tests/test_studio_parity.py` recomputes the headline from
the tables, so change a row and the headline together. A cutting option stays
🟡 until a real cut has used it. When a feature lands or a cut is made,
update its row in the same commit.

## Help and advice

Troubleshoot a Cut (`desktop_help.CUT_PROBLEMS`) and the material test's
symptom advice (`starting_guide.ADVICE`) are shared by both windows. Each
problem names the control it points to (`OPEN_SHEET`, `DIAL_IN`,
`CUT_SETTINGS`), and the windows open it. Use the controls' current names;
a renamed control means updating this text.

Advice from other users (forum threads, the Card Maker guide) is labelled as
reported, never presented as measured, and never becomes a preset. Record
the report in `docs/how-it-works.md` under Materials with its date.

## Commits

One commit per piece of work. Squash work-in-progress commits before they
reach `main`; `main` reads as a list of features, not a log of attempts.
Commit as Justin Caveda. Commit messages say what changed and why, in the
same plain style as the docs.

Branches: work on a branch, fast-forward `main` when it is done, and delete
the branch. Release work goes on `release/vX.Y.Z`.

## Releases

**Every package is built in CI, never on a local machine.**
`.github/workflows/release.yml` builds and checks them all:

| Job | What it produces |
|---|---|
| `tests` | The full suite on Arch, with Qt offscreen and GTK under Xvfb. |
| `linux` | The AppImage (pinned type2 runtime), its payload smoke test and a dry-run cut. Runs in an Arch container. |
| `flatpak` | The Flatpak on the GNOME 50 SDK, with a dry-run cut. |
| `windows-build` | The Windows installer, built on Linux with NSIS. |
| `windows-check` | Installs it on a Windows runner and runs a dry-run cut. |
| `publish` | Only for a `v*` tag: the release, with `install-cutline.sh` and `SHA256SUMS`. |

A push to `release/**` runs everything except `publish`. To release:

1. Bump the version in `cutline/__init__.py`, `packaging/cutline.spec`,
   `packaging/aur/PKGBUILD` and `packaging/install-cutline.sh`.
2. Push a `release/vX.Y.Z` branch and wait for every job to pass.
3. Squash, fast-forward `main`, and push an annotated tag `vX.Y.Z`. The tag's
   message is the release notes: what changed for someone using Cutline,
   then what was verified and how.

Packaging rules:

- **No distribution gets special treatment.** `install-cutline.sh` installs
  the AppImage or Flatpak and the udev rule on any Linux.
- **The Windows installer is Qt only**, on the official embedded Python with
  PySide6 and a few MSYS2 DLLs. A GTK option was tried in 0.2.0 and dropped:
  PyGObject exists for Windows only as an MSYS2 build, so it needed a second
  Python and the whole GTK runtime, and the installer grew to 235 MB.
- **Pin what is downloaded.** The AppImage runtime, Python, wheels and MSYS2
  packages are pinned by version and checked by SHA-256.
- **Screenshots** come from `python3 tools/capture_screenshots.py`. Regenerate
  them when the windows change, and look at them before committing.
