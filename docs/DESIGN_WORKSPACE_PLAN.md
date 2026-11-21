# Design workspace: Photoshop-style layout (Qt first)

Decided 2026-11-20 after an Impeccable critique of `cut_proxies/qt_design.py`
(21/40; snapshot in `.impeccable/critique/`). The owner wants the general
arrangement of the UI and tools to follow Photoshop, without new features:
no layers panel, eye toggles or drag reordering. Page setup shows in
Properties when nothing is selected. Qt first, GTK after.

## Zones, one job each

| Zone | Job | Contents |
|---|---|---|
| Header | Menus + workspace | File, Edit, View, Object. Design/Send switch. |
| Options bar | The current tool | Tool name, that tool's settings, its hint, Snap toggle on the right. Drawing tools: action (Cut/Score) for new objects. Rounded rectangle: corner radius. Polygon: Close Shape. |
| Toolbox | What the mouse does | Select (V; a selection always shows its box: drag inside to move, a corner to scale, the round grip to rotate), Point Edit (A), shapes (R/U/E/L), paths (P/F), Type (T). Grouped tools show a corner triangle; right-click or hold opens the group. |
| Canvas | The document | A document tab with the name and `*`. |
| Properties | What is selected | Nothing selected: Page (size, orientation, mat), Registration marks. Selection: Action, Position and size (several objects: their combined box), Rotate and flip, Align (two or more), Appearance, plus Edit Text… or Trace and Bleed… when that applies. |
| Objects | What exists | Unchanged list. |
| Status bar | Messages and document | Messages, size/paths/selected, zoom, Export Print & Cut (stickers only). |

## Removed duplicates

- The command toolbar: Save/Undo/Redo are in File/Edit; Add Shape is gone
  (the shape tools draw); Place Image is in File; Trace is in Object and Properties.
- The four inspector tabs, and `ResponsiveInspector` with them (Design-only).
- The Transform, Rotate and Scale tools: Select does all three.
- The rotate/flip buttons that moved between the options bar and the panel.
- The Panels menu: Select Same moves to Edit; Offset stays in Object > Modify.
- The Replicate tab: Object > Replicate… opens a dialog.
- The canvas checkboxes in Page Setup: they are View menu toggles; grid
  spacing and print margin are in View > Grid and Guides….
- "Flip local X/Y" and "Visible" checkboxes: the flip buttons already toggle
  the flags.

## Implementation notes

- `design_tools.TOOL_GROUPS` is shared with GTK `editor.py`. Qt uses
  `WORKSPACE_TOOL_GROUPS`/`WORKSPACE_TOOLS`/`WORKSPACE_KEYS`; delete the old
  set when GTK is ported.
- Verify: `./run-tests.sh`, offscreen screenshots at 1440×900 and 720×520,
  then `/impeccable critique` again.

## Template tracing (added 2026-11-20)

From Silhouette School's "How to Make a Template in Silhouette Studio"
(2023-01-17): photograph a blank, bring it in at real size, outline it with
curve points, adjust points, simplify, paste into a new page.

| Step in Studio | Here |
|---|---|
| PixScan import at true size | Not yet. Place Image + Measure (I): drag a known distance, Scale Selection to Length…. PixScan mat calibration is to be recovered from Studio itself, not guessed. |
| Draw a Curve Shape | Curve tool (C): Catmull-Rom through each click, closed, flattened to a Path. |
| Edit points, add points | Point Edit (A): drag points; double-click the outline to add one. |
| Simplify | Object > Simplify (Ctrl+J), unchanged. |
| Copy, new work area, paste | Edit > Cut/Copy/Paste (Ctrl+X/C/V), in place, one clipboard across documents. |
