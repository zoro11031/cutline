# Silhouette Control Center roadmap

The goal is functional parity with Silhouette Studio across Basic, Designer, Designer Plus, and Business in the existing Linux desktop application. Silhouette Web offers workflow cues for controls that follow the selection, snapping to nearby objects, drawing and point editing, and moving from Design to Send. Adapt those behaviors to the Qt and GTK workspaces. The live Web editor stayed on its loading screen during review, so the Web comparisons are based on Silhouette’s published materials. [Silhouette Web introduction](https://www.silhouette101.com/archives/introducing-silhouette-web) · [Web feature updates](https://www.silhouette101.com/archives/new-software-features) · [Studio 5 manual](https://www.silhouetteeurope.eu/media/catalog/product/attachment/s/i/silhouette_studio_5_ss-um-151_8.pdf)

## Current capabilities

Qt and GTK share projects with drawing, polygon and freehand paths, point editing, transforms, replication, convex and concave offsets, compound paths with holes, editable text outlines, placed stickers with silhouette and transparency tracing, print bleed, named layers with cut-by-layer planning, ruler guides with snapping, knife slicing, and Cut, Score, and None actions. Control Center imports SVG, DXF, and supported Studio3 vectors, checks printed PDFs, Proxy Press bundles, and published Silhouette Card Maker templates, provides an offline design library, material presets, test cuts, and verified job planning. The hardware findings record successful Linux USB registration on one Cameo 4 sheet. [User guide](README.md) · [Hardware findings](docs/FINDINGS.md)

## Milestones

| Order | Milestone | Status | Completion check |
| --- | --- | --- | --- |
| 1 | Vector editing | Complete | Create compound contours with holes and editable text using installed fonts. Weld, Subtract, Intersect, and inward and outward Offset handle concave shapes and compound contours. In both frontends, edit, undo, save, reopen, and review the same geometry in Send. |
| 2 | Sticker Print & Cut | Complete | Place raster artwork, trace silhouettes or component contours, add bleed and registration marks, and export a full-size PDF with matching cut geometry. Measure PDF dimensions independently and check alignment on a physical sheet. (Physical cut validation pending hardware). |
| 3 | Design tools and local library | In progress | Add named layers and cut-by-layer, guides, object snapping, freehand and Bézier drawing, knife and eraser, text and objects on paths, glyph and font tools, gradients, pattern fills, and image effects. Save, search, and reuse user-owned designs offline. |
| 4 | Studio file exchange | Partial | Expand Studio and Studio3 support with versioned fixtures and report unsupported objects when opening projects. Track PDF, raster, embroidery, and vendor vector formats by tested import and export capability. Verify dimensions, contours, and editable properties through round trips where supported. |
| 5 | Specialty design and production | Planned | Cover warp, rhinestones, sketch and stipple patterns, PixScan, pop-up and puzzle tools, font creation, nesting, tiling, matrix copy, roll batching, barcodes, and multi-cutter jobs. Give each workflow a test project and record the hardware or accessory required to validate it. |

Use the Studio edition inventory and format guide to break these milestones into individual workflows. Treat Silhouette account sign-in, vendor Library sync, and Design Store connectivity as conditional on supported vendor interfaces. The offline library and user-owned file import cover the local workflows. [Studio edition feature inventory](https://www.silhouette101.com/archives/silhouette-studio-features) · [File-format support by edition](https://silhouetteamerica.freshdesk.com/support/solutions/articles/35000282776-file-types-and-features)

## Completed implementation sequences

1. **Vector editing (Milestone 1)**:
   - Shared editor model with multi-contour compound paths and holes.
   - Text outlines rendered with HarfBuzz/FreeType with missing-font reporting.
   - Boolean operations (Weld, Subtract, Intersect) and inward/outward Offsets via GEOS C API.
   - Preserves backward compatibility with `.scc-template` v1 and v2 projects.
2. **Sticker Print & Cut (Milestone 2)**:
   - Embedded PNG/JPEG/WebP sticker artwork with transparency and silhouette tracing.
   - Configurable print bleed and registration mark clearance checks.
   - Full-size PDF, cut SVG, and registration marks bundled into exportable paired archives.
   - Fourteen built-in Silhouette Card Maker layout templates.
3. **Design tools & offline library (Milestone 3 core)**:
   - Named layers on shapes and CutPaths, mapping into Send by Layer with per-layer tool and blade settings.
   - Persistent horizontal and vertical ruler guides with tolerance-based object snapping.
   - Offline design library (`cut_proxies/library.py` and `cut-proxies library ...` CLI) for saving, searching, tagging, loading, exporting, and importing user-owned designs offline.
   - Knife tool contour slicing via GEOS planar graph polygonization.

## Hardware validation

Track physical cut measurements on Cameo 4 USB, Linux Bluetooth, and additional cutters and tools alongside the editor milestones. Record the model, firmware, transport, and tool for each result. Report decoded stream verification, successful registration, and measured physical cuts as distinct results. Cite evidence for every new device command or setting. [Hardware findings](docs/FINDINGS.md)
