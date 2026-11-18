# Build a full desktop design workspace for the Cameo 4

## Release goal

Make **Design → Print → Send** the main workflow in Silhouette Control Center. A user should be able to design or import a sticker sheet, edit its artwork and contours, print it with registration marks, and send a verified job to a Cameo 4 over USB. Keep the existing card layouts available as starting templates. Build the workspace in the app’s own GTK design.

## Design workspace

- Replace the modal shape editor with a full-window canvas, tool palette, object and layer list, and properties inspector. Support zoom, pan, rulers, grid snapping, canvas handles, multi-select, group and ungroup, ordering, alignment, duplicate, and undo/redo. A completed drag is one undo step.
- Add direct drawing for rectangles, rounded rectangles, ellipses, polygons, lines, and Bézier paths. Allow node and handle editing, rotation, mirroring, and exact position and dimensions in millimetres. Add editable text with installed fonts and save its resolved outlines so reopening a project preserves its geometry.
- Import SVG and DXF as editable path objects, preserving the source scale and showing any units assumption before placement. Import PNG, JPEG, and self-contained SVG as print artwork. A cut import that would silently omit unsupported geometry must stop and identify what needs converting.
- Save `.scc-template` v2 projects with page setup, embedded artwork, editable objects, layers, and print and cut assignments. Open v1 projects as editable shapes on their saved page. Warn before discarding unsaved changes. Export selected cut geometry as SVG or DXF.

## Print and Send

- Give each object a print toggle and an action of **Cut**, **Score**, or **None**. Hidden objects are excluded from both outputs. Show registration marks and their clearance zones on the canvas. Export a true-size PDF containing visible printable artwork and registration marks, and use that same PDF with the system Print dialog at 100% scale.
- Add a dedicated Send workspace with device status, material preset, action picker, and exact cut preview. Send one action per job with one verified settings profile. For a sheet with both actions, run Score and Cut as separate registered jobs.
- Build each send through the existing `job.build` and `Job.blocked` path. Run the verifier and its liveness probes on the selected paths for every pass. Design, page, action, or material changes invalidate the checked job. Confirm the current job, require a ready cutter, and wait for a successful registration reply before sending cutting motion.

## Acceptance and release

- Test v1 migration, v2 round trips, embedded artwork, undo/redo, transforms, imported scale, and text outline stability. Verify that the printed PDF has the correct page dimensions and marks and that only the selected action reaches the checked command stream. Exercise failure cases for blocked geometry, stale confirmation, registration failure, and USB disconnection.
- Run `./run-tests.sh` and the assertion audit. Smoke-test the complete workflow in the GTK app and in both x86-64 packages. Rebuild the AppImage and Flatpak, and ship the Silverblue install script and checksums alongside them.
- Qualify the hardware workflow with a supervised plain-paper sticker or card proof over Cameo 4 USB. Verify registration and measure alignment on spacing across the sheet before claiming physical Print & Cut support. Automatic raster tracing, offset and weld, mixed-action sends, and general `.studio3` import are follow-on releases.
