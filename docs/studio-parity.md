# Silhouette Studio parity

How Cutline compares with Silhouette Studio 5.0.556.1, feature by feature, as
of 2026-11-26.

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

**Parity: 72.8%** (48 ✅, 22 🟡, 11 ❌ across 81 features; ✅ counts 1, 🟡 counts ½).

## Design walkthrough

The 2026-11-25 Qt and GTK walkthrough used simulated cutters. It covered
keyboard import, numeric edits, save and reopen, sticker tracing and export,
and mixed Cut and Score paths in Send. The keyboard flows saved identical
projects. The mouse flows covered drawing, moving a node, pan, and zoom.
Compact, wide, light, dark, and larger-text captures included 720 × 520.

Both frontends can now select and nudge individual points from the keyboard.
With one outline selected, choose Point Edit, press [ or ] to select a
point, and use the arrow keys to move it. Both frontends also let you drag
nodes and Bézier handles and add a point on the outline. The GTK
Point Actions menu and node context menu now use the same commands as Qt.
Dragging open ends together closes the path in either frontend. Measure is
available in both toolboxes. GTK groups it with Hand and Zoom and shows the
length, angle, and Scale to Length action in the contextual toolbar.
Studio3 export still needs complete files checked in Studio.

Document actions and tool options share a row when they fit and wrap at compact widths.
The canvas sits between the drawing rail and the independently collapsible
Properties and Objects panels. Paper and mat grids have separate toggles. Both
frontends support custom object names, locking, collapsible group rows, visibility,
and drag reordering in the undo history, persisted in version 7 template projects.
Both offer a nine-point transform origin, Shift/Alt drawing constraints, and the
Knife, Eraser, and Eyedropper tools.

## Design: drawing and editing

Coverage in these two sections refers to both Qt and GTK unless a row
specifies a frontend. A capability with missing controls or behaviors in
either frontend is partial.

| Silhouette Studio | Cutline | |
|---|---|---|
| Select, move, scale and rotate | Both have selection and transforms; Qt has side stretch and on-canvas corner radius | 🟡 |
| Point editing | Both support keyboard and pointer node edits, Bézier handles, node commands, and joining open ends. Release compound paths before editing individual points. | 🟡 |
| Line | Both draw lines with Shift 45° angle constraints | ✅ |
| Rectangle and rounded rectangle | Both draw rectangles and rounded rectangles with Shift and Alt modifiers | ✅ |
| Ellipse | Both draw ellipses with Shift circle and Alt center modifiers | ✅ |
| Regular polygon | Both have Regular Polygon with a side count and Shift and Alt modifiers | ✅ |
| Polygon | Both place polygon vertices with Shift constraints and can finish open | ✅ |
| Curve | Both draw editable smooth curves through clicked points | ✅ |
| Arc | Both draw editable arcs from a drag | ✅ |
| Freehand and smooth freehand | Both have freehand and smooth freehand and close strokes ending near the start | ✅ |
| Knife | Both split closed shapes along a dragged line | ✅ |
| Eraser | Both erase along a sized brush stroke | ✅ |
| Eyedropper | Both copy cut action, line, and fill from another object | ✅ |
| Measure | Both measure and scale to a known length | ✅ |
| Pan and zoom | Both have Hand and Zoom tools, fit-to-selection, and temporary Space-drag panning | ✅ |
| Undo, redo, cut, copy, paste, paste in front, duplicate | Both | ✅ |
| Select All and Select Same | Both, Select Same by line or fill colour | ✅ |
| Basic shapes library (hearts, stars, arrows…) | Keyboard-accessible Add Shape in Qt and GTK covers the basic drawing tools | 🟡 |

## Design: panels

| Silhouette Studio | Cutline | |
|---|---|---|
| Page Setup: size, mat, registration marks | Page Setup in both panel pickers; blank designs open it in Qt | ✅ |
| Transform: move, rotate, scale, shear, mirror | Both edit numeric selection bounds and expose flips. Qt adds shear | 🟡 |
| Align and distribute | Align panel | ✅ |
| Arrange (front and back) | Object → Arrange | ✅ |
| Group and ungroup | Both | ✅ |
| Replicate | Both replicate in rows and columns; Qt adds directional, rotated and path copies | 🟡 |
| Modify: weld, subtract, intersect, divide, crop | Both expose path operations, including Subtract All and Detach Lines | ✅ |
| Compound paths | Make and release compound paths | ✅ |
| Offset and internal offset | Both expose offset; Qt adds a separate Internal Offset action | 🟡 |
| Trace: threshold, high pass, outer edge, detach | Both offer Trace and Bleed and show when sticker export can proceed. Qt adds trace-area preview and trace styles | 🟡 |
| Nesting | Qt nests to the cut area or a selected border with rotation and padding | 🟡 |
| Text Style: fonts, size, bold, italic, spacing, justification | Qt has the Text Style panel; GTK has a Type tool with contextual font, size, and alignment and edits text in a dialog | 🟡 |
| Text on a path | Qt has Text to Path and Release Path | 🟡 |
| Text warp | — | ❌ |
| Line and fill colour | Line and Fill panel | ✅ |
| Pattern and gradient fills | — | ❌ |
| Grid and snap | Both have grid and snapping; Qt adds rulers, guides and crosshairs | 🟡 |
| Layers | Objects supports custom names, locking, visibility, collapsible groups, drag reordering, and context menus. Full multi-level layer hierarchies remain partial. | 🟡 |
| Image effects (brightness, colour) | — | ❌ |
| Sketch and Rhinestone | — | ❌ |
| Emboss | — | ❌ |

## Files

| Silhouette Studio | Cutline | |
|---|---|---|
| Open `.studio3` | Paths, compound paths, curves and page size; not registration, colours, images or effects | 🟡 |
| Save `.studio3` | Card Maker template rewriter and native path-block encoder; Studio compatibility remains unverified | 🟡 |
| Import SVG (Designer edition) | SVG import with Inkscape layers and marks in Send. Both Design frontends let you pick an Inkscape page to open or import. | ✅ |
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
| Print bleed and print border | Both have Trace and Bleed, print margin and border; Qt shows trace prerequisites before export | ✅ |
| Calibration | A stored offset from a pen test; no guided print-and-scan calibration | 🟡 |

## Send

| Silhouette Studio | Cutline | |
|---|---|---|
| Material list | Studio's own table, bundled, or imported from an installation | ✅ |
| Custom materials | Save, update and delete presets | ✅ |
| Force, speed, passes, blade depth | All, used on real cuts | ✅ |
| Cut, score or no action per object | Cut, Score, None. Removing all cut paths from the active design blocks its Send handoff. | ✅ |
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
