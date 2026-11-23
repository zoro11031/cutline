# Inkscape Silhouette comparison

[inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette/tree/e782d599b799861b8e2aaacc49bb2ccc7ec78633)
(compared at commit `e782d59`) drives Graphtec cutters from Inkscape SVG, and
it is where most of Cutline's command set comes from. This page records where
the two match and where they still differ. Studio-specific features (the
material library, `.studio3` files, PixScan) are separate from it.

| Area | Cutline | Still different |
|---|---|---|
| Connection | USB; Bluetooth Classic on RFCOMM channel 1, with one link held open and recovery from stalled sessions; Bluetooth LE over the vendor GATT service. The model is confirmed with `FG`. | The reference also supports Craft Robo and Silhouette SD over USB. LE has not worked against a Cameo 4 on Linux. |
| LE transport | One running event loop, separate notification channels for control and replies, `ETX` buffering, acknowledged 20-byte writes, bounded retries on the transient ATT busy error. | Connection loss, long jobs and other firmware need real devices. |
| Device queries | Status, installed tools, firmware, and raw calibration replies (`TB71`, `FA`, and Cameo 3 `TC`). `cut --wait-done` waits after the last movement. | The calibration fields stay raw. |
| Registration | Inkscape three- and four-mark artwork, per-page settings, automatic and manual scans, a scan-only check, and a required scan success before any registered cut. The captured Cameo 4 sequence is the default for its standard mat. | Direct `.studio3` import uses the job's own registration settings. |
| Tools and media | Mat size and loading, holder and media settings, per-group presets, AutoBlade checks, force, speed, depth, passes, corner lift and extension, Track Enhancing, end position. | Older devices' initialisation and material behaviour need the hardware to compare. |
| Path preparation | Travel and roller sorting, inside-first ordering, alternating pass direction, closed-path overlap, weeding borders and lines, a Studio-style test cut. | Path pre-orientation, endpoint fusion, dash-to-perforation, bounding-box-only plotting and mat-free bridges. |
| Limits | The planned geometry is checked and jobs outside the sheet or cut area are refused. | The reference can instead clip segments at the boundary. |
| SVG | Page selection, geometry and transforms, nested layers and colour grouping, internal clones, and the extension's registration marks recognised by shape. | Effects that need rendering must be converted to paths in Inkscape first. |
| `.studio3` | Reads document paths, compound paths, curves and page size. | Registration settings, visibility, colours, raster content, Studio effects and older containers. |

## Comparing a job

For a protocol comparison, take a single-page SVG and run the extension in
`--dry_run` mode to get its command transcript offline. Compare model setup,
tool settings, the mark search and decoded coordinates one variable at a
time, then repeat the same design in Studio and compare its traffic. The
extension skips groups labelled Print, CuttingMat and Regmarks, and draws
marks with IDs `regmark-tl`, `regmark-tr`, `regmark-bl` and optionally
`regmark-br`: a 5 mm square, 20 mm arms and a 0.3 mm stroke.
