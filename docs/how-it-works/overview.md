# Overview

Cutline turns a design into a Graphtec command stream, checks that stream by
decoding it back and measuring it, and only then sends it to a cutter. Every
route to the cutter, from either window or the command line, goes through the
same builder and the same checks.

## From design to cutter

```
artwork or layout ──▶ job.build ──▶ verified Job ──▶ transport ──▶ cutter
 (SVG, DXF, .studio3,     │                │
  generated cards)        │                └─ refused if Job.blocked
                          ├─ lay out the sheet          (geometry, regmarks)
                          ├─ place registration marks   (regmarks)
                          ├─ check clearance            (regmarks)
                          ├─ build the byte stream      (graphtec)
                          ├─ decode it and measure      (verify)
                          └─ prove the checks are live  (verify.liveness_failures)
```

`job.build` returns a `Job`. A caller sends only when `Job.blocked` is false.
There is no path to the cutter that skips the build, and no check is
downgraded to a warning to let a job through: a job that fails a check is
refused, with the check named.

## Modules

| Module | Role |
|---|---|
| `geometry.py` | Sheets, cards and the two layout engines. Millimetres, top-left origin. |
| `regmarks.py` | Registration mark geometry, the scan window, cards crowding a mark. |
| `template.py` | Cut paths, arc flattening, the SVG and DXF writers. |
| `importer.py` | Reads SVG and DXF into cut paths, and reports the scale it used. |
| `studio3.py`, `studio3_paths.py` | Read and rewrite Silhouette Studio's `.studio3` documents. |
| `graphtec.py` | The command layer. Every command byte carries its source. |
| `verify.py` | Decodes a built stream and measures it back. |
| `job.py` | Composes all of the above into one checked job. |
| `transport.py`, `rfcomm.py`, `ble.py` | USB (PyUSB and usblp), Bluetooth Classic and Bluetooth LE. |
| `cutters.py` | Cutter selection for the windows, and the held Bluetooth link. |
| `presets.py`, `materials.py` | Blade presets with their provenance, and Silhouette's material table. |
| `testcut.py`, `starting_guide.py` | The settings test sheet and the Dial In a Material workflow. |
| `sheet.py` | Measures the registration marks on a printed PDF, optically. |
| `pixscan.py` | Straightens a photo of a PixScan mat to true size. |
| `cli.py` | The `cutline` command, and choosing between the two windows. |
| `qt_gui.py`, `qt_design.py` | The Qt workspace and design editor. |
| `gui.py`, `editor.py` | The GTK 4 / libadwaita workspace and design editor. |

## Two front ends, one engine

The Qt and GTK windows are separate front ends over the same engine. Anything
that decides what is sent (job building, verification, tool labels, staged
pass descriptions, connections) lives in shared modules, and both windows call
it. Behaviour that differs between the windows is a bug.

## Running without a cutter

`cutline cut --dry-run` builds and verifies a real job and prints the check
table without opening any connection. The test suite (`./run-tests.sh`) runs
offline: cutter connections are simulated, and the suite fails if any test
asserts nothing.
