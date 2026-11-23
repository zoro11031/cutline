# Cutline documentation

How Cutline works, and how what it knows about Silhouette's cutters and
Silhouette Studio was worked out. For installing and using Cutline, start with
the [README](../README.md) at the top of the repository.

## How it works

| Page | What it covers |
|---|---|
| [Overview](how-it-works/overview.md) | The path from a design to bytes on the wire, and which module does what. |
| [Sheets, layouts and registration marks](how-it-works/layout-and-marks.md) | Card layouts, mark geometry, mark length, cards that crowd a mark. |
| [The cut stream](how-it-works/cut-stream.md) | The Graphtec command language: units, framing, registration, tools, passes. |
| [Verification](how-it-works/verification.md) | How every job is decoded and measured before it is sent, and how the checks prove they work. |
| [Files](how-it-works/files.md) | SVG and DXF import and export, Studio's `.studio3` container, PixScan photos. |
| [Materials and presets](how-it-works/materials.md) | Silhouette's material table, preset provenance, the speed-scale dispute. |
| [Connecting to a cutter](how-it-works/connecting.md) | USB, Bluetooth Classic and Bluetooth LE, and what each one does on a real Cameo 4. |

## How Silhouette Studio was studied

| Page | What it covers |
|---|---|
| [Method](studying-studio/method.md) | The rules every finding here follows, and the sources used. |
| [The Windows guest](studying-studio/windows-guest.md) | Running Studio in a VM, driving it, and capturing what it sends. |
| [What Studio does](studying-studio/what-studio-does.md) | Findings about Studio 5.0.556.1 itself: files, materials, registration, Bluetooth. |
| [Inkscape Silhouette comparison](studying-studio/inkscape-silhouette.md) | Where Cutline matches the Inkscape extension and where it differs. |
| [Status and open questions](studying-studio/status.md) | What is verified, what is untested, and the experiments still to run. |

## Reading these pages

Two kinds of claim appear throughout, and the pages keep them apart:

- **Verified** means measured, or checked against a published reference or a
  capture of Studio's own traffic. A claim about bytes ("the stream decodes to
  the right coordinates") is verified by decoding the bytes.
- **Untested** means built from a recovered protocol or community
  documentation and not yet observed on a machine. A claim about what a cutter
  *does* with those bytes is only verified by a cutter doing it.

Each finding carries its date (all in 2026) and how it was established, and
the things that did not work are recorded with the things that did. The one
raw capture kept here, [`cameo4-studio-registration-2026-11-17.tsv`](cameo4-studio-registration-2026-11-17.tsv),
is Studio's registration traffic to a Cameo 4; a test replays it.
