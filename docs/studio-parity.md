# Silhouette Studio parity

How Cutline compares with Silhouette Studio 5.0.556.1, feature by feature, as
of 2026-11-25.

| Mark | Meaning |
|---|---|
| ✅ | Works: in Cutline and checked, by the tests or on a Cameo 4. |
| 🟡 | Partial: there with gaps, or uses an unconfirmed cutter command. |
| ❌ | Missing. |

Cutline has cut several registered Print & Cut sheets on a Cameo 4 over USB
(see [Status](status.md)). Options that drive the cutter through already
verified commands (such as path ordering, weed paths, test cuts, action
filtering, and end position) count as working; options whose protocol
commands are not yet confirmed on hardware stay partial.

**Parity: 79.0%** (58 ✅, 12 🟡, 11 ❌ across 81 features; ✅ counts 1, 🟡 counts ½).

## Design: drawing and editing

| Silhouette Studio | Cutline | |
|---|---|---|
| Select, move, scale and rotate | Select tool with a transform box, Shift for 15° steps | ✅ |
| Point editing | Point Edit: add, delete, break, corner, smooth, make flat or curve | ✅ |
| Line | Line, Shift for 45° steps | ✅ |
| Rectangle and rounded rectangle | Both, Shift for squares, Alt from the centre | ✅ |
| Ellipse | Ellipse, Shift for circles | ✅ |
| Regular polygon | Regular Polygon with a side count | ✅ |
| Polygon | Polygon, click to place points | ✅ |
| Curve | Curve, smooth through clicked points | ✅ |
| Arc | Arc | ✅ |
| Freehand and smooth freehand | Both | ✅ |
| Knife | Knife | ✅ |
| Eraser | Eraser, with a size | ✅ |
| Eyedropper | Eyedropper copies cut action, line and fill | ✅ |
| Measure | Measure, then scale to a known length | ✅ |
| Pan and zoom | Hand and Zoom tools, Space to pan | ✅ |
| Undo, redo, cut, copy, paste, paste in front, duplicate | All | ✅ |
| Select All and Select Same | Both, Select Same by line or fill colour | ✅ |
| Basic shapes library (hearts, stars, arrows…) | Add Shape covers the drawing tools' shapes only | 🟡 |

## Design: panels

| Silhouette Studio | Cutline | |
|---|---|---|
| Page Setup: size, mat, registration marks | Page Setup panel and a named default card layout | ✅ |
| Transform: move, rotate, scale, shear, mirror | Transform panel and Object → Transform | ✅ |
| Align and distribute | Align panel | ✅ |
| Arrange (front and back) | Object → Arrange | ✅ |
| Group and ungroup | Both | ✅ |
| Replicate | Replicate panel | ✅ |
| Modify: weld, subtract, intersect, divide, crop | All, plus Subtract All and Detach Lines | ✅ |
| Compound paths | Make and release compound paths | ✅ |
| Offset and internal offset | Offset panel, corner or round | ✅ |
| Trace: threshold, high pass, outer edge, detach | Trace panel, with a trace area | ✅ |
| Nesting | Nesting panel: cut area or border, rotations, padding | ✅ |
| Text Style: fonts, size, bold, italic, spacing, justification | Text Style panel | ✅ |
| Text on a path | Text to Path and Release Path | ✅ |
| Text warp | — | ❌ |
| Line and fill colour | Line and Fill panel | ✅ |
| Pattern and gradient fills | — | ❌ |
| Grid and snap | Grid panel and Snap to Grid | ✅ |
| Layers | The Objects panel lists objects; there are no layers | 🟡 |
| Image effects (brightness, colour) | — | ❌ |
| Sketch and Rhinestone | — | ❌ |
| Emboss | — | ❌ |

## Files

| Silhouette Studio | Cutline | |
|---|---|---|
| Open `.studio3` | Paths, compound paths, curves and page size; not registration, colours, images or effects | 🟡 |
| Save `.studio3` | Rewrites a Card Maker template in place; Studio has not been seen opening one | 🟡 |
| Import SVG (Designer edition) | Full SVG import with Inkscape layers and marks, available during startup cutter checks | ✅ |
| Import DXF | DXF import | ✅ |
| Export DXF | DXF export with true arcs (Studio opens it 2.84% too large) | ✅ |
| Import PDF (Designer edition) | Open Print File on the Sheet tab checks PDF registration marks | 🟡 |
| Place an image (PNG, JPG) | Place Image | ✅ |
| PixScan | Import PixScan Photo; only tried on synthetic photos | 🟡 |
| Library and Design Store | — | ❌ |
| Cloud sync | — | ❌ |

## Print & Cut

| Silhouette Studio | Cutline | |
|---|---|---|
| Registration marks on the page | Studio's patterns, Card Maker's geometry | ✅ |
| Scan the marks on the cutter | Studio's captured sequence; found the marks from Linux | ✅ |
| Print from Studio | Export a printable SVG or a matched Print & Cut ZIP; no direct printing | 🟡 |
| Print bleed and print border | Trace and Bleed, print margin and border | ✅ |
| Calibration | A stored offset from a pen test; no guided print-and-scan calibration | 🟡 |

## Send

| Silhouette Studio | Cutline | |
|---|---|---|
| Material list | Studio's own table, bundled, or imported from an installation | ✅ |
| Custom materials | Save, update and delete presets | ✅ |
| Force, speed, passes, blade depth | All, used on real cuts | ✅ |
| Cut, score or no action per object | Cut, Score, None | ✅ |
| Send by line colour, fill colour or layer | Send by line or fill colour; layers from SVG and the command line | 🟡 |
| Two tool holders | Tool Holder 1 and 2 per object, used on registered cuts | ✅ |
| Tool detection | Reads both holders, over USB and Bluetooth | ✅ |
| Line segment overcut | Line Segment Overcut | 🟡 |
| Track Enhancing | Track Enhancing, tested on a Cameo 4 | ✅ |
| Cut order and interior first | Path order, pass order, interior first, delicate media | ✅ |
| Weed lines and border | Weed Lines and Weed Border | ✅ |
| Test cut | Corner Test Cut, Test-Cut Matrix, Dial In a Material | ✅ |
| Pause and cancel | Pause Cut and Cancel Cut, tested on a Cameo 4 | ✅ |
| Feed and end position | Feed Options and end position | ✅ |
| Sketch pen and pen styles | The pen as a tool; no sketch fills | 🟡 |

## The cutter

| Silhouette Studio | Cutline | |
|---|---|---|
| Connect over USB | PyUSB or the kernel's printer driver | ✅ |
| Connect over Bluetooth | Bluetooth Classic, link held open as in Studio | ✅ |
| Find and pair a new cutter | Scan for Cutters shows available devices; pairs on Linux, system settings elsewhere | ✅ |
| Cutter status and firmware version | Check Status verifies a Bluetooth selection while layout editing stays available | ✅ |
| Cut over USB | Registered Print & Cut sheets cut; placement slightly off-centre | ✅ |
| Cut over Bluetooth | Tested on a Cameo 4 | ✅ |
| Firmware updates | — | ❌ |
| Several cutters at once (Business edition) | One at a time | ❌ |
| Tiling a large design (Business edition) | — | ❌ |
| Other cutters (Portrait, Curio) | Portrait 1–4 and every Cameo are known; only a Cameo 4 has been tried; no Curio | 🟡 |
| Windows and macOS | Linux; an experimental Windows installer; no macOS package | ❌ |
| Help | Setup Guide and Troubleshoot a Cut | ✅ |

## Only in Cutline

Not counted above, since Studio has no equivalent:

- Every job is decoded and measured before it is sent, and refused if a
  check fails.
- Silhouette Card Maker's layouts reproduced exactly, with cards near a mark
  flagged for masking.
- A printed PDF's marks are measured against the template before cutting.
- Dial In a Material: repeat a pass on the same spot, compare two settings,
  and keep what worked.
- Help → Troubleshoot a Cut, linked to the controls each fix needs.
- A command line for every action, including a dry run that needs no cutter.
- Two windows, Qt and GTK, over one engine.
