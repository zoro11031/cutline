# Inkscape Silhouette compatibility

The comparison uses [Inkscape Silhouette at `e782d59`](https://github.com/fablabnbg/inkscape-silhouette/tree/e782d599b799861b8e2aaacc49bb2ccc7ec78633).
That extension controls Graphtec hardware from Inkscape SVG. Studio-specific
features and file interchange are a separate part of Control Center.

| Area | Control Center coverage | Remaining difference |
| --- | --- | --- |
| Discovery and connection | USB, Bluetooth Classic RFCOMM with a channel option, and Bluetooth LE over the vendor GATT service. Bluetooth supports selection by identifier or name and confirms the model using `FG`. | Reference USB support includes Craft Robo and Silhouette SD. Bluetooth changes need live coverage on each OS and cutter generation. |
| BLE transport | A continuously running event loop, separate notification channels for control and command replies, ETX buffering, acknowledged 20-byte writes, bounded retries for the transient ATT buffer-busy error. | Test connection loss, long jobs and firmware variants on real devices. |
| Device queries | Status, installed tools, firmware, and optional raw calibration replies (`TB71`, `FA`, and Cameo 3 `TC`). `cut --wait-done` waits after the final movement. | The calibration reply fields remain raw. |
| Registration | Inkscape three- and four-mark artwork, per-page SVG settings, automatic and manual scans, a scan-only check, and required scan success before registered cuts. The captured Cameo 4 setup remains the default for its standard mat. | Direct Studio3 import uses the job's registration parameters. |
| Tool and media setup | Mat dimensions and loading, tool holder and media settings, per-group presets, AutoBlade verification, pressure, speed, depth, passes, corner lifts and extensions, Track Enhancing, and end positioning. | Additional legacy-device initialization and material behavior require equipment for comparison. |
| Path preparation | Travel and roller sorting, interior-first sorting, alternating pass direction, closed-path overlap, weed borders and lines, and a Studio-style test cut. | Reference-specific path preorientation, endpoint fusion, dash-to-perforation conversion, bounding-box-only plotting, and mat-free bridges are open. |
| Motion limits | Verify the planned path geometry and reject jobs outside the permitted sheet or cut area. | The reference also offers software clipping of segments at the boundary. |
| Inkscape SVG | Page selection, geometry and transforms, nested layers and color grouping, internal clones, and recognition of the plug-in's registration marks. | Complex effects and SVG features that require rendering still need outlining in Inkscape. |
| Studio3 | The vector importer reads `silhouette05;` document paths, compound paths, curves, and native page dimensions. | Registration settings, artwork visibility, colors, raster content, Studio-generated effects, and older container formats need further format work. |

The physical results recorded in [FINDINGS.md](FINDINGS.md) include Cameo 4 USB
queries and Bluetooth LE discovery. The RFCOMM and GATT changes are covered by
offline tests. Connection stability, paper registration and cutting still need
a physical cutter test.

The Studio3 reader follows the independently documented
[studio3svg format specification](https://github.com/zaydmulani09/studio3svg/blob/e8c3a58c3f9ddce0291cc04c2087c658acc748f5/SPEC.md).
The bundled Card Maker templates check the geometry at known dimensions.
