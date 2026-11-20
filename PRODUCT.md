# Product

<!-- impeccable:product-schema 1 -->

## Platform

desktop

Native desktop app with two front ends that share one workflow: Qt Widgets
(PySide6, tried first) and GTK4/libadwaita. Linux first; a Windows installer
is also built. Not a web app and not a mobile app.

## Users

Linux owners of Silhouette cutters who want a replacement for Silhouette
Studio, which does not run natively on Linux. They design or import cut
artwork, line cuts up with a printed sheet, and send the job to the cutter.
The owner's own setup (a Cameo 4 over USB, printing card proxies and stickers
on 330gsm stock) is the test bench, not the target audience.

## Product Purpose

Design cut paths, align them with a print through registration marks, and
send them to a Silhouette cutter from Linux, with every job checked before
the cutter moves. Success is a user cutting what they meant to cut, in the
right place, without Silhouette Studio.

## Positioning

The job is verified before it is sent. `job.build` lays out, checks
clearance, builds the command stream, decodes it back and measures every
path, and the app refuses a job it cannot check (`Job.blocked`). The
verification proves itself on every run by corrupting the stream and
confirming each check catches it. Every command byte is cited to its source
(inkscape-silhouette's recovery of Studio's traffic). Silhouette Studio is the
long-term feature reference; the verification is the difference.

## Operating Context

- Workflow: **Design** (draw, import SVG/DXF/Studio3, place images, trace)
  → print with registration marks → **Send** (Layout, Sheet, Material
  Presets, Cut Job) → cutter. Each step can be used on its own.
- Upstream: Proxy Press PDF/ZIP exports and Silhouette Card Maker sheets
  arrive in **Sheet** as printed artwork with marks.
- The cutter is on USB (Bluetooth paths exist); the user is physically at
  the machine, loading a sheet face-up onto a mat.
- Material is expensive and a bad cut wastes it, so test cuts (registration,
  corner, material test sheet) sit beside **Send to Cutter**.
- A CLI (`silhouette-control-center`, also `cut-proxies`) covers the same
  checks for terminal use.

## Capabilities and Constraints

- **Anything that moves the cutter must be verified.** No path to the
  cutter skips `job.build`; no check is downgraded to a warning to let a job
  through. Pure design and editing features may follow Silhouette Studio
  freely.
- Qt and GTK must offer the same workflow and share projects, presets and
  calibration.
- Geometry claims and hardware claims are kept apart: "the stream decodes
  correctly" is not "the machine cuts correctly". UI copy must not blur them.
- Presets carry provenance (`silhouette`, `guide`, `recovered`, `derived`,
  `measured`) and the UI shows it.
- Standard library only on the template path; no `pip` dependencies there.
- Stocked sheets are Letter and A4; other sizes are behind `--all-sheets`.
- Projects save as `.scc-template` (v1 and v2).
- Undecided: the product name (see Brand Commitments).

## Brand Commitments

- "Silhouette Control Center" is a **working name**; a rename is possible.
  It uses the cutter maker's brand, so the app must not present itself as
  Silhouette's own software.
- Voice: plain, factual, says what failed. A null result is reported, not
  hidden.

## Evidence on Hand

- `docs/FINDINGS.md`: dated, method-stated findings, verified sections kept
  apart from untested ones.
- `docs/cameo4-studio-registration-2026-11-17.tsv`: Studio registration
  capture from a Cameo 4.
- One successful Linux USB registration on one Cameo 4 sheet (per
  `ROADMAP.md`). Physical Print & Cut alignment across a sheet is not yet
  qualified; do not claim it.
- `screenshots/`: README captures of the Send, Design and Sheet views.
- No user testimonials, download counts or third-party reviews exist; do not
  invent any.

## Product Principles

1. **Refuse rather than guess.** A job the app cannot check does not reach
   the cutter, and the UI says why.
2. **Say what was measured, and how.** Settings, presets and statuses show
   where their numbers came from.
3. **One workflow, two toolkits.** A behaviour or a word that differs between
   Qt and GTK is a bug.
4. **Protect the material.** Make testing on scrap the easy path before
   committing an expensive sheet.
5. **Studio parity where it is safe.** Follow Silhouette Studio's design
   features and terms where users expect them; never at the cost of
   verification.

## Accessibility & Inclusion

Colorblind mode (blue/orange, dashed failures, OK/! labels) is an
established, saved preference; status that matters must not rely on colour
alone. Both front ends follow the system light or dark theme.
