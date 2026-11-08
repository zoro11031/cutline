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

from . import config, geometry, graphtec, presets, regmarks, template, verify


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
        return template.total_length_mm(self.paths) * self.settings.passes

    def summary(self) -> list[tuple[str, str]]:
        mk = self.marks
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
            ("Origin shift", f"cut coordinates are measured from "
                             f"({mk.origin_mm[0]:g}, {mk.origin_mm[1]:g}) mm"),
            ("Preset", f"{self.preset.name} [{self.preset.origin}]"),
            ("Blade", f"force {self.settings.force}, speed "
                      f"{self.settings.speed}, {self.settings.passes} "
                      f"pass(es), blade {self.settings.blade_diameter_mm:g} mm"
                      + (f", depth {self.settings.depth}"
                         if self.settings.depth is not None else "")),
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
          depth: int | None = None,
          bleed_mm: float = 0.0,
          proxypress: geometry.ProxyPressSettings | None = None,
          registration: bool = True,
          auto_search: bool = True,
          skip: tuple[int, ...] = (),
          offset_mm: tuple[float, float] | None = None,
          imported=None) -> Job:
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
        if length is None and marks_cfg["regLen"]:
            length = float(marks_cfg["regLen"])
        thickness = (mark_thickness_mm if mark_thickness_mm is not None
                     else float(marks_cfg["regThickness"]))
        # Proxy Press used to draw a fourth mark in the bottom-right, which
        # belongs to neither Silhouette pattern; it now has a pattern
        # setting and this workflow sets it to three. Pass
        # `pattern="proxypress"` explicitly for a sheet exported before
        # that change.
    else:
        inset = (mark_inset_mm if mark_inset_mm is not None
                 else config.DEFAULT_MARK_INSET_MM)
        grid = geometry.scm_layout(sheet, card, portrait, inset)
        length = mark_length_mm
        thickness = (mark_thickness_mm if mark_thickness_mm is not None
                     else config.DEFAULT_MARK_THICKNESS_MM)

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
        collisions = regmarks.collisions(grid, mk, bleed_mm=bleed_mm)
        off = regmarks.off_sheet(grid, mk)
    skip = tuple(sorted({i for i in skip if 0 <= i < grid.count}))
    if imported is not None:
        paths = tuple(p for i, p in enumerate(imported.paths)
                      if i not in set(skip))
    else:
        paths = template.cut_paths(grid, mode, radius_mm, skip=skip)
    cut_cards = tuple(c for i, c in enumerate(grid.cards) if i not in set(skip))
    hints = [h for h in regmarks.mask_hints(grid, mk)
             if h["index"] not in set(skip)]

    # 4. the settings and the stream
    preset = presets.get(preset_name)
    cut = preset.cut_settings()
    if passes is not None:
        cut.passes = passes
    if force is not None:
        cut.force = force
    if speed is not None:
        cut.speed = speed
    if depth is not None:
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
        quad=window["quad"], offset_mm=offset)
    stream = job.stream(paths)

    # 5. take it apart again
    ok, rows = verify.expectations_met(stream, grid, mk, cut, device, mode,
                                       cards=cut_cards, offset_mm=offset,
                                       registration=registration)
    # ...and prove, in this same run, that those checks would have noticed if
    # the stream were wrong. A verifier that always says yes prints exactly
    # the table above.
    liveness = verify.liveness_failures(
        stream, grid, mk, cut, device, mode, cards=cut_cards,
        offset_mm=offset, registration=registration)

    if grid.sheet_w_mm > device.width_mm:
        warnings.append(
            f"The sheet is {grid.sheet_w_mm:g} mm wide but the "
            f"{device.name} can only cut {device.width_mm:g} mm.")
    if mk.length_mm > grid.max_mark_length_mm and engine == "scm":
        warnings.append(
            f"Mark arms are {mk.length_mm:g} mm but this layout only has "
            f"{grid.max_mark_length_mm:g} mm of clearance.")

    return Job(grid=grid, marks=mk, paths=paths, settings=cut, device=device,
               preset=preset, mode=mode, stream=stream, rows=rows, ok=ok,
               collisions=collisions, off_sheet=off, warnings=warnings,
               skip=skip, mask_hints=hints, offset_mm=offset,
               liveness_failures=liveness)


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
                       note=f"preset {job.preset.name}")
    out["svg"] = svg

    dxf = folder / f"{stem}.dxf"
    template.write_dxf(dxf, job.grid, job.mode, job.grid.card.radius_mm)
    out["dxf"] = dxf

    if proof:
        proof_svg = folder / f"{stem}-proof.svg"
        template.write_svg(proof_svg, job.grid, job.paths, job.mode,
                           job.grid.card.radius_mm, job.marks, proof=True,
                           note="PROOF - do not cut; marks are printed, "
                                "not cut")
        out["proof"] = proof_svg
    return out
