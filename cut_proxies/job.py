"""One place that turns a set of choices into a checked, ready-to-cut job.

Both front ends go through here, so the terminal and the window cannot drift
apart on what a given set of options means - which is the mistake
`print-proxies` describes making with its old PyQt window.

The ordering is deliberate:

1. lay out the grid,
2. place the marks, and check nothing collides with them,
3. build the cut paths,
4. build the byte stream,
5. decode the stream and measure it back,

and only then is anything allowed near the cutter.  `Job.blocked` is the one
thing a caller has to look at: if it is non-empty, refuse.
"""

from __future__ import annotations

import dataclasses
import pathlib
import math

from . import config, geometry, graphtec, layout_presets, presets, regmarks, template, toolpath, verify


@dataclasses.dataclass
class Job:
    grid: geometry.Grid
    marks: regmarks.Marks
    paths: tuple
    settings: graphtec.CutSettings
    device: graphtec.Device
    preset: presets.Preset
    mode: str
    stream: bytes
    rows: list
    ok: bool
    collisions: list[str]
    off_sheet: list[str]
    warnings: list[str]
    # Probes the verifier failed to catch. Non-empty means the check table
    # cannot be trusted, whatever it reported.
    liveness_failures: list[str] = dataclasses.field(default_factory=list)
    skip: tuple[int, ...] = ()
    # The standing blade-vs-sensor correction this job was built with.
    offset_mm: tuple[float, float] = (0.0, 0.0)
    # Cards the optical sensor may mistake for a mark. Not blocking: the
    # answer is to cover them for the scan, which is what the Card Maker
    # guide tells you to do.
    mask_hints: list[dict] = dataclasses.field(default_factory=list)
    # Preserve the exact write/read plan whose writes were verified. Front
    # ends must not reconstruct a different plan from current widget state.
    steps: tuple = ()
    registration: bool = True
    auto_search: bool = True
    mat: str = "auto"
    end_position: str = "below"
    end_feed_mm: float = 0.0

    # ---- reporting ---------------------------------------------------
    @property
    def blocked(self) -> list[str]:
        """Everything that must stop this job being sent."""
        out = [f"verification is not trustworthy - {m}"
               for m in self.liveness_failures]
        out += list(self.collisions) + list(self.off_sheet)
        out += [f"{r.label}: wanted {r.want}, got {r.got}"
                for r in self.rows if not r.ok]
        return out

    @property
    def cut_length_mm(self) -> float:
        if not self.settings.custom_toolpath:
            return template.total_length_mm(self.paths) * self.settings.passes
        origin = self.marks.origin_mm if self.registration else (0.0, 0.0)
        start = tuple(axis - offset for axis, offset in zip(origin, self.offset_mm))
        return sum(math.dist(a, b) for _, points in toolpath.schedule(self.paths, self.settings, start)
                   for a, b in zip(points, points[1:]))

    def registration_steps(self) -> tuple:
        """Use the verified plan, stopping at the sensor's reply before paths."""
        for i, step in enumerate(self.steps):
            if step.kind == "expect" and step.expect == graphtec.RESP_REGMARK_OK:
                return self.steps[:i+1]
        return ()

    def registration_writes(self) -> tuple[str, ...]:
        """Display the USB/BLE command blocks that precede the scan reply."""
        return tuple(" ".join(part.decode("ascii") if all(32 <= byte < 127 for byte in part)
                              else part.hex(" ")
                              for part in step.data.split(graphtec.CMD_ETX) if part)
                     for step in self.registration_steps() if step.kind == "write")

    def registration_notes(self, report=None, near_marks=None) -> list[str]:
        """PDF and layout observations available after a hardware scan fails."""
        notes = []
        if report is not None:
            problems = report.problems(self.marks.sheet_w_mm, self.marks.sheet_h_mm)
            if problems:
                notes.append("PDF check: " + "\n".join(problems))
            else:
                names = ", ".join(mark.name for mark in report.found)
                notes.append(f"PDF check found all expected marks: {names}.")
        if near_marks is None:
            corners = {hint["mark"] for hint in self.mask_hints}
            near_marks = [f"{corner} {'square' if corner == 'top-left' and self.marks.square else 'L'}"
                          for corner in sorted(corners)]
        if near_marks:
            notes.append("Check for nearby artwork at " + ", ".join(near_marks)
                         + " (mat view).")
        return notes

    def summary(self) -> list[tuple[str, str]]:
        mk = self.marks
        s = self.settings
        tool = [f"holder {s.holder}"]
        if self.device.cameo3_on:
            tool.append("head lift on" if s.lift_head or s.sharpen_corners else "head lift off")
            if s.sharpen_corners:
                tool.append(f"Line Segment Overcut {s.corner_start_mm:g}/{s.corner_end_mm:g} mm")
        if self.device.detects_tools:
            tool.append("automatic acceleration" if s.acceleration is None
                        else f"acceleration {s.acceleration}")
        if s.track_enhancing:
            tool.append("Track Enhancing")
        if s.custom_toolpath:
            tool.extend((toolpath.PATH_ORDERS[s.path_order], toolpath.PASS_ORDERS[s.pass_order]))
            if s.interior_first or s.delicate_media:
                tool.append("interior contours first")
            if s.delicate_media:
                tool.append("delicate media path")
            if s.alternate_direction:
                tool.append("alternating direction")
            if s.overcut_mm:
                tool.append(f"{s.overcut_mm:g} mm overcut")

        weed_count = sum(toolpath.is_weed_path(path) for path in self.paths)
        return [
            ("Cutter", f"{self.device.name} "
                       f"({self.device.width_mm:g} mm cuttable)"),
            ("Sheet", f"{self.grid.sheet_w_mm:g} x {self.grid.sheet_h_mm:g} mm "
                      f"{self.grid.sheet_name} "
                      f"{'portrait' if self.grid.portrait else 'landscape'}"),
            ("Grid", self.grid.describe()),
            ("Source", "imported file" if self.grid.engine == "imported"
             else "generated layout"),
            ("Card", f"{self.grid.card.width_mm:g} x "
                     f"{self.grid.card.height_mm:g} mm, "
                     f"{self.grid.card.radius_mm:g} mm corners"),
            ("Marks", mk.describe()),
            ("Cutting mat", graphtec.MAT_OPTIONS[self.mat]),
            ("End position", graphtec.END_POSITIONS[self.end_position]
             + (f" +{self.end_feed_mm:g} mm" if self.end_feed_mm else "")),
            *([("Weed Settings", f"{weed_count} cut paths")] if weed_count else []),
            ("Origin shift", f"cut coordinates are measured from "
                             f"({mk.origin_mm[0]:g}, {mk.origin_mm[1]:g}) mm"
             if self.registration else "registration off; sheet coordinates"),
            ("Preset", f"{self.preset.name} [{self.preset.origin}]"),
            ("Blade", f"force {self.settings.force}, speed "
                      f"{self.settings.speed}, {self.settings.passes} "
                      f"pass(es), blade {self.settings.blade_diameter_mm:g} mm"
                      + (f", depth {self.settings.depth}"
                         if self.settings.depth is not None else "")),
            ("Tool setup", ", ".join(tool)),
            ("Cut mode", self.mode),
            ("Calibration", "none"
             if tuple(self.offset_mm) == (0.0, 0.0)
             else f"every cut shifted by {self.offset_mm[0]:+g}, "
                  f"{self.offset_mm[1]:+g} mm"),
            ("Paths", f"{len(self.paths)} path(s), "
                      f"{self.cut_length_mm / 1000:.2f} m of travel"
                      + (f", {len(self.skip)} slot(s) skipped"
                         if self.skip else "")),
            ("Stream", f"{len(self.stream):,} bytes"),
        ]


_USE_PRESET_DEPTH = object()


def build(sheet: str = config.DEFAULT_SHEET,
          card: str = config.DEFAULT_CARD,
          portrait: bool | None = None,
          engine: str = "scm",
          preset_name: str = config.DEFAULT_PRESET,
          cutter: str = "Silhouette Cameo 4",
          mode: str = template.ROUNDED,
          radius_mm: float | None = None,
          mark_inset_mm: float | None = None,
          mark_length_mm: float | None = None,
          mark_thickness_mm: float | None = None,
          pattern: str = regmarks.THREE,
          passes: int | None = None,
          force: int | None = None,
          speed: int | None = None,
          depth: int | None | object = _USE_PRESET_DEPTH,
          bleed_mm: float = 0.0,
          proxypress: geometry.ProxyPressSettings | None = None,
          registration: bool = True,
          auto_search: bool = True,
          skip: tuple[int, ...] = (),
          offset_mm: tuple[float, float] | None = None,
          imported=None, blade_options=None, mat: str = "auto",
          end_position: str = "below", end_feed_mm: float = 0.0,
          weed_border_mm: float = 0.0, weed_lines: str = "none") -> Job:
    """Build and check a job.  Raises only on things that cannot be reported."""
    device = graphtec.device_by_name(cutter)

    # 1. the grid
    if engine == "proxypress":
        # The eight-up sheet, which is what this workflow prints. Proxy
        # Press's own b-cameo preset fits four, because a 16 mm margin
        # keeps the grid clear of the marks by brute force; `cut-proxies
        # proxypress` gives the settings for eight on the same paper.
        settings_pp = proxypress or config.PROXYPRESS_8UP
        grid = geometry.proxypress_layout(
            sheet, portrait if portrait is not None else False, settings_pp)
        marks_cfg = config.PROXYPRESS_8UP_MARKS
        inset = (mark_inset_mm if mark_inset_mm is not None
                 else float(marks_cfg["regInset"]))
        # 0 means auto in Proxy Press and None means auto here.
        length = mark_length_mm
        thickness = (mark_thickness_mm if mark_thickness_mm is not None
                     else float(marks_cfg["regThickness"]))
        # Both layout engines use the same standard registration patterns.
    else:
        inset = (mark_inset_mm if mark_inset_mm is not None
                 else config.DEFAULT_MARK_INSET_MM)
        grid = geometry.scm_layout(sheet, card, portrait, inset)
        length = mark_length_mm
        thickness = (mark_thickness_mm if mark_thickness_mm is not None
                     else config.DEFAULT_MARK_THICKNESS_MM)

    # Stock landscape jobs and bare cut files share the printed paper preset.
    if (length is None and not grid.portrait and card == "standard"
            and inset == 10 and proxypress is None and pattern == regmarks.THREE):
        preset = layout_presets.BUILTIN.get(f"mtg-cameo-{sheet.lower()}")
        if preset is not None:
            length = preset.arm_mm

    # 2. the marks
    mk = regmarks.for_grid(grid, inset_mm=inset, length_mm=length,
                           thickness_mm=thickness, pattern=pattern)
    collisions = regmarks.collisions(grid, mk, bleed_mm=bleed_mm)
    off = regmarks.off_sheet(grid, mk)

    # 3. the paths, minus any slot deliberately left empty
    #
    # An imported file replaces the generated grid entirely. Its paths get
    # a synthetic Grid built from their bounding boxes, so every existing
    # check - size, position, in-bounds, the liveness probes - applies to
    # imported geometry exactly as it does to a grid we laid out. The
    # alternative was a second, weaker verification path for imports, and
    # the import is the case where the geometry is least trusted.
    if imported is not None:
        grid = _grid_from_paths(imported, grid)
        mk = regmarks.for_grid(grid, inset_mm=inset, length_mm=length,
                               thickness_mm=thickness, pattern=pattern) \
            if engine == "scm" else mk
        inkscape_marks = imported.inkscape_marks if registration else None
        if inkscape_marks:
            mk = regmarks.marks(grid.sheet_w_mm, grid.sheet_h_mm,
                                inkscape_marks.inset_mm, inkscape_marks.length_mm,
                                pattern=inkscape_marks.pattern, window_mm=inkscape_marks.window_mm)
            # The plugin prints thinner strokes than the SCM presets.
            thickness = inkscape_marks.thickness_mm
            mk = dataclasses.replace(mk, thickness_mm=thickness,
                                     ells=tuple(dataclasses.replace(ell, thickness=thickness)
                                                for ell in mk.ells))
        collisions = regmarks.collisions(grid, mk, bleed_mm=bleed_mm)
        off = regmarks.off_sheet(grid, mk)
        off += imported_mode_errors(imported, mode)
        if registration:
            off += imported_page_errors(imported, grid)
            off += inkscape_registration_errors(imported, device)
    skip = tuple(sorted({i for i in skip if 0 <= i < grid.count}))
    if radius_mm is not None and imported is None:
        grid = dataclasses.replace(
            grid, card=dataclasses.replace(grid.card, radius_mm=radius_mm))
    if imported is not None:
        paths = tuple(p for i, p in enumerate(imported.paths)
                      if i not in set(skip))
    else:
        paths = template.cut_paths(grid, mode, radius_mm, skip=skip)
        if weed_border_mm or weed_lines != "none":
            raise ValueError("Open cut artwork or review a Design to add a Weed Border.")
    paths += toolpath.weed_paths(paths, weed_border_mm, weed_lines)
    cut_cards = tuple(c for i, c in enumerate(grid.cards) if i not in set(skip))
    hints = [h for h in regmarks.mask_hints(grid, mk)
             if h["index"] not in set(skip)]

    off += cutting_mat_errors(device, mat, grid.sheet_w_mm, grid.sheet_h_mm)

    # 4. the settings and the stream
    preset = presets.get(preset_name)
    cut = dataclasses.replace(preset.cut_settings(), **(blade_options or {}))
    if passes is not None:
        cut.passes = passes
    if force is not None:
        cut.force = force
    if speed is not None:
        cut.speed = speed
    if depth is not _USE_PRESET_DEPTH:
        cut.depth = depth
    warnings = cut.warnings(device)
    cut = cut.clamped(device)

    # No explicit offset means "use whatever this machine was calibrated to".
    offset = config.cut_offset() if offset_mm is None else offset_mm
    window = regmarks.scan_window(mk)
    job = graphtec.CutJob(
        device, cut, origin_mm=mk.origin_mm,
        reg_width_mm=mk.reg_width_mm, reg_length_mm=mk.reg_length_mm,
        registration=registration, auto_search=auto_search,
        quad=window["quad"], offset_mm=offset,
        mark_shape_mm=(mk.length_mm, mk.thickness_mm),
        sheet_size_mm=(grid.sheet_w_mm, grid.sheet_h_mm),
        mat=mat, end_position=end_position, end_feed_mm=end_feed_mm)
    steps = tuple(job.plan(paths))
    stream = b''.join(step.data for step in steps if step.kind == 'write')

    # 5. take it apart again
    verify_mode = mode if imported is None else "rounded"
    check_options = dict(cards=cut_cards, offset_mm=offset, registration=registration,
                         auto_search=auto_search, mat=mat, end_position=end_position, end_feed_mm=end_feed_mm)
    if imported is not None:
        check_options["expected_paths"] = paths
    elif cut.custom_toolpath:
        check_options["source_paths"] = paths
    ok, rows = verify.expectations_met(stream, grid, mk, cut, device, verify_mode,
                                       **check_options)
    # ...and prove, in this same run, that those checks would have noticed if
    # the stream were wrong. A verifier that always says yes prints exactly
    # the table above.
    liveness = verify.liveness_failures(
        stream, grid, mk, cut, device, verify_mode, **check_options)

    if grid.sheet_w_mm > device.width_mm:
        warnings.append(
            f"The sheet is {grid.sheet_w_mm:g} mm wide but the "
            f"{device.name} can only cut {device.width_mm:g} mm.")
    if imported is None and mk.length_mm > grid.max_mark_length_mm and engine == "scm":
        warnings.append(
            f"Mark arms are {mk.length_mm:g} mm but this layout only has "
            f"{grid.max_mark_length_mm:g} mm of clearance.")

    return Job(grid=grid, marks=mk, paths=paths, settings=cut, device=device,
               preset=preset, mode=mode, stream=stream, rows=rows, ok=ok,
               collisions=collisions, off_sheet=off, warnings=warnings,
               skip=skip, mask_hints=hints, offset_mm=offset,
               liveness_failures=liveness, steps=steps, registration=registration,
               auto_search=auto_search, mat=mat, end_position=end_position, end_feed_mm=end_feed_mm)


def cutting_mat_errors(device, mat: str, sheet_w: float, sheet_h: float) -> list[str]:
    if mat == "auto" or mat not in graphtec.MAT_OPTIONS:
        return []
    if not device.cameo3_on:
        return [f"{device.name} does not support software mat selection. Choose Automatic."]
    if mat in graphtec.MATS:
        _, width, height = graphtec.MATS[mat]
        if sheet_w > width or sheet_h > height:
            return [f"The {sheet_w:g} × {sheet_h:g} mm sheet does not fit "
                    f"the {graphtec.MAT_OPTIONS[mat]} mat."]
    return []


def imported_mode_errors(imported, mode: str) -> list[str]:
    """Imported artwork defines explicit cut paths; cross-cuts cannot tile it."""
    if imported is not None and mode == "cross":
        return ["mode='cross' cannot be used with imported artwork; "
                "imported files define their own cut paths"]
    return []


def inkscape_registration_errors(imported, device) -> list[str]:
    if imported.inkscape_mark_error:
        return [imported.inkscape_mark_error]
    marks = imported.inkscape_marks
    if marks and marks.pattern == regmarks.FOUR and not device.quad_regmarks:
        return ["This cutter requires the Inkscape plugin's standard three-mark pattern."]
    return []


def imported_page_errors(imported, grid) -> list[str]:
    """Registered paths and marks must share the declared page coordinates."""
    if imported.page_size_mm is None:
        return []
    w, h = imported.page_size_mm
    if abs(w - grid.sheet_w_mm) <= 0.1 and abs(h - grid.sheet_h_mm) <= 0.1:
        return []
    return [f"Imported page is {w:g} x {h:g} mm; selected sheet is "
            f"{grid.sheet_w_mm:g} x {grid.sheet_h_mm:g} mm. "
            "Select the matching sheet size and orientation, or export again "
            "for the selected sheet."]


def _grid_from_paths(imported, like: geometry.Grid) -> geometry.Grid:
    """A Grid whose cards are the bounding boxes of imported paths.

    Not a real layout - there are no rows and columns in an arbitrary SVG -
    but it is the shape the rest of the pipeline checks against, so an
    imported file gets the same verification as a generated one.
    """
    boxes = []
    for p in imported.paths:
        xs = [x for x, _ in p.points]
        ys = [y for _, y in p.points]
        boxes.append(geometry.Rect(min(xs), min(ys),
                                   max(xs) - min(xs), max(ys) - min(ys)))
    face = geometry.Card("imported", boxes[0].w if boxes else 0.0,
                         boxes[0].h if boxes else 0.0, 0.0)
    return dataclasses.replace(
        like, engine="imported", card=face, cols=len(boxes), rows=1,
        cards=tuple(boxes), max_mark_length_mm=like.max_mark_length_mm)


def write_templates(job: Job, stem: str, folder=None,
                    proof: bool = True) -> dict[str, pathlib.Path]:
    """Write the SVG, the DXF and (optionally) a proof SVG."""
    folder = pathlib.Path(folder) if folder else config.template_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[str, pathlib.Path] = {}

    svg = folder / f"{stem}.svg"
    template.write_svg(svg, job.grid, job.paths, job.mode,
                       job.grid.card.radius_mm, job.marks, proof=False,
                       note=f"preset {job.preset.name}", skip=job.skip)
    out["svg"] = svg

    dxf = folder / f"{stem}.dxf"
    template.write_dxf(dxf, job.grid, job.mode, job.grid.card.radius_mm,
                       skip=job.skip,
                       paths=job.paths if job.grid.engine == 'imported' else None)
    out["dxf"] = dxf

    if proof:
        proof_svg = folder / f"{stem}-proof.svg"
        template.write_svg(proof_svg, job.grid, job.paths, job.mode,
                           job.grid.card.radius_mm, job.marks, proof=True,
                           note="PROOF - do not cut; marks are printed, "
                                "not cut", skip=job.skip)
        out["proof"] = proof_svg
    return out
